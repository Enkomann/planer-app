"""Phase 3 tests — schedule-based projection, availability warning,
combined generator precedence, and the /plan/generate wiring that
persists the ``source`` tag on each saved proposal.
"""
import json
import os
import shutil
import tempfile
import unittest

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-phase3-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "phase3-test-only")
os.environ["AUTO_PLANNING_MODE"] = "write_enabled"
os.environ["AUTO_PLANNING_ENABLED"] = "true"

import app  # noqa: E402

app.app.config["AUTO_PLANNING_MODE"] = "write_enabled"
app.app.config["AUTO_PLANNING_ENABLED"] = True
try:
    app.init_db()
except Exception:
    pass


def tearDownModule():
    shutil.rmtree(TEST_STORAGE, ignore_errors=True)
    try:
        os.rmdir(TEST_TEMP_ROOT)
    except OSError:
        pass


# 2026-10-26 is a Monday (used as target week start throughout).
MONDAY = "2026-10-26"
TUESDAY = "2026-10-27"


def _reset_tables():
    conn = app.get_conn()
    for table in ("shift_proposals", "shift_proposal_batches",
                  "shift_locks", "shifts", "absences", "leave_requests",
                  "holidays", "workers", "clients", "worker_colors"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _seed_client(name, schedule_json="", cfrom="", cto=""):
    conn = app.get_conn()
    conn.execute(
        "INSERT INTO clients (name, address, cleaning_frequency, "
        "contract_from, contract_to) VALUES (?,?,?,?,?)",
        (name, "Rue 1", schedule_json, cfrom, cto),
    )
    conn.commit()
    conn.close()


def _seed_worker(name, availability=""):
    conn = app.get_conn()
    conn.execute(
        "INSERT INTO workers (name, contract_end_date, availability) "
        "VALUES (?, '', ?)", (name, availability),
    )
    conn.commit()
    conn.close()


class ScheduleProjectorTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def _schedule_only(self, target=MONDAY):
        conn = app.get_conn()
        try:
            return app.ap_project_from_schedule(conn, target)
        finally:
            conn.close()

    def test_weekly_schedule_projects_to_target_week(self):
        _seed_worker("Aida")
        _seed_client("Alpha", json.dumps([{
            "kind": "weekly", "weekday": 1,  # Tuesday
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        props, scheduled = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["target_date"], TUESDAY)
        self.assertEqual(props[0]["client"], "Alpha")
        self.assertEqual(props[0]["worker"], "Aida")
        self.assertEqual(props[0]["source"], "schedule")
        self.assertEqual(scheduled, {"Alpha"})

    def test_multiple_visits_per_week(self):
        _seed_worker("Aida")
        _seed_client("Beta", json.dumps([
            {"kind": "weekly", "weekday": 0, "start": "08:00",
             "end": "10:00", "preferred_workers": ["Aida"]},
            {"kind": "weekly", "weekday": 3, "start": "14:00",
             "end": "16:00", "preferred_workers": ["Aida"]},
        ]))
        props, _ = self._schedule_only()
        self.assertEqual(len(props), 2)
        dates = sorted(p["target_date"] for p in props)
        self.assertEqual(dates[0], MONDAY)
        self.assertEqual(dates[1], "2026-10-29")  # Thursday

    def test_biweekly_due_on_anchor_week(self):
        _seed_worker("Aida")
        _seed_client("Gamma", json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-10-26",  # the target Monday
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["recurrence_period"], 2)

    def test_biweekly_skipped_on_off_week(self):
        _seed_worker("Aida")
        _seed_client("Delta", json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-10-19",  # one week BEFORE target
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only()
        self.assertEqual(props, [])  # off-cycle

    def test_monthly_first_week_match(self):
        # 2026-11-02 is first Monday of November.
        _seed_worker("Aida")
        _seed_client("Epsilon", json.dumps([{
            "kind": "monthly", "weekday": 0, "monthly_week": 1,
            "start": "08:00", "end": "10:00",
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only(target="2026-11-02")
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["recurrence_period"], 4)

    def test_monthly_last_week_match(self):
        _seed_worker("Aida")
        _seed_client("Zeta", json.dumps([{
            "kind": "monthly", "weekday": 0, "monthly_week": -1,
            "start": "08:00", "end": "10:00",
            "preferred_workers": ["Aida"],
        }]))
        # 2026-10-26 is the LAST Monday of October.
        props, _ = self._schedule_only(target=MONDAY)
        self.assertEqual(len(props), 1)

    def test_custom_interval_due(self):
        _seed_worker("Aida")
        _seed_client("Eta", json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 3,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-10-05",  # 3 weeks before target
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only(target=MONDAY)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["recurrence_period"], 3)

    def test_year_boundary(self):
        # anchor 2026-12-28 (Monday) + every 3 weeks → 2027-01-18
        _seed_worker("Aida")
        _seed_client("Theta", json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 3,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-12-28",
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only(target="2027-01-18")
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["target_date"], "2027-01-18")

    def test_contract_window_filters(self):
        _seed_worker("Aida")
        _seed_client("Iota", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]), cfrom="2027-01-01")
        props, _ = self._schedule_only(target=MONDAY)
        self.assertEqual(props, [])

    def test_unknown_preferred_worker_surfaces_warning(self):
        # Aida was deleted since the schedule was saved.
        _seed_client("Kappa", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["GhostA", "GhostB"],
        }]))
        props, _ = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["worker"], "")  # for manual pick
        codes = [i["code"] for i in props[0]["issues"]]
        self.assertIn("preferred_worker_unknown", codes)

    def test_corrupt_schedule_json_surfaces_blocking_issue(self):
        _seed_client("Lambda", "not{json")
        props, scheduled = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["client"], "Lambda")
        codes = [i["code"] for i in props[0]["issues"]]
        self.assertIn("schedule_invalid", codes)
        # Client is in the scheduled-set so history projector would
        # not duplicate for it.
        self.assertIn("Lambda", scheduled)


class CombinedGeneratorPrecedenceTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def test_schedule_takes_precedence_over_history(self):
        _seed_worker("Aida")
        _seed_client("Mu", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        # Seed enough history for the engine to also project Mu.
        conn = app.get_conn()
        for d in ("2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25",
                  "2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22"):
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, '08:00-11:00', 'done')",
                ("Aida", "Mu", d),
            )
        conn.commit()
        conn.close()

        conn = app.get_conn()
        try:
            merged = app.ap_generate_combined_proposals(
                conn, MONDAY, lookback_weeks=8,
                include_exceptional=False,
            )
        finally:
            conn.close()
        same_date_props = [p for p in merged if p["client"] == "Mu"
                           and p["target_date"] == TUESDAY]
        self.assertEqual(len(same_date_props), 1)
        self.assertEqual(same_date_props[0]["source"], "schedule")

    def test_history_still_runs_for_non_scheduled_clients(self):
        _seed_worker("Mila")
        _seed_client("Nu")  # no schedule
        conn = app.get_conn()
        for d in ("2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25",
                  "2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22"):
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, '09:00-11:00', 'done')",
                ("Mila", "Nu", d),
            )
        conn.commit()
        conn.close()
        conn = app.get_conn()
        try:
            merged = app.ap_generate_combined_proposals(
                conn, MONDAY, lookback_weeks=8,
            )
        finally:
            conn.close()
        hist = [p for p in merged if p["client"] == "Nu"]
        self.assertTrue(len(hist) >= 1)
        self.assertEqual(hist[0]["source"], "history")


