import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import mock


# Importing app.py runs its existing idempotent init_db(). Point it at an
# isolated temporary SQLite tree before import so tests can never touch the
# configured PostgreSQL service or the developer's normal local database.
TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-auto-planning-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "auto-planning-test-only")
# This suite exercises the historical persistent flow (draft tables,
# shift_locks, bulk-accept, apply, cancel). ``init_db`` only creates
# the shift_locks / shift_proposal_batches / shift_proposals tables
# in ``write_enabled`` mode, so set it here BEFORE the import that
# triggers ``init_db``.
os.environ["AUTO_PLANNING_MODE"] = "write_enabled"

import app  # noqa: E402

# When this module is imported LATER in a ``unittest discover`` run,
# another test module may have already imported ``app`` under an
# analysis_only / missing mode — ``init_db`` then skipped the
# auto-planning DDL. Force the mode here and re-run ``init_db`` so
# ``shift_locks``/``shift_proposal_batches``/``shift_proposals`` and
# their indexes are present on whichever storage the shared app
# instance ended up using.
app.app.config["AUTO_PLANNING_MODE"] = "write_enabled"
try:
    app.init_db()
except Exception:  # pragma: no cover — defensive, surfaces in tests
    pass


def tearDownModule():
    shutil.rmtree(TEST_STORAGE, ignore_errors=True)
    try:
        os.rmdir(TEST_TEMP_ROOT)
    except OSError:
        pass


def shift(row_id, workers, client, day, hours="08:00-10:00"):
    return (row_id, workers, client, day, hours, "done")