class AvailabilityWarningTests(unittest.TestCase):
    """``ap_run_checks`` must surface outside_working_hours whenever
    a WORKER is already assigned to a proposal and the stored
    availability rejects the slot. We construct proposals manually
    so the smart-pick (which legitimately drops such workers) does
    not pre-filter them out of the test subject."""

    def setUp(self):
        _reset_tables()

    def _check_with_av(self, av):
        _seed_worker("Aida", availability=av)
        _seed_client("Omicron")
        proposal = {
            "target_date": TUESDAY,
            "time": "13:00-15:00",
            "client": "Omicron", "worker": "Aida",
            "source": "history",
            "recurrence_period": 1, "confidence": 1.0,
            "occurrences": 1, "duration_hours": 2.0,
            "requires_manual_validation": False,
            "issues": [],
        }
        conn = app.get_conn()
        try:
            return app.ap_run_checks(conn, [proposal], MONDAY)
        finally:
            conn.close()

    def test_shift_inside_working_hours_no_warning(self):
        checked = self._check_with_av(json.dumps({
            "tue": [{"start": "08:00", "end": "17:00"}],
        }))
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertNotIn("outside_working_hours", codes)

    def test_shift_outside_working_hours_warns(self):
        checked = self._check_with_av(json.dumps({
            "tue": [{"start": "08:00", "end": "12:00"}],
        }))
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertIn("outside_working_hours", codes)

    def test_day_off_warns(self):
        checked = self._check_with_av(json.dumps({"tue": []}))
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertIn("outside_working_hours", codes)

    def test_empty_availability_no_warning(self):
        checked = self._check_with_av("")
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertNotIn("outside_working_hours", codes)

    def test_absent_preferred_worker_falls_back_to_manual_pick(self):
        """P1-A: with only one preferred worker who is absent on the
        target date, the projector must NOT auto-assign them. The
        proposal appears with worker='' so admin picks manually."""
        _seed_worker("Aida")
        _seed_client("Pi", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'sick', ?, ?, '')",
            (TUESDAY, TUESDAY),
        )
        conn.commit()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
        finally:
            conn.close()
        pi = [p for p in props if p["client"] == "Pi"]
        self.assertEqual(len(pi), 1)
        self.assertEqual(pi[0]["worker"], "")

    def test_available_preferred_wins_over_absent_one(self):
        """P1-A: among several preferred workers, pick the first one
        who is NOT absent / on leave."""
        _seed_worker("Aida")
        _seed_worker("Mila")
        _seed_client("Rho", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida", "Mila"],
        }]))
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'sick', ?, ?, '')",
            (TUESDAY, TUESDAY),
        )
        conn.commit()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
        finally:
            conn.close()
        rho = [p for p in props if p["client"] == "Rho"]
        self.assertEqual(len(rho), 1)
        self.assertEqual(rho[0]["worker"], "Mila")


class CorruptAvailabilityTests(unittest.TestCase):
    """P1-C: malformed workers.availability must surface a BLOCKING
    issue, never silently drop the working-hours check."""

    def setUp(self):
        _reset_tables()

    def test_corrupt_availability_adds_blocking_issue(self):
        """When a worker is already assigned to a proposal (manually
        or from history) and their stored availability JSON is
        corrupt, ap_run_checks must surface a BLOCKING issue."""
        _seed_worker("Aida", availability="not{json")
        _seed_client("Sigma")
        proposal = {
            "target_date": TUESDAY,
            "time": "08:00-11:00",
            "client": "Sigma", "worker": "Aida",
            "source": "history",
            "recurrence_period": 1, "confidence": 1.0,
            "occurrences": 1, "duration_hours": 3.0,
            "requires_manual_validation": False,
            "issues": [],
        }
        conn = app.get_conn()
        try:
            checked = app.ap_run_checks(conn, [proposal], MONDAY)
        finally:
            conn.close()
        codes = [i["code"] for i in checked[0]["issues"]]
        self.assertIn("worker_availability_invalid", codes)
        self.assertTrue(checked[0]["has_blocking_issues"])


class OutsideHoursConfirmationTests(unittest.TestCase):
    """P1-B: outside_working_hours requires explicit admin confirm
    before apply. Field edits reset confirmation."""

    def setUp(self):
        _reset_tables()

    def _build_outside_proposal(self):
        # Note: the smart worker picker in ap_project_from_schedule
        # intentionally skips Aida here because the configured
        # availability (08–12) doesn't match a 13–15 shift. The
        # outside_working_hours WARNING case is therefore the one
        # where an admin manually assigns Aida AFTER review (or an
        # old history proposal recovers her). To test the warning +
        # confirm path we construct the proposal explicitly and run
        # it through ap_run_checks, which is exactly what every
        # apply pre-flight does.
        _seed_worker("Aida", availability=json.dumps({
            "tue": [{"start": "08:00", "end": "12:00"}],
        }))
        _seed_client("Tau")
        return app.get_conn()

    def _manual_proposal(self):
        return {
            "target_date": TUESDAY,
            "time": "13:00-15:00",
            "client": "Tau", "worker": "Aida",
            "source": "history",
            "recurrence_period": 1, "confidence": 1.0,
            "occurrences": 1, "duration_hours": 2.0,
            "requires_manual_validation": False,
            "issues": [],
        }

    def test_unconfirmed_outside_hours_blocks(self):
        conn = self._build_outside_proposal()
        try:
            checked = app.ap_run_checks(
                conn, [self._manual_proposal()], MONDAY,
            )
        finally:
            conn.close()
        tau = checked[0]
        codes = {i["code"] for i in tau["issues"]}
        self.assertIn("outside_working_hours", codes)
        self.assertIn("outside_working_hours_unconfirmed", codes)
        self.assertTrue(tau["has_blocking_issues"])

    def test_confirmed_outside_hours_does_not_block(self):
        conn = self._build_outside_proposal()
        prop = self._manual_proposal()
        prop["outside_hours_confirmed"] = 1
        try:
            checked = app.ap_run_checks(conn, [prop], MONDAY)
        finally:
            conn.close()
        tau = checked[0]
        codes = {i["code"] for i in tau["issues"]}
        self.assertIn("outside_working_hours", codes)
        self.assertNotIn("outside_working_hours_unconfirmed", codes)

    def test_field_edit_resets_confirmation(self):
        """ap_update_proposal_fields must set outside_hours_confirmed=0.
        """
        conn = self._build_outside_proposal()
        try:
            batch_id = app.ap_create_draft(
                conn, "admin", "owner-token", MONDAY, 8, False,
                [self._manual_proposal()],
            )
            conn.commit()
            cur = conn.cursor()
            row = cur.execute(
                "SELECT id FROM shift_proposals WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            pid = int(row[0])
            cur.execute(
                "UPDATE shift_proposals SET outside_hours_confirmed=1 "
                "WHERE id=?", (pid,),
            )
            conn.commit()
            app.ap_update_proposal_fields(
                conn, {"id": batch_id}, pid, {
                    "worker": "Aida", "client": "Tau",
                    "target_date": TUESDAY, "time": "09:00-11:00",
                    "is_locked": False,
                },
            )
            conn.commit()
            flag = cur.execute(
                "SELECT outside_hours_confirmed FROM shift_proposals "
                "WHERE id=?", (pid,),
            ).fetchone()[0]
            self.assertEqual(flag, 0)
        finally:
            conn.close()


class SmartWorkerPickTests(unittest.TestCase):
    """P1-A round 2: auto-pick must also respect working hours,
    existing shifts, and intra-batch conflicts."""

    def setUp(self):
        _reset_tables()

    def test_skips_candidate_whose_availability_rejects_shift(self):
        _seed_worker("Aida", availability=json.dumps({
            "tue": [{"start": "08:00", "end": "12:00"}],
        }))  # Aida only works mornings
        _seed_worker("Mila")  # no availability constraint
        _seed_client("Phi", json.dumps([{
            "kind": "weekly", "weekday": 1,  # Tuesday
            "start": "14:00", "end": "16:00",  # afternoon
            "preferred_workers": ["Aida", "Mila"],
        }]))
        conn = app.get_conn()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
        finally:
            conn.close()
        self.assertEqual(props[0]["worker"], "Mila")

    def test_skips_candidate_with_existing_shift_overlap(self):
        _seed_worker("Aida")
        _seed_worker("Mila")
        _seed_client("Chi", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "09:00", "end": "11:00",
            "preferred_workers": ["Aida", "Mila"],
        }]))
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Aida', 'OtherClient', ?, '10:00-12:00', 'planned')",
            (TUESDAY,),
        )
        conn.commit()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
        finally:
            conn.close()
        self.assertEqual(props[0]["worker"], "Mila")

    def test_intra_batch_conflict_pushes_to_next_candidate(self):
        """Two schedule entries, same preferred list, same target
        date, overlapping times — the second must NOT reuse the
        first's worker."""
        _seed_worker("Aida")
        _seed_worker("Mila")
        _seed_client("Psi", json.dumps([
            {"kind": "weekly", "weekday": 1, "start": "09:00",
             "end": "11:00", "preferred_workers": ["Aida", "Mila"]},
        ]))
        _seed_client("Psi2", json.dumps([
            {"kind": "weekly", "weekday": 1, "start": "10:00",
             "end": "12:00", "preferred_workers": ["Aida", "Mila"]},
        ]))
        conn = app.get_conn()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
        finally:
            conn.close()
        workers_assigned = sorted(p["worker"] for p in props)
        self.assertEqual(workers_assigned, ["Aida", "Mila"])

    def test_all_candidates_unavailable_leaves_manual_pick(self):
        _seed_worker("Aida", availability=json.dumps({"tue": []}))
        _seed_worker("Mila", availability=json.dumps({"tue": []}))
        _seed_client("Omega", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida", "Mila"],
        }]))
        conn = app.get_conn()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
        finally:
            conn.close()
        self.assertEqual(props[0]["worker"], "")