class RecurrenceEngineTests(unittest.TestCase):
    def test_postgres_translation_contract_for_auto_planning_sql(self):
        class RecordingCursor:
            def execute(self, _query, _params=()):
                return None

        cursor = app._PgCursor(RecordingCursor())
        self.assertEqual(
            cursor._translate("SELECT pg_advisory_xact_lock(?)"),
            "SELECT pg_advisory_xact_lock(%s)",
        )
        self.assertEqual(
            cursor._translate("LOCK TABLE shifts IN EXCLUSIVE MODE"),
            "LOCK TABLE shifts IN EXCLUSIVE MODE",
        )
        self.assertIn(
            "SERIAL PRIMARY KEY",
            cursor._translate(
                "CREATE TABLE sample (id INTEGER PRIMARY KEY AUTOINCREMENT)"
            ),
        )

    def test_lookback_bounds_are_date_only_across_dst_changes(self):
        self.assertEqual(
            app.ap_lookback_bounds("2026-03-30", 2),
            (date(2026, 3, 16), date(2026, 3, 29)),
        )
        self.assertEqual(
            app.ap_lookback_bounds("2026-10-26", 2),
            (date(2026, 10, 12), date(2026, 10, 25)),
        )

    def test_schweig_pletgen_weekly_visit_with_time_drift(self):
        """Real-world bug: ONE weekly Wednesday visit for
        Schweig-Pletgen Claudine with Edita+Samela, 2 hours, with a
        historical arrival drift between 10:15 and 10:30. The engine
        must infer:
          - a single proposal for 2026-11-04;
          - recurrence_period = 1 (WEEKLY, not bi-weekly);
          - confidence higher than what either sub-group alone
            would have yielded (full-history view);
          - occurrences = total of merged dates;
          - source_last_seen = latest observation across both.
        """
        # Perfectly alternating weeks over 8 weeks: four at 10:15-12:15
        # and four at 10:30-12:30 → combined, a strict weekly series.
        rows_a = [
            shift(i, "Edita, Samela", "Schweig-Pletgen Claudine",
                  day, "10:15-12:15")
            for i, day in enumerate([
                "2026-09-09", "2026-09-23", "2026-10-07", "2026-10-21",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Edita, Samela", "Schweig-Pletgen Claudine",
                  day, "10:30-12:30")
            for i, day in enumerate([
                "2026-09-16", "2026-09-30", "2026-10-14", "2026-10-28",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b),
            "2026-11-02", lookback_weeks=8,
        )
        self.assertEqual(
            len(proposals), 1,
            f"expected single proposal after merge, got {proposals}",
        )
        p = proposals[0]
        self.assertEqual(p["client"], "Schweig-Pletgen Claudine")
        self.assertEqual(p["target_date"], "2026-11-04")
        # Weekly cadence — proven by the merged date list.
        self.assertEqual(p["recurrence_period"], 1)
        # All 8 observations collapsed into one proposal.
        self.assertEqual(p["occurrences"], 8)
        self.assertEqual(p["source_last_seen"], "2026-10-28")
        # Duration preserved exactly (no synthetic time).
        self.assertEqual(p["duration_hours"], 2.0)
        # Time is literally one of the observed times — never a
        # synthesised midpoint.
        self.assertIn(p["time"], {"10:15-12:15", "10:30-12:30"})
        # Confidence on the merged series: 8 weekly occurrences →
        # the ratio ``8 / lookback_weeks`` already saturates at 1.0.
        self.assertEqual(p["confidence"], 1.0)

    def test_near_duplicate_merge_is_order_independent(self):
        """Shuffling the input historical rows must NEVER change
        the proposal set."""
        rows = [
            shift(i, "A, B", "Client", day, hours)
            for i, (day, hours) in enumerate([
                ("2026-09-09", "10:15-12:15"),
                ("2026-09-16", "10:30-12:30"),
                ("2026-09-23", "10:15-12:15"),
                ("2026-09-30", "10:30-12:30"),
            ], 1)
        ]
        baseline = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8,
        )
        import random
        for seed in range(5):
            shuffled = list(rows)
            random.Random(seed).shuffle(shuffled)
            got = app.ap_project(
                app.ap_group_by_key(shuffled),
                "2026-11-02", lookback_weeks=8,
            )
            self.assertEqual(got, baseline, f"ordering changed for seed {seed}")

    def test_merge_without_the_new_logic_fails_this_scenario(self):
        """Guard-rail: with the new merger NEUTRALISED (pass-through),
        the Schweig-Pletgen scenario drops back to the broken
        behaviour (two proposals or non-weekly cadence). Proves the
        test can only pass thanks to ``ap_merge_near_duplicate_groups``.
        """
        rows_a = [
            shift(i, "Edita, Samela", "Schweig-Pletgen Claudine",
                  day, "10:15-12:15")
            for i, day in enumerate([
                "2026-09-09", "2026-09-23", "2026-10-07", "2026-10-21",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Edita, Samela", "Schweig-Pletgen Claudine",
                  day, "10:30-12:30")
            for i, day in enumerate([
                "2026-09-16", "2026-09-30", "2026-10-14", "2026-10-28",
            ], 1)
        ]
        # Neutralise the merger with a no-op; the engine must then
        # fall back to the broken, pre-fix output.
        import unittest.mock as _mock
        with _mock.patch.object(
            app, "ap_merge_near_duplicate_groups", lambda g: g,
        ):
            proposals = app.ap_project(
                app.ap_group_by_key(rows_a + rows_b),
                "2026-11-02", lookback_weeks=8,
            )
        # Either two proposals OR a single proposal that is NOT the
        # correct (weekly, 8 occurrences) outcome.
        is_broken = (
            len(proposals) != 1
            or proposals[0].get("recurrence_period") != 1
            or proposals[0].get("occurrences") != 8
        )
        self.assertTrue(
            is_broken,
            "the test cannot distinguish fixed from broken behaviour: "
            f"{proposals}",
        )

    def test_dedup_keeps_non_overlapping_same_day_visits(self):
        """A legitimate morning + afternoon visit at the same client,
        same team, same weekday must NOT be merged — the time windows
        do not overlap."""
        morning_rows = [
            shift(i, "Aida", "ClientX", day, "08:00-09:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        afternoon_rows = [
            shift(100 + i, "Aida", "ClientX", day, "14:00-16:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(morning_rows + afternoon_rows),
            "2026-11-02", lookback_weeks=8,
        )
        times = sorted(p["time"] for p in proposals)
        self.assertEqual(times, ["08:00-09:00", "14:00-16:00"], proposals)

    def test_dedup_does_not_merge_different_clients(self):
        rows_a = [
            shift(i, "Aida", "ClientA", day, "10:00-12:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Aida", "ClientB", day, "10:15-12:15")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b),
            "2026-11-02", lookback_weeks=8,
        )
        clients = sorted(p["client"] for p in proposals)
        self.assertEqual(clients, ["ClientA", "ClientB"])

    def test_dedup_does_not_merge_different_teams(self):
        rows_a = [
            shift(i, "Aida", "ClientX", day, "10:00-12:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Bela", "ClientX", day, "10:15-12:15")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b),
            "2026-11-02", lookback_weeks=8,
        )
        workers = sorted(p["worker"] for p in proposals)
        self.assertEqual(workers, ["Aida", "Bela"])

    def test_three_group_cluster_rejects_transitive_date_collision(self):
        """P1 — Codex scenario:
          A : 10:00-11:00, weeks 1-4 (dates disjoint from B/C).
          B : 10:10-11:10, weeks 5-8.
          C : 10:20-11:20, weeks 5-8 (SAME dates as B).
        Comparing only to the first member (A) would admit all three
        into one cluster because A shares no dates with either B or
        C. The P1 extension refuses on the UNION of the cluster's
        dates: once B joins {A}, the running union covers weeks 5-8,
        so C's dates collide and C is kept separate.
        """
        rows_a = [
            shift(i, "Edita", "ClientABC", day, "10:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Edita", "ClientABC", day, "10:10-11:10")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_c = [
            shift(200 + i, "Edita", "ClientABC", day, "10:20-11:20")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b + rows_c),
            "2026-11-02", lookback_weeks=8,
        )
        times = sorted(p["time"] for p in proposals)
        # Not merged into a single row. The pre-fix behaviour was
        # exactly ONE proposal; after the P1 extension, C stays
        # distinct because its historical dates overlap the running
        # union of {A, B}. A and B themselves share NO dates and are
        # allowed to merge, so the final output has:
        #   - one proposal representing A+B (time ∈ {10:00, 10:10});
        #   - one proposal representing C   (time == 10:20-11:20).
        # The key invariant is that C — which historically occurred
        # at the same moment as B — is NOT silently absorbed.
        self.assertEqual(len(proposals), 2, proposals)
        self.assertIn("10:20-11:20", times, "C silently absorbed")
        other_times = {t for t in times if t != "10:20-11:20"}
        self.assertTrue(
            other_times.issubset({"10:00-11:00", "10:10-11:10"}),
            f"unexpected representative time for the A/B cluster: {times}",
        )
        # Both proposals target the same Monday 2026-11-02 and overlap
        # by 40 min — the batch overlap check must flag the conflict.
        issues = app.ap_check_overlap_batch(proposals)
        has_overlap = any(
            any(i.get("code") == "overlap_batch" for i in by_index)
            for by_index in issues.values()
        )
        self.assertTrue(
            has_overlap,
            f"expected overlap_batch issue on {proposals} → {issues}",
        )

    def test_three_group_cluster_is_order_independent(self):
        """The 3-group clustering output is identical for every input
        permutation of the historical rows."""
        rows_a = [
            shift(i, "Edita", "ClientABC", day, "10:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Edita", "ClientABC", day, "10:10-11:10")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_c = [
            shift(200 + i, "Edita", "ClientABC", day, "10:20-11:20")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        baseline = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b + rows_c),
            "2026-11-02", lookback_weeks=8,
        )
        import random
        for seed in range(5):
            shuffled = list(rows_a + rows_b + rows_c)
            random.Random(seed).shuffle(shuffled)
            got = app.ap_project(
                app.ap_group_by_key(shuffled),
                "2026-11-02", lookback_weeks=8,
            )
            self.assertEqual(
                got, baseline,
                f"3-group clustering changed with seed {seed}",
            )

    def test_three_group_cluster_without_union_check_would_fail(self):
        """Guard-rail: a reduced merger that compares the candidate
        only to the cluster's reference (no cluster-wide union) WOULD
        collapse A/B/C into a single proposal → proves the P1
        extension is the sole reason the production test above
        passes.
        """
        rows_a = [
            shift(i, "Edita", "ClientABC", day, "10:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Edita", "ClientABC", day, "10:10-11:10")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_c = [
            shift(200 + i, "Edita", "ClientABC", day, "10:20-11:20")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]

        def _reference_only_merge(groups):
            # Rebuilds the OLD (pre-fix) behaviour: compares every
            # candidate ONLY to the cluster's first member, never
            # to the running union. Returned shape is identical to
            # the real function so ap_project consumes it unchanged.
            if not groups:
                return groups
            bucketed = {}
            passthrough = {}
            for key, dates in groups.items():
                descriptor = app._ap_group_descriptor(key, dates)
                if descriptor is None:
                    passthrough[key] = dates
                    continue
                bucket_key = (descriptor["client"], descriptor["team"],
                              descriptor["weekday"])
                bucketed.setdefault(bucket_key, []).append(descriptor)
            merged = {}
            for members in bucketed.values():
                members.sort(
                    key=lambda d: (d["start_m"], d["end_m"],
                                   d["key"][3], d["key"][4]),
                )
                clusters = []
                for descriptor in members:
                    placed = False
                    for cluster in clusters:
                        if app._ap_can_merge(cluster[0], descriptor):
                            cluster.append(descriptor)
                            placed = True
                            break
                    if not placed:
                        clusters.append([descriptor])
                for cluster in clusters:
                    if len(cluster) == 1:
                        merged[cluster[0]["key"]] = list(cluster[0]["dates"])
                        continue
                    union_dates = sorted({d for m in cluster
                                          for d in m["dates"]})
                    start_lit, end_lit = app._ap_representative_time(cluster)
                    new_key = (
                        cluster[0]["client"], cluster[0]["team"],
                        cluster[0]["weekday"], start_lit, end_lit,
                    )
                    if new_key in merged:
                        merged[new_key] = sorted(
                            set(merged[new_key]) | set(union_dates)
                        )
                    else:
                        merged[new_key] = union_dates
            merged.update(passthrough)
            return merged

        import unittest.mock as _mock
        with _mock.patch.object(
            app, "ap_merge_near_duplicate_groups", _reference_only_merge,
        ):
            proposals = app.ap_project(
                app.ap_group_by_key(rows_a + rows_b + rows_c),
                "2026-11-02", lookback_weeks=8,
            )
        self.assertEqual(
            len(proposals), 1,
            "scenario cannot distinguish fixed from broken: "
            f"{proposals}",
        )

    def test_shared_date_disables_merge_and_surfaces_both(self):
        """P1: two series with Δstart = 29 min on the SAME historical
        dates represent two real visits that day — the merger must
        refuse and let both proposals surface so the batch overlap
        check can flag the conflict."""
        shared_dates = [
            "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
        ]
        rows_a = [
            shift(i, "Edita", "SharedClient", day, "10:00-11:00")
            for i, day in enumerate(shared_dates, 1)
        ]
        rows_b = [
            shift(100 + i, "Edita", "SharedClient", day, "10:29-11:29")
            for i, day in enumerate(shared_dates, 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b),
            "2026-11-02", lookback_weeks=8,
        )
        times = sorted(p["time"] for p in proposals)
        self.assertEqual(
            times, ["10:00-11:00", "10:29-11:29"],
            f"merge silently dropped a same-day distinct visit: "
            f"{proposals}",
        )

    def test_shared_date_without_merger_would_fail(self):
        """P1 guard-rail: with BOTH date-safety layers neutralised
        (reference-level check inside ``_ap_can_merge`` AND the
        cluster-wide union check inside
        ``ap_merge_near_duplicate_groups``) the shared-date series
        collapse into one proposal. Proves the test can only pass
        because the production code applies both layers.
        """
        shared_dates = [
            "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
        ]
        rows_a = [
            shift(i, "Edita", "SharedClient", day, "10:00-11:00")
            for i, day in enumerate(shared_dates, 1)
        ]
        rows_b = [
            shift(100 + i, "Edita", "SharedClient", day, "10:29-11:29")
            for i, day in enumerate(shared_dates, 1)
        ]

        def _merge_without_any_date_guard(groups):
            """A reduced merger that compares candidates to the
            cluster reference ONLY on time/duration — no reference
            date check, no cluster-wide union check."""
            if not groups:
                return groups
            bucketed = {}
            passthrough = {}
            for key, dates in groups.items():
                descriptor = app._ap_group_descriptor(key, dates)
                if descriptor is None:
                    passthrough[key] = dates
                    continue
                bk = (descriptor["client"], descriptor["team"],
                      descriptor["weekday"])
                bucketed.setdefault(bk, []).append(descriptor)

            def _no_date_check(ref, cand):
                return (
                    ref["client"] == cand["client"]
                    and ref["team"] == cand["team"]
                    and ref["weekday"] == cand["weekday"]
                    and abs(ref["start_m"] - cand["start_m"]) <= 30
                    and abs(ref["end_m"] - cand["end_m"]) <= 30
                )

            merged = {}
            for members in bucketed.values():
                members.sort(
                    key=lambda d: (d["start_m"], d["end_m"],
                                   d["key"][3], d["key"][4]),
                )
                clusters = []
                for descriptor in members:
                    placed = False
                    for cluster in clusters:
                        if _no_date_check(cluster[0], descriptor):
                            cluster.append(descriptor)
                            placed = True
                            break
                    if not placed:
                        clusters.append([descriptor])
                for cluster in clusters:
                    if len(cluster) == 1:
                        merged[cluster[0]["key"]] = list(cluster[0]["dates"])
                        continue
                    union_dates = sorted({d for m in cluster
                                          for d in m["dates"]})
                    start_lit, end_lit = app._ap_representative_time(cluster)
                    new_key = (
                        cluster[0]["client"], cluster[0]["team"],
                        cluster[0]["weekday"], start_lit, end_lit,
                    )
                    if new_key in merged:
                        merged[new_key] = sorted(
                            set(merged[new_key]) | set(union_dates)
                        )
                    else:
                        merged[new_key] = union_dates
            merged.update(passthrough)
            return merged

        import unittest.mock as _mock
        with _mock.patch.object(
            app, "ap_merge_near_duplicate_groups",
            _merge_without_any_date_guard,
        ):
            proposals = app.ap_project(
                app.ap_group_by_key(rows_a + rows_b),
                "2026-11-02", lookback_weeks=8,
            )
        self.assertEqual(
            len(proposals), 1,
            "the P1 scenario cannot distinguish fixed from broken: "
            f"{proposals}",
        )

    def test_shared_date_batch_overlap_check_is_still_raised(self):
        """After P1, both proposals survive — ``ap_check_overlap_batch``
        must then raise a visible ``overlap_batch`` warning on the
        batch so the operator sees the conflict."""
        shared_dates = [
            "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
        ]
        rows_a = [
            shift(i, "Edita", "SharedClient", day, "10:00-11:00")
            for i, day in enumerate(shared_dates, 1)
        ]
        rows_b = [
            shift(100 + i, "Edita", "SharedClient", day, "10:29-11:29")
            for i, day in enumerate(shared_dates, 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b),
            "2026-11-02", lookback_weeks=8,
        )
        issues = app.ap_check_overlap_batch(proposals)
        has_overlap = any(
            any(i.get("code") == "overlap_batch" for i in by_index)
            for by_index in issues.values()
        )
        self.assertTrue(
            has_overlap,
            f"expected overlap_batch issue on {proposals} → {issues}",
        )

    def test_30_vs_45_minutes_do_not_merge(self):
        """P2: a 30-min visit and a 45-min visit on disjoint weekly
        dates differ by 15 min absolute (ON the absolute boundary)
        BUT by 50% of the shorter window → the AND rule must refuse.
        """
        rows_30 = [
            shift(i, "Aida", "ClientD", day, "10:00-10:30")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            ], 1)
        ]
        rows_45 = [
            shift(100 + i, "Aida", "ClientD", day, "10:00-10:45")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_30 + rows_45),
            "2026-11-02", lookback_weeks=8,
        )
        durations = sorted(p["duration_hours"] for p in proposals)
        self.assertEqual(durations, [0.5, 0.75], proposals)

    def test_120_vs_150_minutes_do_not_merge(self):
        """P2: 2 h and 2 h 30 differ by 30 min absolute (above the
        15-min absolute boundary) BUT by only 25% relative (ON the
        boundary) → the AND rule must refuse on the absolute leg."""
        rows_120 = [
            shift(i, "Aida", "ClientE", day, "09:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
            ], 1)
        ]
        rows_150 = [
            shift(100 + i, "Aida", "ClientE", day, "09:00-11:30")
            for i, day in enumerate([
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_120 + rows_150),
            "2026-11-02", lookback_weeks=8,
        )
        durations = sorted(p["duration_hours"] for p in proposals)
        self.assertEqual(durations, [2.0, 2.5], proposals)

    def test_merge_time_window_boundaries(self):
        """P2 boundary: 29 / 30 / 31 minute start deltas with
        matching durations and disjoint dates — 29 and 30 merge,
        31 does not. Both sub-series are weekly on disjoint date
        ranges so both would be ``due`` on the target week without
        the merger.
        """
        # Series A: 4 consecutive Mondays, weekly, ending 2026-09-28.
        dates_a = [
            "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
        ]
        # Series B: 4 consecutive Mondays, weekly, ending 2026-10-26.
        dates_b = [
            "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
        ]
        rows_ref = [
            shift(i, "Aida", "ClientF", day, "10:00-11:00")
            for i, day in enumerate(dates_a, 1)
        ]

        def _run(delta_minutes, expected_single):
            minutes = (10 * 60) + delta_minutes
            hh = minutes // 60
            mm = minutes % 60
            time_str = f"{hh:02d}:{mm:02d}-{hh + 1:02d}:{mm:02d}"
            rows_other = [
                shift(100 + i, "Aida", "ClientF", day, time_str)
                for i, day in enumerate(dates_b, 1)
            ]
            proposals = app.ap_project(
                app.ap_group_by_key(rows_ref + rows_other),
                "2026-11-02", lookback_weeks=8,
            )
            if expected_single:
                self.assertEqual(
                    len(proposals), 1,
                    f"Δ={delta_minutes}min should have merged: "
                    f"{proposals}",
                )
            else:
                self.assertEqual(
                    len(proposals), 2,
                    f"Δ={delta_minutes}min should NOT merge: "
                    f"{proposals}",
                )

        _run(29, expected_single=True)
        _run(30, expected_single=True)
        _run(31, expected_single=False)

    def test_duration_mismatch_prevents_merge(self):
        """Codex scenario F: a 4-hour 08:00-12:00 visit and a 1-hour
        10:00-11:00 visit on the same day MUST NOT merge even though
        the shorter window sits entirely inside the longer one — the
        durations are too far apart to be the same real intervention.
        """
        rows_long = [
            shift(i, "Aida", "ClientX", day, "08:00-12:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_short = [
            shift(100 + i, "Aida", "ClientX", day, "10:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_long + rows_short),
            "2026-11-02", lookback_weeks=8,
        )
        times = sorted(p["time"] for p in proposals)
        self.assertEqual(
            times, ["08:00-12:00", "10:00-11:00"],
            f"long vs short visit incorrectly merged: {proposals}",
        )

    def test_merge_is_bounded_no_abusive_transitive_chain(self):
        """Codex scenario G: three distinct series with starts 10:00,
        10:25 and 10:50 — adjacent pairs are within the 30-min window
        but the extremes (10:00 vs 10:50) are 50 min apart. A
        transitive merge would chain all three; our reference-based
        clustering must keep the extremes separate.
        """
        rows_a = [
            shift(i, "Aida", "ClientY", day, "10:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Aida", "ClientY", day, "10:25-11:25")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_c = [
            shift(200 + i, "Aida", "ClientY", day, "10:50-11:50")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b + rows_c),
            "2026-11-02", lookback_weeks=8,
        )
        times = sorted(p["time"] for p in proposals)
        # 10:00 vs 10:50 is 50min apart → must stay a separate
        # cluster. The exact second merged cluster depends on the
        # reference comparison but at MINIMUM two proposals must
        # survive — abusive transitive chaining would produce one.
        self.assertGreaterEqual(
            len(proposals), 2,
            f"transitive chain merged everything: {proposals}",
        )
        # The extreme series must appear.
        self.assertIn("10:00-11:00", times)
        self.assertIn("10:50-11:50", times)

    def test_dedup_back_to_back_shifts_are_kept(self):
        """09:00-10:00 and 10:00-11:00 touch but do not overlap → kept."""
        rows_a = [
            shift(i, "Aida", "ClientX", day, "09:00-10:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        rows_b = [
            shift(100 + i, "Aida", "ClientX", day, "10:00-11:00")
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows_a + rows_b),
            "2026-11-02", lookback_weeks=8,
        )
        self.assertEqual(len(proposals), 2, proposals)

    def test_weekly_recurrence(self):
        rows = [
            shift(i, "Aida", "Maison A", day)
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        groups = app.ap_group_by_key(rows)
        proposals = app.ap_project(groups, "2026-11-02", lookback_weeks=8)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 1)
        self.assertEqual(proposals[0]["confidence"], 1.0)
        self.assertFalse(proposals[0]["requires_manual_validation"])

    def test_every_two_weeks(self):
        rows = [
            shift(i, "Aida", "Bureau B", day)
            for i, day in enumerate(
                ["2026-09-07", "2026-09-21", "2026-10-05", "2026-10-19"], 1
            )
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8
        )
        self.assertEqual(proposals[0]["recurrence_period"], 2)
        self.assertEqual(proposals[0]["confidence"], 1.0)

    def test_every_three_weeks(self):
        rows = [
            shift(i, "Aida", "Restaurant C", day)
            for i, day in enumerate(
                ["2026-09-07", "2026-09-28", "2026-10-19"], 1
            )
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-09", lookback_weeks=8
        )
        self.assertEqual(proposals[0]["recurrence_period"], 3)
        self.assertEqual(proposals[0]["target_date"], "2026-11-09")

    def test_every_four_weeks_monthly_cycle(self):
        rows = [
            shift(1, "Aida", "Appartement D", "2026-09-07"),
            shift(2, "Aida", "Appartement D", "2026-10-05"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8
        )
        self.assertEqual(proposals[0]["recurrence_period"], 4)
        self.assertEqual(proposals[0]["confidence"], 1.0)

    def test_calendar_monthly_cycle_handles_five_week_gap(self):
        rows = [
            shift(1, "Aida", "Monthly office", "2026-09-07"),
            shift(2, "Aida", "Monthly office", "2026-10-05"),
            shift(3, "Aida", "Monthly office", "2026-11-02"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-12-07", lookback_weeks=8
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 4)
        self.assertTrue(proposals[0]["calendar_monthly"])

    def test_missing_occurrence_keeps_cadence_but_lowers_confidence(self):
        rows = [
            shift(1, "Aida", "Interrupted weekly", "2026-10-05"),
            shift(2, "Aida", "Interrupted weekly", "2026-10-12"),
            shift(3, "Aida", "Interrupted weekly", "2026-10-26"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=4
        )
        self.assertEqual(proposals[0]["recurrence_period"], 1)
        self.assertAlmostEqual(proposals[0]["confidence"], 0.75)

    def test_team_signature_is_order_independent(self):
        rows = [
            shift(1, "Aida, Edita", "Maison E", "2026-10-19"),
            shift(2, "Edita, Aida", "Maison E", "2026-10-26"),
        ]
        groups = app.ap_group_by_key(rows)
        self.assertEqual(len(groups), 1)
        proposals = app.ap_project(groups, "2026-11-02", lookback_weeks=2)
        self.assertEqual(proposals[0]["worker"], "Aida, Edita")

    def test_one_shot_is_hidden_unless_exceptional_is_requested(self):
        groups = app.ap_group_by_key([
            shift(1, "Aida", "One shot", "2026-10-26")
        ])
        self.assertEqual(
            app.ap_project(groups, "2026-11-02", lookback_weeks=8), []
        )
        proposals = app.ap_project(
            groups, "2026-11-02", include_exceptional=True,
            lookback_weeks=8,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 0)
        self.assertTrue(proposals[0]["requires_manual_validation"])

    def test_low_confidence_cycle_remains_visible_for_manual_review(self):
        rows = [
            shift(1, "Aida", "Sparse weekly", "2026-10-19"),
            shift(2, "Aida", "Sparse weekly", "2026-10-26"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 1)
        self.assertAlmostEqual(proposals[0]["confidence"], 0.25)
        self.assertTrue(proposals[0]["requires_manual_validation"])

    def test_non_quarter_hour_times_and_duration_are_preserved(self):
        rows = [
            shift(1, "Aida", "Precise", "2026-10-19", "08:07-09:23"),
            shift(2, "Aida", "Precise", "2026-10-26", "08:07-09:23"),
        ]
        proposal = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=2
        )[0]
        self.assertEqual(proposal["time"], "08:07-09:23")
        self.assertAlmostEqual(proposal["duration_hours"], 76 / 60.0)

    def test_fetch_history_uses_inclusive_bounds_and_stable_shape(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE shifts (id INTEGER PRIMARY KEY, worker TEXT, "
            "client TEXT, date TEXT, time TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO shifts VALUES (?,?,?,?,?,?)",
            [
                shift(1, "Aida", "Before", "2026-10-18"),
                shift(2, "Aida", "First", "2026-10-19"),
                shift(3, "Aida", "Last", "2026-11-01"),
                shift(4, "Aida", "After", "2026-11-02"),
            ],
        )
        rows = app.ap_fetch_history(conn, "2026-10-19", "2026-11-01")
        conn.close()
        self.assertEqual([row[2] for row in rows], ["First", "Last"])
        self.assertTrue(all(len(row) == 6 for row in rows))


def proposal(day="2026-11-02", hours="08:00-10:00", workers="Aida",
             client="Client A", **extra):
    value = {
        "target_date": day,
        "time": hours,
        "worker": workers,
        "client": client,
        "issues": [],
    }
    value.update(extra)
    return value


class ConstraintChecksTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE shifts (
                id INTEGER PRIMARY KEY, worker TEXT, client TEXT,
                date TEXT, time TEXT, status TEXT
            );
            CREATE TABLE shift_locks (
                shift_id INTEGER PRIMARY KEY,
                locked_at TEXT DEFAULT '', locked_by TEXT DEFAULT ''
            );
            CREATE TABLE absences (
                id INTEGER PRIMARY KEY, worker TEXT, type TEXT,
                date_from TEXT, date_to TEXT, note TEXT DEFAULT ''
            );
            CREATE TABLE leave_requests (
                id INTEGER PRIMARY KEY, worker TEXT, type TEXT,
                date_from TEXT, date_to TEXT, note TEXT,
                status TEXT, created_at TEXT, admin_note TEXT
            );
            CREATE TABLE holidays (
                id INTEGER PRIMARY KEY, date TEXT UNIQUE, name TEXT
            );
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                contract_from TEXT, contract_to TEXT
            );
            CREATE TABLE workers (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                contract_end_date TEXT DEFAULT ''
            );
        """)
        self.conn.executemany(
            "INSERT INTO workers (name, contract_end_date) VALUES (?,?)",
            [("Aida", ""), ("Edita", ""), ("Samela", "2026-10-31")],
        )
        self.conn.executemany(
            "INSERT INTO clients (name, contract_from, contract_to) "
            "VALUES (?,?,?)",
            [
                ("Client A", "2026-01-01", "2026-12-31"),
                ("Expired Client", "2025-01-01", "2026-10-31"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def issue_codes(self, checked_proposal):
        return [issue["code"] for issue in checked_proposal["issues"]]

    def test_batch_overlap_marks_both_proposals_for_shared_worker(self):
        proposed = [
            proposal(hours="08:00-10:00", workers="Aida, Edita"),
            proposal(hours="09:30-11:00", workers="Edita", client="Client B"),
        ]
        issues = app.ap_check_overlap_batch(proposed)
        self.assertEqual(issues[0][0]["code"], "overlap_batch")
        self.assertEqual(issues[1][0]["code"], "overlap_batch")
        self.assertEqual(issues[0][0]["workers"], ["Edita"])

    def test_adjacent_times_do_not_overlap(self):
        proposed = [
            proposal(hours="08:00-10:00"),
            proposal(hours="10:00-12:00", client="Client B"),
        ]
        self.assertEqual(app.ap_check_overlap_batch(proposed), {0: [], 1: []})

    def test_existing_overlap_and_lock_are_both_blocking(self):
        self.conn.execute(
            "INSERT INTO shifts VALUES (1,?,?,?,?,?)",
            ("Aida", "Existing", "2026-11-02", "09:00-11:00", "planned"),
        )
        self.conn.execute(
            "INSERT INTO shift_locks VALUES (1,?,?)", ("now", "admin")
        )
        issues = app.ap_check_overlap_existing(
            self.conn, proposal(hours="10:30-12:00")
        )
        self.assertEqual([item["code"] for item in issues],
                         ["overlap_existing", "locked"])
        self.assertTrue(app.ap_check_shift_locked(self.conn, 1))

    def test_existing_shift_for_other_worker_is_not_a_conflict(self):
        self.conn.execute(
            "INSERT INTO shifts VALUES (1,?,?,?,?,?)",
            ("Edita", "Existing", "2026-11-02", "09:00-11:00", "planned"),
        )
        self.assertEqual(
            app.ap_check_overlap_existing(
                self.conn, proposal(hours="09:30-10:30", workers="Aida")
            ),
            [],
        )

    def test_same_client_slot_different_worker_is_blocking(self):
        """Regression (staging incident 2026-10-26): client A already
        has a 09:00-11:00 visit by worker1; a proposal for a different
        worker on the SAME client+time slot must surface a blocking
        ``client_double_booking`` issue, not pass silently."""
        self.conn.execute(
            "INSERT INTO shifts VALUES (42,?,?,?,?,?)",
            ("worker1", "Client A", "2026-11-02", "09:00-11:00", "planned"),
        )
        issues = app.ap_check_overlap_existing(
            self.conn,
            proposal(hours="09:00-11:00", workers="Aida", client="Client A"),
        )
        codes = [i["code"] for i in issues]
        self.assertIn("client_double_booking", codes)
        same = [i for i in issues if i["code"] == "client_double_booking"][0]
        self.assertEqual(same["severity"], "blocking")
        self.assertEqual(same["shift_id"], 42)
        self.assertEqual(same["existing_worker"], "worker1")

    def test_same_client_overlapping_time_different_worker_is_blocking(self):
        """Partial time overlap, different worker, same client → still
        ``client_double_booking``."""
        self.conn.execute(
            "INSERT INTO shifts VALUES (43,?,?,?,?,?)",
            ("Milana", "Client A", "2026-11-02", "10:30-12:30", "planned"),
        )
        issues = app.ap_check_overlap_existing(
            self.conn,
            proposal(hours="09:00-11:00", workers="Aida", client="Client A"),
        )
        self.assertIn(
            "client_double_booking", [i["code"] for i in issues],
        )

    def test_same_worker_takes_precedence_over_client_booking_code(self):
        """When the overlap is already explained by a shared worker
        we keep the existing ``overlap_existing`` issue (not
        ``client_double_booking``) so historical callers that filter on
        that specific code keep working."""
        self.conn.execute(
            "INSERT INTO shifts VALUES (44,?,?,?,?,?)",
            ("Aida", "Client A", "2026-11-02", "09:00-11:00", "planned"),
        )
        issues = app.ap_check_overlap_existing(
            self.conn,
            proposal(hours="09:00-11:00", workers="Aida", client="Client A"),
        )
        codes = [i["code"] for i in issues]
        self.assertIn("overlap_existing", codes)
        self.assertNotIn("client_double_booking", codes)

    def test_different_client_overlapping_time_not_a_conflict(self):
        """Two different clients happening in the same room/time is a
        human-scheduler judgement — the engine must NOT make it
        blocking here."""
        self.conn.execute(
            "INSERT INTO shifts VALUES (45,?,?,?,?,?)",
            ("worker1", "Other Client", "2026-11-02", "09:00-11:00", "planned"),
        )
        issues = app.ap_check_overlap_existing(
            self.conn,
            proposal(hours="09:00-11:00", workers="Aida", client="Client A"),
        )
        self.assertEqual(issues, [])

    def test_client_slot_conflict_helper_direct(self):
        """Lower-level helper used by the apply boundary."""
        self.conn.execute(
            "INSERT INTO shifts VALUES (50,?,?,?,?,?)",
            ("worker1", "Client A", "2026-11-02", "09:00-11:00", "planned"),
        )
        self.assertEqual(
            app.client_slot_conflict(
                self.conn, "Client A", "2026-11-02", "09:30-10:30",
            ),
            50,
        )
        # Different client → no conflict
        self.assertIsNone(
            app.client_slot_conflict(
                self.conn, "Other", "2026-11-02", "09:30-10:30",
            ),
        )
        # Different day → no conflict
        self.assertIsNone(
            app.client_slot_conflict(
                self.conn, "Client A", "2026-11-03", "09:00-11:00",
            ),
        )
        # exclude_shift_id suppresses the collision with the same row
        self.assertIsNone(
            app.client_slot_conflict(
                self.conn, "Client A", "2026-11-02", "09:30-10:30",
                exclude_shift_id=50,
            ),
        )

    def test_run_checks_marks_proposal_as_blocking_on_client_double_booking(self):
        """End-to-end through ``ap_run_checks`` so the review page's
        ``has_blocking_issues`` flag flips to True — this is what
        prevents the apply button from succeeding when the slot is
        already booked for the same client by a different worker.
        Before the fix, this scenario produced 0 blocking conflicts."""
        self.conn.execute(
            "INSERT INTO shifts VALUES (46,?,?,?,?,?)",
            ("worker1", "Client A", "2026-11-02", "09:00-11:00", "planned"),
        )
        checked = app.ap_run_checks(
            self.conn,
            [proposal(hours="09:00-11:00", workers="Aida", client="Client A")],
            "2026-11-02",
        )[0]
        codes = [i["code"] for i in checked["issues"]]
        self.assertIn("client_double_booking", codes)
        self.assertTrue(checked["has_blocking_issues"])

    def test_absence_is_inclusive_and_blocks_one_team_member(self):
        self.conn.execute(
            "INSERT INTO absences VALUES (1,?,?,?,?,?)",
            ("Edita", "vacation", "2026-11-02", "2026-11-04", ""),
        )
        self.assertTrue(app.ap_check_absence(self.conn, "Edita", "2026-11-02"))
        checked = app.ap_run_checks(
            self.conn,
            [proposal(workers="Aida, Edita")],
            "2026-11-02",
        )[0]
        self.assertIn("absence", self.issue_codes(checked))
        self.assertTrue(checked["has_blocking_issues"])

    def test_only_approved_leave_is_blocking(self):
        self.conn.executemany(
            "INSERT INTO leave_requests VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, "Aida", "vacation", "2026-11-02", "2026-11-02", "",
                 "pending", "now", ""),
                (2, "Edita", "vacation", "2026-11-02", "2026-11-02", "",
                 "approved", "now", ""),
            ],
        )
        self.assertFalse(
            app.ap_check_leave_approved(self.conn, "Aida", "2026-11-02")
        )
        self.assertTrue(
            app.ap_check_leave_approved(self.conn, "Edita", "2026-11-02")
        )

    def test_holiday_and_client_contract_are_warnings_only(self):
        self.conn.execute(
            "INSERT INTO holidays VALUES (1,?,?)",
            ("2026-11-02", "Allerseelen"),
        )
        checked = app.ap_run_checks(
            self.conn,
            [proposal(client="Expired Client")],
            "2026-11-02",
        )[0]
        self.assertIn("holiday", self.issue_codes(checked))
        self.assertIn("contract", self.issue_codes(checked))
        self.assertFalse(checked["has_blocking_issues"])

    def test_missing_or_expired_worker_is_blocking(self):
        missing = app.ap_run_checks(
            self.conn, [proposal(workers="Unknown")], "2026-11-02"
        )[0]
        expired = app.ap_run_checks(
            self.conn, [proposal(workers="Samela")], "2026-11-02"
        )[0]
        self.assertIn("worker_missing", self.issue_codes(missing))
        self.assertIn("worker_contract", self.issue_codes(expired))
        self.assertTrue(missing["has_blocking_issues"])
        self.assertTrue(expired["has_blocking_issues"])

    def test_integration_combines_batch_overlap_and_absence_without_writes(self):
        self.conn.execute(
            "INSERT INTO absences VALUES (1,?,?,?,?,?)",
            ("Aida", "sick", "2026-11-02", "2026-11-02", ""),
        )
        proposed = [
            proposal(hours="08:00-10:00", workers="Aida, Edita"),
            proposal(hours="09:00-11:00", workers="Edita", client="Client B"),
        ]
        before = self.conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        checked = app.ap_run_checks(self.conn, proposed, "2026-11-02")
        after = self.conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(
            set(self.issue_codes(checked[0])), {"absence", "overlap_batch"}
        )
        self.assertEqual(
            set(self.issue_codes(checked[1])), {"overlap_batch"}
        )

    def test_rechecking_does_not_duplicate_issues(self):
        self.conn.execute(
            "INSERT INTO holidays VALUES (1,?,?)",
            ("2026-11-02", "Holiday"),
        )
        first = app.ap_run_checks(
            self.conn, [proposal(issues=[
                {"code": "exceptional", "severity": "warning"}
            ])], "2026-11-02"
        )
        second = app.ap_run_checks(self.conn, first, "2026-11-02")
        self.assertEqual(self.issue_codes(second[0]).count("exceptional"), 1)
        self.assertEqual(self.issue_codes(second[0]).count("holiday"), 1)

    def test_outside_target_week_and_invalid_time_are_blocking(self):
        checked = app.ap_run_checks(
            self.conn,
            [proposal(day="2026-11-09"), proposal(hours="12:00-11:00")],
            "2026-11-02",
        )
        self.assertIn("outside_target_week", self.issue_codes(checked[0]))
        self.assertIn("invalid_data", self.issue_codes(checked[1]))


class Phase4DraftRoutesTests(unittest.TestCase):
    TARGET_WEEK = "2026-11-02"
    HISTORY_DATES = [
        "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
        "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
    ]

    def setUp(self):
        previous_flag = app.app.config.get("AUTO_PLANNING_ENABLED", False)
        app.app.config["AUTO_PLANNING_ENABLED"] = True
        self.addCleanup(
            app.app.config.__setitem__,
            "AUTO_PLANNING_ENABLED", previous_flag,
        )
        conn = app.get_conn()
        for table in (
            "shift_proposals", "shift_proposal_batches", "shift_locks",
            "shifts", "absences", "leave_requests", "holidays",
            "workers", "clients",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        self.login(self.client)

    def login(self, client, username="admin", role="admin"):
        with client.session_transaction() as flask_session:
            flask_session["user"] = username
            flask_session["role"] = role
            flask_session["lang"] = "en"

    def csrf_token(self, client=None):
        client = client or self.client
        client.get("/plan/generate")
        with client.session_transaction() as flask_session:
            return flask_session[app.AP_CSRF_SESSION_KEY]

    def seed_weekly_history(self, worker="Aida", client="Client A",
                            add_worker=True):
        conn = app.get_conn()
        if add_worker:
            conn.execute(
                "INSERT INTO workers (name, contract_end_date) VALUES (?, '')",
                (worker,),
            )
        conn.execute(
            "INSERT INTO clients (name, contract_from, contract_to) "
            "VALUES (?, '2026-01-01', '2026-12-31')",
            (client,),
        )
        conn.executemany(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES (?, ?, ?, '08:00-10:00', 'done')",
            [(worker, client, day) for day in self.HISTORY_DATES],
        )
        conn.commit()
        conn.close()

    def generate(self, lookback=8, include_exceptional=False):
        data = {
            "target_week_start": self.TARGET_WEEK,
            "lookback_weeks": str(lookback),
            "csrf_token": self.csrf_token(),
        }
        if include_exceptional:
            data["include_exceptional"] = "1"
        return self.client.post("/plan/generate", data=data)

    def batch_id_from_response(self, response):
        parsed = urllib.parse.urlparse(response.headers["Location"])
        return int(urllib.parse.parse_qs(parsed.query)["batch"][0])

    def db_rows(self, query, params=()):
        conn = app.get_conn()
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def test_generate_target_week_creates_reviewable_draft_without_shift_writes(self):
        self.seed_weekly_history()
        before = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "ORDER BY id"
        )

        response = self.generate(8)

        self.assertEqual(response.status_code, 302)
        batch_id = self.batch_id_from_response(response)
        batch = self.db_rows(
            "SELECT target_week_start, lookback_weeks, include_exceptional "
            "FROM shift_proposal_batches WHERE id = ?", (batch_id,),
        )[0]
        proposals = self.db_rows(
            "SELECT target_date, client, worker, time FROM shift_proposals "
            "WHERE batch_id = ?", (batch_id,),
        )
        after = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "ORDER BY id"
        )
        self.assertEqual(batch, (self.TARGET_WEEK, 8, 0))
        self.assertEqual(
            proposals, [(self.TARGET_WEEK, "Client A", "Aida", "08:00-10:00")]
        )
        self.assertEqual(after, before)

        review = self.client.get(response.headers["Location"])
        self.assertEqual(review.status_code, 200)
        self.assertIn(b'data-proposal-id="', review.data)
        self.assertIn(b'Client A', review.data)
        self.assertIn(b'value="2026-11-02"', review.data)

    def test_history_choices_2_4_and_8_are_persisted_and_generate(self):
        self.seed_weekly_history()
        batch_ids = [
            self.batch_id_from_response(self.generate(weeks))
            for weeks in (2, 4, 8)
        ]
        rows = self.db_rows(
            "SELECT lookback_weeks FROM shift_proposal_batches "
            "WHERE id IN (?, ?, ?) ORDER BY lookback_weeks", tuple(batch_ids),
        )
        counts = self.db_rows(
            "SELECT batch_id, COUNT(*) FROM shift_proposals "
            "WHERE batch_id IN (?, ?, ?) GROUP BY batch_id", tuple(batch_ids),
        )
        self.assertEqual(rows, [(2,), (4,), (8,)])
        self.assertEqual(sorted(count for _, count in counts), [1, 1, 1])

    def test_next_week_is_the_generate_form_default(self):
        expected = app.ap_default_target_week_start().isoformat().encode()
        response = self.client.get("/plan/generate")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="' + expected + b'"', response.data)
        self.assertIn(b'value="8" checked', response.data)

    def test_permissions_restrict_generate_and_review_to_admin(self):
        anonymous = app.app.test_client()
        self.assertEqual(anonymous.get("/plan/generate").status_code, 302)
        self.assertIn("/login", anonymous.get("/plan/generate").headers["Location"])

        worker = app.app.test_client()
        self.login(worker, username="Aida", role="worker")
        self.assertEqual(worker.get("/plan/generate").status_code, 403)
        self.assertEqual(worker.post("/plan/generate").status_code, 403)
        self.assertEqual(worker.get("/plan/review?batch=1").status_code, 403)

    def test_constraint_warnings_are_stored_and_rendered(self):
        self.seed_weekly_history()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'vacation', ?, ?, '')",
            (self.TARGET_WEEK, self.TARGET_WEEK),
        )
        conn.commit()
        conn.close()

        response = self.generate()
        batch_id = self.batch_id_from_response(response)
        issue_text = self.db_rows(
            "SELECT issues FROM shift_proposals WHERE batch_id = ?", (batch_id,),
        )[0][0]
        self.assertIn('"code": "absence"', issue_text)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'data-issue-code="absence"', review.data)
        self.assertIn(b'Manual review required', review.data)

    def test_confidence_below_threshold_is_clearly_manual(self):
        self.seed_weekly_history()
        conn = app.get_conn()
        conn.execute(
            "DELETE FROM shifts WHERE date < '2026-10-19'"
        )
        conn.commit()
        conn.close()

        response = self.generate(8)
        batch_id = self.batch_id_from_response(response)
        confidence = self.db_rows(
            "SELECT confidence FROM shift_proposals WHERE batch_id = ?",
            (batch_id,),
        )[0][0]
        self.assertLess(confidence, app.AP_DEFAULT_MIN_CONFIDENCE)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'Manual review required', review.data)
        self.assertIn(b'25%', review.data)

    def test_removed_or_renamed_historical_worker_is_actionable(self):
        self.seed_weekly_history(worker="Former Worker", add_worker=False)
        response = self.generate()
        batch_id = self.batch_id_from_response(response)
        issue_text = self.db_rows(
            "SELECT issues FROM shift_proposals WHERE batch_id = ?", (batch_id,),
        )[0][0]
        self.assertIn('"code": "worker_missing"', issue_text)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'data-issue-code="worker_missing"', review.data)
        self.assertIn(b'Former Worker', review.data)
        self.assertIn(b'no longer exists or was renamed', review.data)

    def test_repeated_generation_reuses_same_batch_without_duplicates(self):
        self.seed_weekly_history()
        first = self.generate()
        second = self.generate()
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposal_batches")[0][0], 1
        )
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposals")[0][0], 1
        )

    def test_draft_is_private_to_user_and_originating_session(self):
        self.seed_weekly_history()
        response = self.generate()
        location = response.headers["Location"]
        self.assertEqual(self.client.get(location).status_code, 200)

        same_user_new_session = app.app.test_client()
        self.login(same_user_new_session, username="admin", role="admin")
        self.assertEqual(same_user_new_session.get(location).status_code, 404)

        other_admin = app.app.test_client()
        self.login(other_admin, username="other-admin", role="admin")
        self.assertEqual(other_admin.get(location).status_code, 404)

    def test_existing_locked_shift_is_preserved_and_reported(self):
        self.seed_weekly_history()
        conn = app.get_conn()
        cursor = conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Aida', 'Existing Client', ?, '09:00-11:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        existing_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
            "VALUES (?, 'now', 'admin')", (existing_id,),
        )
        conn.commit()
        conn.close()
        before = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "WHERE id = ?", (existing_id,),
        )[0]

        response = self.generate()

        after = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "WHERE id = ?", (existing_id,),
        )[0]
        issue_text = self.db_rows(
            "SELECT issues FROM shift_proposals ORDER BY id DESC LIMIT 1"
        )[0][0]
        self.assertEqual(after, before)
        self.assertIn('"code": "overlap_existing"', issue_text)
        self.assertIn('"code": "locked"', issue_text)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'data-existing-shift-id="', review.data)
        self.assertIn(b'Locked', review.data)

    def test_client_double_booking_surfaces_on_review_page_html(self):
        """End-to-end: a pre-existing 09:00-11:00 visit by ``worker1``
        for the SAME client as the proposal must flip the review page
        into a non-applyable state and render the
        ``auto_plan_issue_client_double_booking`` warning. Before the
        5d342ad fix, this scenario displayed zero conflicts in the UI.
        """
        self.seed_weekly_history(worker="Aida", client="Client A")
        conn = app.get_conn()
        cursor = conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('worker1', 'Client A', ?, '08:00-10:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        pre_existing_id = cursor.lastrowid
        conn.commit()
        conn.close()

        response = self.generate()
        review = self.client.get(response.headers["Location"])
        self.assertEqual(review.status_code, 200)
        body = review.data

        # 1. The specific issue code is in the HTML
        self.assertIn(b"client_double_booking", body)
        # 2. ``has_blocking_issues`` renders a visible blocking marker
        #    on the proposal row (reused ``apr-blocking`` convention)
        self.assertTrue(
            b"apr-blocking" in body or b'data-blocking="1"' in body
            or b'severity="blocking"' in body
            or b"blocking" in body.lower(),
            "no visible blocking marker on the review page",
        )
        # 3. The pre-existing shift row is still present in the
        #    "existing shifts" review table (template reads from
        #    ``ap_fetch_existing_week`` and renders a ``data-existing-shift-id``
        #    on each row).
        self.assertIn(
            f'data-existing-shift-id="{pre_existing_id}"'.encode(),
            body,
        )
        # 4. The pre-existing shift was NOT modified
        after = self.db_rows(
            "SELECT worker, client, date, time, status FROM shifts WHERE id = ?",
            (pre_existing_id,),
        )[0]
        self.assertEqual(
            after,
            ("worker1", "Client A", self.TARGET_WEEK, "08:00-10:00", "planned"),
        )

    def test_invalid_non_monday_target_is_rejected_without_draft(self):
        response = self.client.post("/plan/generate", data={
            "target_week_start": "2026-11-03", "lookback_weeks": "8",
            "csrf_token": self.csrf_token(),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposal_batches")[0][0], 0
        )


class Phase5DraftWorkflowTests(unittest.TestCase):
    TARGET_WEEK = "2026-11-02"
    HISTORY_DATES = Phase4DraftRoutesTests.HISTORY_DATES

    def setUp(self):
        previous_flag = app.app.config.get("AUTO_PLANNING_ENABLED", False)
        app.app.config["AUTO_PLANNING_ENABLED"] = True
        self.addCleanup(
            app.app.config.__setitem__,
            "AUTO_PLANNING_ENABLED", previous_flag,
        )
        conn = app.get_conn()
        for table in (
            "shift_proposals", "shift_proposal_batches", "shift_locks",
            "shifts", "absences", "leave_requests", "holidays",
            "workers", "clients",
        ):
            conn.execute(f"DELETE FROM {table}")
        for worker in ("Aida", "Bea", "Cara"):
            conn.execute(
                "INSERT INTO workers (name, contract_end_date) VALUES (?, '')",
                (worker,),
            )
        for client_name in ("Client A", "Client B", "Client C"):
            conn.execute(
                "INSERT INTO clients (name, contract_from, contract_to) "
                "VALUES (?, '2026-01-01', '2026-12-31')",
                (client_name,),
            )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        self.login(self.client)

    def login(self, client, username="admin", role="admin",
              owner_token=None, csrf_token=None):
        with client.session_transaction() as flask_session:
            flask_session["user"] = username
            flask_session["role"] = role
            flask_session["lang"] = "en"
            if owner_token is not None:
                flask_session[app.AP_DRAFT_SESSION_KEY] = owner_token
            if csrf_token is not None:
                flask_session[app.AP_CSRF_SESSION_KEY] = csrf_token

    def csrf_token(self, client=None):
        client = client or self.client
        client.get("/plan/generate")
        with client.session_transaction() as flask_session:
            return flask_session[app.AP_CSRF_SESSION_KEY]

    def session_security(self):
        self.csrf_token()
        with self.client.session_transaction() as flask_session:
            return (
                flask_session[app.AP_DRAFT_SESSION_KEY],
                flask_session[app.AP_CSRF_SESSION_KEY],
            )

    def db_rows(self, query, params=()):
        conn = app.get_conn()
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def seed_history(self, worker="Aida", client="Client A",
                     hours="08:00-10:00"):
        conn = app.get_conn()
        conn.executemany(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES (?, ?, ?, ?, 'done')",
            [(worker, client, day, hours) for day in self.HISTORY_DATES],
        )
        conn.commit()
        conn.close()

    def generate(self, lookback=8):
        response = self.client.post("/plan/generate", data={
            "target_week_start": self.TARGET_WEEK,
            "lookback_weeks": str(lookback),
            "csrf_token": self.csrf_token(),
        })
        self.assertEqual(response.status_code, 302)
        return int(urllib.parse.parse_qs(
            urllib.parse.urlparse(response.headers["Location"]).query
        )["batch"][0])

    def proposals(self, batch_id):
        return self.db_rows(
            "SELECT id, target_date, time, client, worker, status, "
            "manual_override, is_locked, issues, applied_shift_id "
            "FROM shift_proposals WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        )

    def proposal_form(self, proposal_row, action="save", **changes):
        start_time, end_time = proposal_row[2].split("-", 1)
        data = {
            "csrf_token": self.csrf_token(),
            "action": action,
            "workers": changes.pop(
                "workers", [name.strip() for name in proposal_row[4].split(",")]
            ),
            "client": changes.pop("client", proposal_row[3]),
            "target_date": changes.pop("target_date", proposal_row[1]),
            "start_time": changes.pop("start_time", start_time),
            "end_time": changes.pop("end_time", end_time),
        }
        data.update(changes)
        return data

    def update(self, batch_id, proposal_id, action="save", **changes):
        row = next(
            row for row in self.proposals(batch_id) if row[0] == proposal_id
        )
        return self.client.post(
            f"/plan/review/{batch_id}/proposal/{proposal_id}/update",
            data=self.proposal_form(row, action=action, **changes),
        )

    def accept(self, batch_id, proposal_id, **changes):
        response = self.update(
            batch_id, proposal_id, action="accept", **changes,
        )
        self.assertEqual(response.status_code, 302)
        return response

    def apply(self, batch_id, proposal_ids, client=None, csrf=None):
        target_client = client or self.client
        return target_client.post(
            f"/plan/review/{batch_id}/apply",
            data={
                "csrf_token": csrf or self.csrf_token(target_client),
                "confirm_apply": "1",
                "proposal_ids": [str(value) for value in proposal_ids],
            },
        )

    def target_shifts(self):
        return self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "WHERE date >= ? AND date <= '2026-11-08' ORDER BY id",
            (self.TARGET_WEEK,),
        )

    def insert_target_shift(self, worker="Aida", client="Client A",
                            day=None, hours="08:00-10:00"):
        conn = app.get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, ?, 'planned')",
                (worker, client, day or self.TARGET_WEEK, hours),
            )
            shift_id = cursor.lastrowid
            conn.commit()
            return shift_id
        finally:
            conn.close()

    def test_modify_one_worker_without_changing_other_proposals(self):
        self.seed_history()
        self.seed_history("Cara", "Client B", "11:00-13:00")
        batch_id = self.generate()
        first, second = self.proposals(batch_id)
        response = self.update(batch_id, first[0], workers=["Bea"])
        self.assertEqual(response.status_code, 302)
        after = self.proposals(batch_id)
        self.assertEqual(after[0][4], "Bea")
        self.assertEqual(after[1][4], second[4])

    def test_replace_multi_worker_team(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.update(batch_id, proposal_id, workers=["Bea", "Cara"])
        self.assertEqual(self.proposals(batch_id)[0][4], "Bea, Cara")

    def test_modify_client(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.update(batch_id, proposal_id, client="Client B")
        self.assertEqual(self.proposals(batch_id)[0][3], "Client B")

    def test_modify_date_and_hhmm_time(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.update(
            batch_id, proposal_id, target_date="2026-11-03",
            start_time="10:15", end_time="12:45",
        )
        row = self.proposals(batch_id)[0]
        self.assertEqual(row[1], "2026-11-03")
        self.assertEqual(row[2], "10:15-12:45")

    def test_tampered_proposal_fields_are_rejected_server_side(self):
        self.seed_history()
        batch_id = self.generate()
        original = self.proposals(batch_id)[0]
        proposal_id = original[0]
        invalid_changes = (
            {"workers": ["Removed Worker"]},
            {"client": "Missing Client"},
            {"target_date": "2026-11-09"},
            {"start_time": "8:00"},
            {"end_time": "08:00"},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                response = self.update(batch_id, proposal_id, **changes)
                self.assertEqual(response.status_code, 400)
                current = self.proposals(batch_id)[0]
                self.assertEqual(current[1:8], original[1:8])

    def test_explicit_acceptance_marks_manual_validation(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        conn = app.get_conn()
        conn.execute(
            "UPDATE shift_proposals SET confidence = 0.25 WHERE id = ?",
            (proposal_id,),
        )
        conn.commit(); conn.close()
        self.accept(batch_id, proposal_id)
        row = self.proposals(batch_id)[0]
        self.assertEqual(row[5], "accepted")
        self.assertEqual(row[6], 1)

    def test_reject_proposal_and_keep_it_out_of_schedule(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        response = self.update(batch_id, proposal_id, action="reject")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.proposals(batch_id)[0][5], "rejected")
        self.assertEqual(self.target_shifts(), [])

    def test_partial_application_only_inserts_selected_accepted_rows(self):
        self.seed_history()
        self.seed_history("Bea", "Client B", "11:00-13:00")
        batch_id = self.generate()
        first, second = self.proposals(batch_id)
        self.accept(batch_id, first[0])
        self.accept(batch_id, second[0])
        response = self.apply(batch_id, [first[0]])
        self.assertEqual(response.status_code, 302)
        target = self.target_shifts()
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0][2], first[3])
        states = self.proposals(batch_id)
        self.assertEqual(states[0][5], "applied")
        self.assertEqual(states[1][5], "accepted")

    def test_apply_requires_explicit_confirmation_marker(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        response = self.client.post(
            f"/plan/review/{batch_id}/apply",
            data={
                "csrf_token": self.csrf_token(),
                "proposal_ids": [str(proposal_id)],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.target_shifts(), [])

    def test_manual_replacement_revalidates_missing_worker_issue(self):
        self.seed_history("Former Worker", "Client A")
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.assertIn("worker_missing", self.proposals(batch_id)[0][8])
        self.accept(batch_id, proposal_id, workers=["Aida"])
        row = self.proposals(batch_id)[0]
        self.assertEqual(row[5], "accepted")
        self.assertNotIn("worker_missing", row[8])

    def test_absence_added_after_generation_blocks_application(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'vacation', ?, ?, '')",
            (self.TARGET_WEEK, self.TARGET_WEEK),
        )
        conn.commit(); conn.close()
        response = self.apply(batch_id, [proposal_id])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.target_shifts(), [])
        self.assertIn("absence", self.proposals(batch_id)[0][8])
        self.assertEqual(
            self.db_rows(
                "SELECT applied_at FROM shift_proposal_batches WHERE id = ?",
                (batch_id,),
            )[0][0], "",
        )

    def test_conflict_added_after_generation_blocks_application(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Aida', 'Client B', ?, '09:00-11:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        conn.commit(); conn.close()
        self.apply(batch_id, [proposal_id])
        target = self.target_shifts()
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0][2], "Client B")
        self.assertIn("overlap_existing", self.proposals(batch_id)[0][8])

    def test_existing_locked_shift_is_never_overwritten(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        conn = app.get_conn()
        cursor = conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Aida', 'Client C', ?, '09:00-11:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        shift_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
            "VALUES (?, 'now', 'admin')", (shift_id,),
        )
        conn.commit(); conn.close()
        self.apply(batch_id, [proposal_id])
        self.assertEqual(len(self.target_shifts()), 1)
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_locks WHERE shift_id = ?", (shift_id,))[0][0],
            1,
        )
        self.assertIn("locked", self.proposals(batch_id)[0][8])

    def test_repeated_application_never_creates_duplicates(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        self.assertEqual(self.apply(batch_id, [proposal_id]).status_code, 302)
        self.assertEqual(self.apply(batch_id, [proposal_id]).status_code, 409)
        self.assertEqual(len(self.target_shifts()), 1)

    def test_two_concurrent_apply_requests_insert_once(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        owner_token, csrf_token = self.session_security()
        clients = [app.app.test_client(), app.app.test_client()]
        for client in clients:
            self.login(
                client, owner_token=owner_token, csrf_token=csrf_token,
            )
        barrier = threading.Barrier(2)

        def submit(client):
            barrier.wait(timeout=5)
            return client.post(
                f"/plan/review/{batch_id}/apply",
                data={
                    "csrf_token": csrf_token,
                    "confirm_apply": "1",
                    "proposal_ids": [str(proposal_id)],
                },
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(submit, clients))
        self.assertEqual(sorted(statuses), [302, 409])
        self.assertEqual(len(self.target_shifts()), 1)

    def test_concurrent_apply_requests_create_at_most_one_shift(self):
        """P1 concurrency: two parallel POST /plan/review/<b>/apply
        requests race against the same batch; the write lock
        (``ap_lock_batch_mutation`` + ``ap_lock_shift_mutations``) +
        ``client_slot_conflict`` + batch state transition ensure at
        most ONE wins. The loser must see 409 or a closed-batch
        error, and the ``shifts`` table must contain exactly one new
        row for the target slot.
        """
        self.seed_history()
        batch_id = self.generate()
        proposal_ids = [row[0] for row in self.proposals(batch_id)]
        for proposal_id in proposal_ids:
            self.accept(batch_id, proposal_id)
        before = len(self.target_shifts())

        # Share the SAME admin owner_token across both threads so
        # ``ap_owned_batch_for_mutation`` recognises both as the
        # legitimate owner of this draft (two browser tabs of the
        # same admin session is the realistic race scenario).
        with self.client.session_transaction() as sess:
            owner_token = sess.get(app.AP_DRAFT_SESSION_KEY, "")
        self.assertTrue(owner_token, "owner_token missing from setUp session")

        results = {}

        def _attempt(label):
            # Each thread uses its OWN Flask test client — Werkzeug's
            # test client is not thread-safe when shared — but ALL
            # clients mirror the same admin session + owner_token.
            client = app.app.test_client()
            with client.session_transaction() as session_data:
                session_data["user"] = "admin"
                session_data["role"] = "admin"
                session_data[app.AP_DRAFT_SESSION_KEY] = owner_token
            csrf = self.csrf_token(client)
            response = client.post(
                f"/plan/review/{batch_id}/apply",
                data={
                    "csrf_token": csrf,
                    "confirm_apply": "1",
                    "proposal_ids": [str(v) for v in proposal_ids],
                },
            )
            results[label] = response.status_code

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_attempt, name) for name in ("A", "B")]
            for f in futures:
                f.result()

        status_values = sorted(results.values())
        # Exactly one 302 (success redirect) and one 409 (or 302 to a
        # closed-batch flash) — never two 302 "applied" responses.
        self.assertIn(302, status_values, results)
        other = [s for s in status_values if s != 302][0] if len(
            {s for s in status_values}) > 1 else 302
        # Loser is either 409 (AutoPlanConflict), 400 (bad state), or
        # 302 with a "draft closed" flash. We just assert that the
        # DB state proves no double-insert happened.
        after = self.target_shifts()
        new_rows = [row for row in after if row[0] > (
            after[-1][0] - len(after) if after else 0)]
        # Hard invariant: at most ONE new row for the slot across
        # both threads.
        self.assertEqual(
            len(after) - before, 1,
            f"expected +1 shift after concurrent applies, got "
            f"{len(after) - before}. results={results}, after={after}",
        )

    def test_apply_refuses_client_slot_booked_by_different_worker(self):
        """Belt-and-braces at the write boundary: even if a review
        passed through with ``has_blocking_issues=False`` (older
        proposals persisted before the fix, concurrent insert
        between generate and apply, …), the apply path refuses to
        create a duplicate client booking under a different worker.
        """
        self.seed_history()
        batch_id = self.generate()
        proposal_ids = [row[0] for row in self.proposals(batch_id)]
        for proposal_id in proposal_ids:
            self.accept(batch_id, proposal_id)
        existing_rows = self.proposals(batch_id)
        self.assertEqual(len(existing_rows), 1, existing_rows)
        prop_row = existing_rows[0]
        # proposals()'s column order is: id, target_date, time,
        # client, worker, status, …
        target_date = prop_row[1]
        target_time = prop_row[2]
        target_client = prop_row[3]
        # Insert a manual shift occupying the same client slot with a
        # different worker between review and apply.
        self.insert_target_shift(
            worker="worker1", client=target_client,
            day=target_date, hours=target_time,
        )
        with mock.patch.object(
            app, "ap_check_overlap_existing", return_value=[],
        ):
            # Even if the review-time check somehow returned no issues
            # (simulating a stale draft), the apply boundary MUST
            # still refuse.
            response = self.apply(batch_id, proposal_ids)
        # ``AutoPlanConflict`` surfaces as HTTP 409 from the route.
        self.assertEqual(response.status_code, 409)
        # Only the manually inserted shift exists; the apply did NOT
        # create a second row for the same slot.
        rows_in_slot = self.db_rows(
            "SELECT id, worker, client FROM shifts "
            "WHERE client = ? AND date = ? AND time = ?",
            (target_client, target_date, target_time),
        )
        self.assertEqual(len(rows_in_slot), 1)
        self.assertEqual(rows_in_slot[0][1], "worker1")
        # The batch stays open so the operator can review again.
        batches = self.db_rows(
            "SELECT applied_at FROM shift_proposal_batches WHERE id = ?",
            (batch_id,),
        )
        self.assertFalse(batches[0][0])

    def test_apply_rolls_back_all_rows_when_second_insert_fails(self):
        self.seed_history()
        self.seed_history("Bea", "Client B", "11:00-13:00")
        batch_id = self.generate()
        proposal_ids = [row[0] for row in self.proposals(batch_id)]
        for proposal_id in proposal_ids:
            self.accept(batch_id, proposal_id)
        original_insert = app.ap_insert_shift_from_proposal
        calls = {"count": 0}

        def fail_second(conn, proposal):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced second insert failure")
            return original_insert(conn, proposal)

        with self.assertLogs(app.app.logger, level="ERROR") as captured_logs:
            with mock.patch.object(
                app, "ap_insert_shift_from_proposal", side_effect=fail_second,
            ):
                response = self.apply(batch_id, proposal_ids)
        self.assertIn(
            "Auto-planning application failed",
            "\n".join(captured_logs.output),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.target_shifts(), [])
        self.assertTrue(all(row[9] is None for row in self.proposals(batch_id)))
        self.assertEqual(
            self.db_rows(
                "SELECT applied_at FROM shift_proposal_batches WHERE id = ?",
                (batch_id,),
            )[0][0], "",
        )

    def test_other_user_or_browser_session_cannot_mutate_draft(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        same_user_new_session = app.app.test_client()
        self.login(same_user_new_session)
        csrf = self.csrf_token(same_user_new_session)
        response = same_user_new_session.post(
            f"/plan/review/{batch_id}/proposal/{proposal_id}/update",
            data={"csrf_token": csrf, "action": "reject"},
        )
        self.assertEqual(response.status_code, 404)
        other_user = app.app.test_client()
        self.login(other_user, username="other-admin")
        csrf = self.csrf_token(other_user)
        response = other_user.post(
            f"/plan/review/{batch_id}/cancel",
            data={"csrf_token": csrf},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.proposals(batch_id)[0][5], "proposed")

    def test_phase5_posts_require_valid_csrf_and_admin_role(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        conn = app.get_conn()
        cursor = conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Bea', 'Client B', ?, '12:00-13:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        shift_id = cursor.lastrowid
        conn.commit(); conn.close()
        endpoints = [
            (f"/plan/review/{batch_id}/proposal/{proposal_id}/update", {"action": "reject"}),
            (f"/plan/review/{batch_id}/bulk-accept", {"proposal_ids": [str(proposal_id)]}),
            (f"/plan/review/{batch_id}/apply", {"proposal_ids": [str(proposal_id)]}),
            (f"/plan/review/{batch_id}/cancel", {}),
            (f"/plan/review/{batch_id}/shift/{shift_id}/lock", {}),
        ]
        for endpoint, data in endpoints:
            self.assertEqual(self.client.post(endpoint, data=data).status_code, 400)
            invalid = dict(data); invalid["csrf_token"] = "wrong"
            self.assertEqual(self.client.post(endpoint, data=invalid).status_code, 400)
        self.assertEqual(
            self.client.post("/plan/generate", data={
                "target_week_start": self.TARGET_WEEK,
                "lookback_weeks": "8",
            }).status_code,
            400,
        )
        worker_client = app.app.test_client()
        self.login(
            worker_client, username="Aida", role="worker",
            owner_token="worker-owner", csrf_token="worker-csrf",
        )
        self.assertEqual(worker_client.post(
            f"/plan/review/{batch_id}/cancel",
            data={"csrf_token": "worker-csrf"},
        ).status_code, 403)

    def test_lock_unlock_and_delete_cleanup_are_transactional(self):
        self.seed_history()
        batch_id = self.generate()
        conn = app.get_conn()
        cursor = conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Bea', 'Client B', ?, '12:00-13:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        shift_id = cursor.lastrowid
        conn.commit(); conn.close()
        token = self.csrf_token()
        self.assertEqual(self.client.post(
            f"/plan/review/{batch_id}/shift/{shift_id}/lock",
            data={"csrf_token": token},
        ).status_code, 302)
        self.client.post(f"/delete_shift/{shift_id}")
        self.assertEqual(len(self.db_rows("SELECT id FROM shifts WHERE id = ?", (shift_id,))), 1)
        self.assertEqual(self.client.post(
            f"/plan/review/{batch_id}/shift/{shift_id}/unlock",
            data={"csrf_token": token},
        ).status_code, 302)
        self.client.post(f"/delete_shift/{shift_id}")
        self.assertEqual(self.db_rows("SELECT id FROM shifts WHERE id = ?", (shift_id,)), [])
        self.assertEqual(self.db_rows("SELECT shift_id FROM shift_locks WHERE shift_id = ?", (shift_id,)), [])

    def test_legacy_move_shift_respects_lock(self):
        self.seed_history()
        batch_id = self.generate()
        shift_id = self.insert_target_shift()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
            "VALUES (?, '2026-11-01 12:00:00', 'admin')",
            (shift_id,),
        )
        conn.commit(); conn.close()

        response = self.client.post(
            "/move_shift",
            data={"shift_id": str(shift_id), "date": "2026-11-03"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"locked", response.data.lower())
        self.assertEqual(
            self.db_rows(
                "SELECT date, time FROM shifts WHERE id = ?", (shift_id,),
            )[0],
            (self.TARGET_WEEK, "08:00-10:00"),
        )

        response = self.client.post(
            f"/plan/review/{batch_id}/shift/{shift_id}/unlock",
            data={"csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            "/move_shift",
            data={"shift_id": str(shift_id), "date": "2026-11-03"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.db_rows(
                "SELECT date, time FROM shifts WHERE id = ?", (shift_id,),
            )[0],
            ("2026-11-03", "08:00-10:00"),
        )

    def test_legacy_edit_shift_respects_lock(self):
        self.seed_history()
        batch_id = self.generate()
        shift_id = self.insert_target_shift()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
            "VALUES (?, '2026-11-01 12:00:00', 'admin')",
            (shift_id,),
        )
        conn.commit(); conn.close()
        with self.client.session_transaction() as flask_session:
            flask_session["lang"] = "fr"

        edit_data = {
            "workers": ["Bea"], "client": "Client B",
            "date": "2026-11-04", "start_hour": "09",
            "start_minute": "15", "end_hour": "11",
            "end_minute": "45", "status": "done",
            "return_to": "/week?start=2026-11-02",
        }
        response = self.client.post(f"/edit_shift/{shift_id}", data=edit_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(
            "/week?start=2026-11-02"
        ))
        self.assertEqual(
            self.db_rows(
                "SELECT worker, client, date, time, status FROM shifts "
                "WHERE id = ?", (shift_id,),
            )[0],
            ("Aida", "Client A", self.TARGET_WEEK,
             "08:00-10:00", "planned"),
        )
        with self.client.session_transaction() as flask_session:
            messages = [message for _category, message in
                        flask_session.get("_flashes", [])]
        self.assertTrue(any("verrouillée" in message for message in messages))

        response = self.client.post(
            f"/plan/review/{batch_id}/shift/{shift_id}/unlock",
            data={"csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/edit_shift/{shift_id}", data=edit_data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db_rows(
                "SELECT worker, client, date, time, status FROM shifts "
                "WHERE id = ?", (shift_id,),
            )[0],
            ("Bea", "Client B", "2026-11-04", "09:15-11:45", "done"),
        )

    def test_legacy_unlocked_shift_remains_modifiable(self):
        shift_id = self.insert_target_shift()
        response = self.client.post(
            "/move_shift",
            data={"shift_id": str(shift_id), "date": "2026-11-03"},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/edit_shift/{shift_id}", data={
            "workers": ["Cara"], "client": "Client C",
            "date": "2026-11-05", "start_hour": "07",
            "start_minute": "30", "end_hour": "09",
            "end_minute": "00", "status": "in_progress",
            "return_to": "/week?start=2026-11-02",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db_rows(
                "SELECT worker, client, date, time, status FROM shifts "
                "WHERE id = ?", (shift_id,),
            )[0],
            ("Cara", "Client C", "2026-11-05",
             "07:30-09:00", "in_progress"),
        )

    def test_legacy_move_and_lock_are_serialized(self):
        self.seed_history()
        batch_id = self.generate()
        shift_id = self.insert_target_shift()
        owner_token, csrf_token = self.session_security()
        move_client = app.app.test_client()
        lock_client = app.app.test_client()
        for client in (move_client, lock_client):
            self.login(
                client, owner_token=owner_token, csrf_token=csrf_token,
            )
        barrier = threading.Barrier(2)

        def move_request():
            barrier.wait(timeout=5)
            return move_client.post("/move_shift", data={
                "shift_id": str(shift_id), "date": "2026-11-03",
            }).status_code

        def lock_request():
            barrier.wait(timeout=5)
            return lock_client.post(
                f"/plan/review/{batch_id}/shift/{shift_id}/lock",
                data={"csrf_token": csrf_token},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            move_future = executor.submit(move_request)
            lock_future = executor.submit(lock_request)
            move_status = move_future.result(timeout=10)
            lock_status = lock_future.result(timeout=10)

        self.assertEqual(lock_status, 302)
        self.assertIn(move_status, (200, 409))
        final_date = self.db_rows(
            "SELECT date FROM shifts WHERE id = ?", (shift_id,),
        )[0][0]
        self.assertEqual(
            final_date,
            "2026-11-03" if move_status == 200 else self.TARGET_WEEK,
        )
        self.assertEqual(
            self.db_rows(
                "SELECT COUNT(*) FROM shift_locks WHERE shift_id = ?",
                (shift_id,),
            )[0][0],
            1,
        )

    def test_cancelled_draft_cannot_be_applied(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        response = self.client.post(
            f"/plan/review/{batch_id}/cancel",
            data={"csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 302)
        marker = self.db_rows(
            "SELECT applied_at FROM shift_proposal_batches WHERE id = ?",
            (batch_id,),
        )[0][0]
        self.assertTrue(marker.startswith(app.AP_BATCH_CANCELLED_PREFIX))
        self.assertEqual(self.apply(batch_id, [proposal_id]).status_code, 409)
        self.assertEqual(self.target_shifts(), [])

    def test_applied_batch_cannot_be_applied_or_edited_again(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        self.apply(batch_id, [proposal_id])
        self.assertEqual(self.apply(batch_id, [proposal_id]).status_code, 409)
        self.assertEqual(
            self.update(batch_id, proposal_id, action="pending").status_code,
            409,
        )
        self.assertEqual(len(self.target_shifts()), 1)

    def test_application_has_no_invoice_email_or_pdf_side_effects(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        tables = (
            "invoice_records", "manual_invoice_drafts",
            "invoice_email_queue", "invoice_email_logs",
        )
        before = {
            table: self.db_rows(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in tables
        }
        self.apply(batch_id, [proposal_id])
        after = {
            table: self.db_rows(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in tables
        }
        self.assertEqual(after, before)
        self.assertEqual(len(self.target_shifts()), 1)

    def test_phase6_week_generation_entry_is_admin_only(self):
        response = self.client.get(f"/week?start={self.TARGET_WEEK}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-auto-plan-entry="week"', response.data)
        self.assertIn(b'href="/plan/generate"', response.data)

        worker = app.app.test_client()
        self.login(worker, username="Aida", role="worker")
        response = worker.get(f"/week?start={self.TARGET_WEEK}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'data-auto-plan-entry="week"', response.data)

    def test_phase6_dashboard_and_month_offer_coherent_admin_access(self):
        dashboard = self.client.get("/")
        month = self.client.get("/month?year=2026&month=11")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(month.status_code, 200)
        self.assertIn(b'data-auto-plan-entry="dashboard"', dashboard.data)
        self.assertIn(b'data-auto-plan-entry="month"', month.data)
        self.assertIn(b'Smart automatic planning', dashboard.data)

        worker = app.app.test_client()
        self.login(worker, username="Aida", role="worker")
        self.assertNotIn(
            b'data-auto-plan-entry="dashboard"', worker.get("/").data,
        )
        self.assertEqual(worker.get("/month").status_code, 302)

    def test_phase6_generate_form_has_all_controls_and_csrf(self):
        response = self.client.get("/plan/generate")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="target_week_start"', response.data)
        for weeks in (2, 4, 8):
            self.assertIn(
                f'name="lookback_weeks" value="{weeks}"'.encode(),
                response.data,
            )
        self.assertIn(b'value="8" checked', response.data)
        self.assertIn(b'name="include_exceptional"', response.data)
        self.assertIn(b'name="csrf_token"', response.data)
        self.assertIn(b'Nothing is added until you approve it', response.data)

    def test_phase6_review_is_seven_day_grid_with_clear_states(self):
        self.seed_history()
        batch_id = self.generate()
        response = self.client.get(f"/plan/review?batch={batch_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="apr-week-grid"', response.data)
        self.assertEqual(response.data.count(b'class="apr-day"'), 7)
        self.assertIn(b'data-status="proposed"', response.data)
        self.assertIn(b'data-manual="0"', response.data)
        self.assertIn(b'Valid proposal', response.data)
        self.assertIn(b'Unconfirmed draft', response.data)

    def test_phase6_review_exposes_warnings_and_blocking_conflicts(self):
        self.seed_history()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'vacation', ?, ?, '')",
            (self.TARGET_WEEK, self.TARGET_WEEK),
        )
        conn.commit(); conn.close()
        batch_id = self.generate()
        response = self.client.get(f"/plan/review?batch={batch_id}")
        self.assertIn(b'data-blocked="1"', response.data)
        self.assertIn(b'data-manual="1"', response.data)
        self.assertIn(b'data-issue-code="absence"', response.data)
        self.assertIn(b'data-blocking-count="1"', response.data)
        self.assertIn(b'Blocking conflict', response.data)

    def test_phase6_individual_editor_and_status_actions_are_wired(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        response = self.client.get(f"/plan/review?batch={batch_id}")
        expected_action = (
            f'/plan/review/{batch_id}/proposal/{proposal_id}/update'
        ).encode()
        self.assertIn(b'action="' + expected_action + b'"', response.data)
        for field in (
            b'name="workers"', b'name="client"', b'name="target_date"',
            b'name="start_time"', b'name="end_time"',
        ):
            self.assertIn(field, response.data)
        for action in (b'save', b'accept', b'reject', b'pending'):
            self.assertIn(b'name="action" value="' + action + b'"', response.data)

        self.update(batch_id, proposal_id, client="Client B")
        self.assertEqual(self.proposals(batch_id)[0][3], "Client B")
        self.update(batch_id, proposal_id, action="accept")
        self.assertEqual(self.proposals(batch_id)[0][5], "accepted")
        self.update(batch_id, proposal_id, action="reject")
        self.assertEqual(self.proposals(batch_id)[0][5], "rejected")

    def test_phase6_multi_selection_confirmation_and_double_submit_guard(self):
        self.seed_history()
        self.seed_history("Bea", "Client B", "11:00-13:00")
        batch_id = self.generate()
        proposal_ids = [row[0] for row in self.proposals(batch_id)]
        for proposal_id in proposal_ids:
            self.accept(batch_id, proposal_id)
        response = self.client.get(f"/plan/review?batch={batch_id}")
        self.assertGreaterEqual(
            response.data.count(b'form="ap-apply-form"'), 2,
        )
        self.assertIn(b'id="ap-bulk-form"', response.data)
        self.assertIn(b'id="ap-apply-form"', response.data)
        self.assertIn(b'id="ap-select-all-admissible"', response.data)
        self.assertIn(b'name="confirm_apply" value="0"', response.data)
        self.assertIn(b'data-confirm-template=', response.data)
        self.assertIn(b'data-confirm-dates=', response.data)
        self.assertIn(b'data-display-date="02/11/2026"', response.data)
        self.assertIn(b'String.fromCharCode(10, 10)', response.data)
        self.assertIn(b"applyForm.dataset.submitting === '1'", response.data)
        self.assertIn(b"confirmApply.value = '1'", response.data)
        self.assertIn(b'applyButton.disabled = true', response.data)
        self.assertIn(b'Only accepted and checked proposals', response.data)

    def test_phase6_locked_shift_is_visible_and_not_draggable_in_week(self):
        self.seed_history()
        batch_id = self.generate()
        shift_id = self.insert_target_shift(
            worker="Bea", client="Client B", hours="12:00-13:00",
        )
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
            "VALUES (?, '2026-11-01 12:00:00', 'admin')", (shift_id,),
        )
        conn.commit(); conn.close()

        review = self.client.get(f"/plan/review?batch={batch_id}")
        week = self.client.get(f"/week?start={self.TARGET_WEEK}")
        self.assertIn(
            f'data-existing-shift-id="{shift_id}" data-locked="1"'.encode(),
            review.data,
        )
        self.assertIn(
            f'data-shift-id="{shift_id}" data-locked="1" draggable="false"'.encode(),
            week.data,
        )
        self.assertIn(b'Locked', week.data)

    def test_phase6_applied_review_returns_to_populated_week_calendar(self):
        self.seed_history()
        batch_id = self.generate()
        proposal_id = self.proposals(batch_id)[0][0]
        self.accept(batch_id, proposal_id)
        response = self.apply(batch_id, [proposal_id])
        self.assertEqual(response.status_code, 302)

        review = self.client.get(response.headers["Location"])
        expected_week = f'/week?start={self.TARGET_WEEK}'.encode()
        self.assertIn(b'data-return-to-week', review.data)
        self.assertIn(b'href="' + expected_week + b'"', review.data)
        self.assertIn(b'Client A', review.data)

        week = self.client.get(expected_week.decode())
        self.assertEqual(week.status_code, 200)
        self.assertIn(b'Client A', week.data)
        self.assertIn(b'08:00-10:00', week.data)

        month = self.client.get("/month?year=2026&month=11")
        self.assertEqual(month.status_code, 200)
        self.assertIn(b'Client A', month.data)
        self.assertIn(b'08:00-10:00', month.data)
        self.assertEqual(len(self.target_shifts()), 1)

    def test_phase7_regression_smoke_for_planning_invoice_email_and_pdfs(self):
        invoice_number = "AP-P7-SMOKE"
        self.seed_history()
        conn = app.get_conn()
        try:
            conn.execute(
                "INSERT INTO client_invoice_profiles "
                "(client_name, email, client_type, hourly_rate, custom_address, "
                "vat_rate, invoice_service_description) "
                "VALUES (?, ?, 'private', 28, '', 8, ?) "
                "ON CONFLICT(client_name) DO UPDATE SET "
                "email=excluded.email, hourly_rate=excluded.hourly_rate, "
                "vat_rate=excluded.vat_rate, "
                "invoice_service_description="
                "excluded.invoice_service_description",
                (
                    "Client A", "staging-only@example.invalid",
                    "Entretien et nettoyage des bureaux",
                ),
            )
            conn.execute(
                "INSERT INTO invoice_records "
                "(invoice_number, client_name, date_from, date_to, "
                "invoice_date, amount, vat_amount, total, paid, paid_date, "
                "sent, sent_date, deleted, source, service_description) "
                "VALUES (?, 'Client A', '2026-09-07', '2026-10-26', "
                "'2026-10-31', 448, 35.84, 483.84, 0, '', 0, '', 0, "
                "'auto', 'Entretien et nettoyage des bureaux')",
                (invoice_number,),
            )
            conn.commit()
        finally:
            conn.close()

        count_queries = {
            "queue": "SELECT COUNT(*) FROM invoice_email_queue",
            "logs": "SELECT COUNT(*) FROM invoice_email_logs",
        }
        before = {
            key: self.db_rows(query)[0][0]
            for key, query in count_queries.items()
        }
        try:
            with mock.patch.object(app, "_smtp_send") as smtp_send:
                html_paths = (
                    f"/week?start={self.TARGET_WEEK}",
                    "/month?year=2026&month=11",
                    "/invoices",
                    f"/invoices/view?invoice_number={invoice_number}",
                    "/invoices/manual",
                    f"/invoices/email?invoice_number={invoice_number}",
                )
                for path in html_paths:
                    with self.subTest(path=path):
                        response = self.client.get(path)
                        self.assertEqual(response.status_code, 200)
                        self.assertIn("text/html", response.content_type)

                pdf_paths = (
                    f"/invoices/preview_pdf?invoice_number={invoice_number}",
                    f"/week_pdf?start={self.TARGET_WEEK}",
                    "/month_pdf?year=2026&month=11",
                )
                for path in pdf_paths:
                    with self.subTest(path=path):
                        response = self.client.get(path)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.mimetype, "application/pdf")
                        self.assertTrue(response.data.startswith(b"%PDF"))
                smtp_send.assert_not_called()

            after = {
                key: self.db_rows(query)[0][0]
                for key, query in count_queries.items()
            }
            self.assertEqual(after, before)
            stored = self.db_rows(
                "SELECT amount, vat_amount, total, paid, sent "
                "FROM invoice_records WHERE invoice_number = ?",
                (invoice_number,),
            )
            self.assertEqual(stored, [(448.0, 35.84, 483.84, 0, 0)])
        finally:
            conn = app.get_conn()
            try:
                conn.execute(
                    "DELETE FROM invoice_records WHERE invoice_number = ?",
                    (invoice_number,),
                )
                conn.execute(
                    "DELETE FROM client_invoice_profiles "
                    "WHERE client_name = 'Client A'",
                )
                conn.commit()
            finally:
                conn.close()

    def test_phase6_copy_exists_in_all_five_languages(self):
        required = {
            "auto_plan_generation_help", "auto_plan_draft_not_confirmed",
            "auto_plan_valid_proposal", "auto_plan_blocking_conflict",
            "auto_plan_apply_count_confirm", "auto_plan_return_week",
            "auto_plan_select_all_admissible",
            "auto_plan_confirmation_dates",
            "auto_plan_confirmation_required",
        }
        for language in ("bos", "fr", "en", "de", "pt"):
            self.assertTrue(required.issubset(app.TRANSLATIONS[language]))
            self.assertTrue(all(app.TRANSLATIONS[language][key]
                                for key in required))


class AutoPlanningFeatureFlagTests(unittest.TestCase):
    TARGET_WEEK = "2026-11-02"
    HISTORY_DATES = Phase4DraftRoutesTests.HISTORY_DATES
    INVOICE_NUMBER = "AP-FLAG-SMOKE"
    LOGIN_USER = "auto-flag-admin"

    def setUp(self):
        previous_flag = app.app.config.get("AUTO_PLANNING_ENABLED", False)
        app.app.config["AUTO_PLANNING_ENABLED"] = False
        self.addCleanup(
            app.app.config.__setitem__,
            "AUTO_PLANNING_ENABLED", previous_flag,
        )
        conn = app.get_conn()
        try:
            for table in (
                "shift_proposals", "shift_proposal_batches", "shift_locks",
                "shifts", "absences", "leave_requests", "holidays",
                "workers", "clients",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "DELETE FROM invoice_records WHERE invoice_number = ?",
                (self.INVOICE_NUMBER,),
            )
            conn.execute(
                "DELETE FROM client_invoice_profiles "
                "WHERE client_name = 'Client A'"
            )
            conn.execute("DELETE FROM users WHERE username = ?", (self.LOGIN_USER,))
            conn.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, 'admin')",
                (self.LOGIN_USER, app.hash_password("flag-test-password")),
            )
            conn.execute(
                "INSERT INTO workers (name, contract_end_date) "
                "VALUES ('Aida', '')"
            )
            conn.execute(
                "INSERT INTO clients (name, contract_from, contract_to) "
                "VALUES ('Client A', '2026-01-01', '2026-12-31')"
            )
            conn.commit()
        finally:
            conn.close()
        self.addCleanup(self._cleanup_fixture_rows)
        self.client = app.app.test_client()
        self.login_session(self.client)

    def _cleanup_fixture_rows(self):
        conn = app.get_conn()
        try:
            conn.execute(
                "DELETE FROM invoice_records WHERE invoice_number = ?",
                (self.INVOICE_NUMBER,),
            )
            conn.execute(
                "DELETE FROM client_invoice_profiles "
                "WHERE client_name = 'Client A'"
            )
            conn.execute("DELETE FROM users WHERE username = ?", (self.LOGIN_USER,))
            conn.commit()
        finally:
            conn.close()

    def login_session(self, client):
        with client.session_transaction() as flask_session:
            flask_session["user"] = "admin"
            flask_session["role"] = "admin"
            flask_session["lang"] = "en"

    def db_rows(self, query, params=()):
        conn = app.get_conn()
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def insert_shift(self, day="2026-11-02", hours="08:00-10:00"):
        conn = app.get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES ('Aida', 'Client A', ?, ?, 'planned')",
                (day, hours),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_history(self):
        conn = app.get_conn()
        try:
            conn.executemany(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES ('Aida', 'Client A', ?, '08:00-10:00', 'done')",
                [(day,) for day in self.HISTORY_DATES],
            )
            conn.commit()
        finally:
            conn.close()

    def test_flag_parser_is_disabled_by_default_and_strictly_opt_in(self):
        for value in ("true", "TRUE", " 1 ", "yes", "Yes", "on", "ON"):
            with self.subTest(value=value):
                self.assertTrue(app.parse_auto_planning_enabled(value))
        for value in (None, "", "false", "0", "no", "off", "enabled", "2"):
            with self.subTest(value=value):
                self.assertFalse(app.parse_auto_planning_enabled(value))

    def test_disabled_ui_hides_every_auto_planning_entry(self):
        for path in (
            "/", f"/week?start={self.TARGET_WEEK}",
            "/month?year=2026&month=11",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"data-auto-plan-entry", response.data)
                self.assertNotIn(b'href="/plan/generate"', response.data)
        week = self.client.get(f"/week?start={self.TARGET_WEEK}")
        self.assertIn(b"/week_pdf?start=2026-11-02", week.data)

    def test_disabled_plan_routes_are_404_and_create_nothing(self):
        shift_id = self.insert_shift()
        before = {
            table: self.db_rows(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in (
                "shift_proposal_batches", "shift_proposals", "shift_locks",
                "shifts",
            )
        }
        routes = (
            ("GET", "/plan/generate"),
            ("POST", "/plan/generate"),
            ("GET", "/plan/review?batch=1"),
            ("POST", "/plan/review/1/proposal/1/update"),
            ("POST", "/plan/review/1/bulk-accept"),
            ("POST", "/plan/review/1/apply"),
            ("POST", "/plan/review/1/cancel"),
            ("POST", f"/plan/review/1/shift/{shift_id}/lock"),
            ("POST", f"/plan/review/1/shift/{shift_id}/unlock"),
            ("GET", "/plan/not-a-real-route"),
        )
        for method, path in routes:
            with self.subTest(method=method, path=path):
                self.assertEqual(
                    self.client.open(path, method=method).status_code, 404
                )
        after = {
            table: self.db_rows(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in before
        }
        self.assertEqual(after, before)

    def test_disabled_login_does_not_create_draft_or_csrf_tokens(self):
        client = app.app.test_client()
        response = client.post("/login", data={
            "username": self.LOGIN_USER,
            "password": "flag-test-password",
        })
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as flask_session:
            self.assertNotIn(app.AP_DRAFT_SESSION_KEY, flask_session)
            self.assertNotIn(app.AP_CSRF_SESSION_KEY, flask_session)

    def test_disabled_unlocked_legacy_shift_remains_fully_editable(self):
        shift_id = self.insert_shift()
        moved = self.client.post("/move_shift", data={
            "shift_id": str(shift_id), "date": "2026-11-03",
        })
        self.assertEqual(moved.status_code, 200)

        edited = self.client.post(f"/edit_shift/{shift_id}", data={
            "workers": ["Aida"], "client": "Client A",
            "date": "2026-11-04", "start_hour": "09",
            "start_minute": "00", "end_hour": "11",
            "end_minute": "00", "status": "done", "return_to": "/week",
        })
        self.assertEqual(edited.status_code, 302)
        self.assertEqual(
            self.db_rows(
                "SELECT date, time, status FROM shifts WHERE id = ?",
                (shift_id,),
            ),
            [("2026-11-04", "09:00-11:00", "done")],
        )

        deleted = self.client.post(f"/delete_shift/{shift_id}")
        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shifts WHERE id = ?", (shift_id,))[0][0],
            0,
        )
        self.assertEqual(
            self.db_rows(
                "SELECT COUNT(*) FROM shift_locks WHERE shift_id = ?",
                (shift_id,),
            )[0][0],
            0,
        )

    def test_disabled_locked_shift_remains_protected_server_side(self):
        shift_id = self.insert_shift()
        conn = app.get_conn()
        try:
            conn.execute(
                "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
                "VALUES (?, '2026-11-01 12:00:00', 'admin')",
                (shift_id,),
            )
            conn.commit()
        finally:
            conn.close()
        before = self.db_rows(
            "SELECT worker, client, date, time, status FROM shifts WHERE id = ?",
            (shift_id,),
        )

        self.assertEqual(self.client.post("/move_shift", data={
            "shift_id": str(shift_id), "date": "2026-11-03",
        }).status_code, 409)
        self.assertEqual(self.client.post(f"/edit_shift/{shift_id}", data={
            "workers": ["Aida"], "client": "Client A",
            "date": "2026-11-04", "start_hour": "09",
            "start_minute": "00", "end_hour": "11",
            "end_minute": "00", "status": "done", "return_to": "/week",
        }).status_code, 302)
        self.assertEqual(
            self.client.post(f"/delete_shift/{shift_id}").status_code, 302
        )
        self.assertEqual(
            self.db_rows(
                "SELECT worker, client, date, time, status FROM shifts WHERE id = ?",
                (shift_id,),
            ),
            before,
        )
        self.assertEqual(
            self.db_rows(
                "SELECT COUNT(*) FROM shift_locks WHERE shift_id = ?",
                (shift_id,),
            )[0][0],
            1,
        )
        week = self.client.get(f"/week?start={self.TARGET_WEEK}")
        self.assertIn(f'data-shift-id="{shift_id}" data-locked="1"'.encode(), week.data)
        self.assertNotIn(b"data-auto-plan-entry", week.data)

    def test_disabled_existing_modules_and_pdfs_remain_available(self):
        self.seed_history()
        conn = app.get_conn()
        try:
            conn.execute(
                "INSERT INTO client_invoice_profiles "
                "(client_name, email, client_type, hourly_rate, custom_address, "
                "vat_rate, invoice_service_description) "
                "VALUES ('Client A', 'flag-test@example.invalid', 'private', "
                "28, '', 8, 'Entretien et nettoyage des bureaux')"
            )
            conn.execute(
                "INSERT INTO invoice_records "
                "(invoice_number, client_name, date_from, date_to, "
                "invoice_date, amount, vat_amount, total, paid, paid_date, "
                "sent, sent_date, deleted, source, service_description) "
                "VALUES (?, 'Client A', '2026-09-07', '2026-10-26', "
                "'2026-10-31', 448, 35.84, 483.84, 0, '', 0, '', 0, "
                "'auto', 'Entretien et nettoyage des bureaux')",
                (self.INVOICE_NUMBER,),
            )
            conn.commit()
        finally:
            conn.close()
        queue_before = self.db_rows(
            "SELECT COUNT(*) FROM invoice_email_queue"
        )[0][0]
        logs_before = self.db_rows(
            "SELECT COUNT(*) FROM invoice_email_logs"
        )[0][0]

        with mock.patch.object(app, "_smtp_send") as smtp_send:
            html_paths = (
                f"/week?start={self.TARGET_WEEK}",
                "/month?year=2026&month=11", "/clients", "/workers",
                "/invoices", "/invoices/manual",
                f"/invoices/view?invoice_number={self.INVOICE_NUMBER}",
                f"/invoices/email?invoice_number={self.INVOICE_NUMBER}",
            )
            for path in html_paths:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("text/html", response.content_type)
            for path in (
                f"/invoices/preview_pdf?invoice_number={self.INVOICE_NUMBER}",
                f"/week_pdf?start={self.TARGET_WEEK}",
                "/month_pdf?year=2026&month=11",
            ):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, "application/pdf")
                    self.assertTrue(response.data.startswith(b"%PDF"))
            smtp_send.assert_not_called()

        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM invoice_email_queue")[0][0],
            queue_before,
        )
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM invoice_email_logs")[0][0],
            logs_before,
        )

    def test_enable_disable_reenable_preserves_and_reopens_same_draft(self):
        self.seed_history()
        app.app.config["AUTO_PLANNING_ENABLED"] = "yes"
        form = self.client.get("/plan/generate")
        self.assertEqual(form.status_code, 200)
        with self.client.session_transaction() as flask_session:
            owner_before = flask_session[app.AP_DRAFT_SESSION_KEY]
            csrf_token = flask_session[app.AP_CSRF_SESSION_KEY]
        generated = self.client.post("/plan/generate", data={
            "target_week_start": self.TARGET_WEEK,
            "lookback_weeks": "8", "csrf_token": csrf_token,
        })
        self.assertEqual(generated.status_code, 302)
        location = generated.headers["Location"]
        batch_count = self.db_rows(
            "SELECT COUNT(*) FROM shift_proposal_batches"
        )[0][0]
        proposal_count = self.db_rows(
            "SELECT COUNT(*) FROM shift_proposals"
        )[0][0]
        self.assertGreater(proposal_count, 0)

        app.app.config["AUTO_PLANNING_ENABLED"] = "off"
        self.assertEqual(self.client.get(location).status_code, 404)
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposal_batches")[0][0],
            batch_count,
        )
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposals")[0][0],
            proposal_count,
        )
        with self.client.session_transaction() as flask_session:
            self.assertEqual(
                flask_session[app.AP_DRAFT_SESSION_KEY], owner_before
            )

        app.app.config["AUTO_PLANNING_ENABLED"] = "on"
        reopened = self.client.get(location)
        self.assertEqual(reopened.status_code, 200)
        self.assertIn(b"Client A", reopened.data)
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposal_batches")[0][0],
            batch_count,
        )


if __name__ == "__main__":
    unittest.main()