class ConfirmHttpTests(unittest.TestCase):
    """Confirm / revoke outside-hours exception via the dedicated
    POST endpoint. Verifies the toggle, the field-edit reset, and
    that the endpoint does NOT bypass other blocking issues."""

    def setUp(self):
        _reset_tables()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "en"
        # Seed workers.availability so outside_working_hours triggers.
        _seed_worker("Aida", availability=json.dumps({
            "tue": [{"start": "08:00", "end": "12:00"}],
        }))
        _seed_client("Theta2", json.dumps([{
            "kind": "weekly", "weekday": 1,  # Tuesday
            "start": "13:00", "end": "15:00",
            "preferred_workers": ["Aida"],
        }]))

    def _csrf(self):
        resp = self.client.get("/plan/generate")
        assert resp.status_code == 200
        with self.client.session_transaction() as sess:
            return sess[app.AP_CSRF_SESSION_KEY]

    def _create_batch(self):
        self.client.post("/plan/generate", data={
            "target_week_start": MONDAY,
            "lookback_weeks": "8",
            "csrf_token": self._csrf(),
        })
        conn = app.get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM shift_proposal_batches "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            batch_id = int(row[0])
            prop_row = conn.execute(
                "SELECT id FROM shift_proposals WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            return batch_id, int(prop_row[0])
        finally:
            conn.close()

    def _flag(self, pid):
        conn = app.get_conn()
        try:
            return int(conn.execute(
                "SELECT outside_hours_confirmed FROM shift_proposals "
                "WHERE id=?", (pid,),
            ).fetchone()[0])
        finally:
            conn.close()

    def test_confirm_sets_flag(self):
        batch_id, pid = self._create_batch()
        resp = self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/confirm_outside_hours",
            data={"confirm": "1", "csrf_token": self._csrf()},
        )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(self._flag(pid), 1)

    def test_revoke_resets_flag(self):
        batch_id, pid = self._create_batch()
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/confirm_outside_hours",
            data={"confirm": "1", "csrf_token": self._csrf()},
        )
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/confirm_outside_hours",
            data={"confirm": "0", "csrf_token": self._csrf()},
        )
        self.assertEqual(self._flag(pid), 0)

    def test_field_edit_resets_confirmation_http(self):
        batch_id, pid = self._create_batch()
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/confirm_outside_hours",
            data={"confirm": "1", "csrf_token": self._csrf()},
        )
        self.assertEqual(self._flag(pid), 1)
        # Edit the time via the official update route.
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/update",
            data={
                "action": "save",
                "client": "Theta2",
                "workers": "Aida",
                "target_date": TUESDAY,
                "start_time": "14:00",
                "end_time": "16:00",
                "csrf_token": self._csrf(),
            },
        )
        self.assertEqual(self._flag(pid), 0)

    def test_confirm_does_not_bypass_other_blocking(self):
        """A proposal with ANOTHER blocking issue (an absence of the
        currently-assigned worker) must stay blocked even after the
        outside-hours confirmation.
        """
        batch_id, pid = self._create_batch()
        # The auto-picker left worker='' because of Aida's 08-12
        # availability. Admin manually assigns Aida (the exact
        # override path) so the absence check has a target.
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/update",
            data={
                "action": "save",
                "client": "Theta2", "workers": "Aida",
                "target_date": TUESDAY,
                "start_time": "13:00", "end_time": "15:00",
                "csrf_token": self._csrf(),
            },
        )
        # Add an absence for Aida, then confirm outside-hours.
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'sick', ?, ?, '')", (TUESDAY, TUESDAY),
        )
        conn.commit()
        conn.close()
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/confirm_outside_hours",
            data={"confirm": "1", "csrf_token": self._csrf()},
        )
        conn = app.get_conn()
        try:
            props = app.ap_load_draft_proposals(conn, batch_id)
            checked = app.ap_run_checks(conn, props, MONDAY)
        finally:
            conn.close()
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertIn("absence", codes)
        self.assertTrue(checked[0]["has_blocking_issues"])

    def test_review_page_shows_source_and_toggle(self):
        """Source badge AND the confirm/revoke form must render in
        the review page HTML. We force an outside_working_hours
        warning by assigning Aida manually (which is what admin
        would do when the auto-picker left the proposal unassigned
        because the configured availability rejects the slot).
        """
        batch_id, pid = self._create_batch()
        # The /plan/generate created a schedule-sourced proposal
        # with worker="" because Aida's availability (08-12) rejects
        # the 13-15 slot. Manually assign her through the update
        # route — that's exactly the "admin override" path.
        self.client.post(
            f"/plan/review/{batch_id}/proposal/{pid}/update",
            data={
                "action": "save",
                "client": "Theta2", "workers": "Aida",
                "target_date": TUESDAY,
                "start_time": "13:00", "end_time": "15:00",
                "csrf_token": self._csrf(),
            },
        )
        resp = self.client.get(f"/plan/review?batch={batch_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode("utf-8", errors="replace")
        self.assertIn('data-source="schedule"', body)
        self.assertIn("confirm_outside_hours", body)


class SchemaMigrationTests(unittest.TestCase):
    def test_source_column_present(self):
        conn = app.get_conn()
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(shift_proposals)"
            ).fetchall()]
        finally:
            conn.close()
        self.assertIn("source", cols)

    def test_outside_hours_confirmed_column_present(self):
        conn = app.get_conn()
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(shift_proposals)"
            ).fetchall()]
        finally:
            conn.close()
        self.assertIn("outside_hours_confirmed", cols)

    def test_init_db_idempotent(self):
        app.init_db()
        app.init_db()


class HttpWiringTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "en"

    def _csrf(self):
        resp = self.client.get("/plan/generate")
        # Confirm GET actually rendered the form, not a redirect.
        assert resp.status_code == 200, \
            f"GET /plan/generate returned {resp.status_code}"
        with self.client.session_transaction() as sess:
            return sess[app.AP_CSRF_SESSION_KEY]

    def test_generate_persists_schedule_sourced_proposal(self):
        _seed_worker("Aida")
        _seed_client("Rho", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        resp = self.client.post("/plan/generate", data={
            "target_week_start": MONDAY,
            "lookback_weeks": "8",
            "csrf_token": self._csrf(),
        })
        self.assertIn(resp.status_code, (302, 303))
        # Read back the single proposal and confirm source="schedule".
        conn = app.get_conn()
        try:
            rows = conn.execute(
                "SELECT client, target_date, source FROM shift_proposals"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Rho")
        self.assertEqual(rows[0][1], TUESDAY)
        self.assertEqual(rows[0][2], "schedule")


class TranslationCoverageTests(unittest.TestCase):
    PHASE3_KEYS = [
        "auto_plan_issue_outside_working_hours",
        "auto_plan_issue_schedule_invalid",
        "auto_plan_issue_preferred_worker_unknown",
        "auto_plan_source_schedule",
        "auto_plan_source_history",
        "auto_plan_issue_worker_availability_invalid",
        "auto_plan_issue_outside_working_hours_unconfirmed",
        "auto_plan_outside_hours_confirmed",
        "auto_plan_outside_hours_revoked",
    ]

    def test_each_language_has_each_key(self):
        for lang in ("bos", "fr", "en", "de", "pt"):
            with app.app.test_request_context():
                from flask import session as flask_session
                flask_session["lang"] = lang
                tr = app.t()
                for key in self.PHASE3_KEYS:
                    self.assertIn(key, tr,
                                  f"{lang} missing {key}")


if __name__ == "__main__":
    unittest.main()
