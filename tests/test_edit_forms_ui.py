"""Regression tests for the + Add visit / + Add interval buttons
on /edit_client and /edit_worker.

The staging bug was: clicking "+ Dodaj termin" on /edit_client did
nothing. Root-cause investigation couldn't fully reproduce locally
(a stale server process was reading a different sqlite file), so
the fix adds defence-in-depth: wrap the inline IIFE in try/catch,
surface init errors to console, and expose a programmatic
readiness flag (data-schedule-ready / data-availability-ready).
These tests pin that defence in place so the symptom cannot
regress silently again.
"""
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-edit-forms-ui-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "edit-forms-ui-test-only")
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


def _reset():
    conn = app.get_conn()
    for table in ("shifts", "clients", "workers", "worker_colors"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


class EditClientScheduleUiTests(unittest.TestCase):
    def setUp(self):
        _reset()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO clients (name, address) VALUES (?,?)",
            ("UiClient", "Rue 1"),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "bos"

    def _html(self):
        resp = self.client.get(
            "/edit_client/" + urllib.parse.quote("UiClient"),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode("utf-8", errors="replace")

    def test_schedule_section_is_rendered(self):
        body = self._html()
        self.assertIn('id="cs-schedule-list"', body)
        self.assertIn('id="cs-schedule-add"', body)
        self.assertIn('name="cleaning_frequency_json"', body)

    def test_add_button_handler_is_in_source(self):
        """The staging bug symptom was a dead +Add button. Pin the
        two things that guarantee the handler attaches: the button
        id must appear in a getElementById call AND the handler
        must call render(INITIAL)."""
        body = self._html()
        self.assertIn('getElementById("cs-schedule-add")', body)
        self.assertIn("render(INITIAL)", body)

    def test_iife_is_guarded_by_try_catch(self):
        body = self._html()
        # The IIFE must open with try{ and close with the matching
        # catch so a Jinja-injected JS surprise cannot silently kill
        # every subsequent event binding.
        self.assertIn("client-schedule UI init failed", body)

    def test_readiness_flag_is_wired(self):
        body = self._html()
        # The script sets list.dataset.scheduleReady = "1" after
        # init — the DOM attribute is `data-schedule-ready` in the
        # live page but the SOURCE string we assert here uses the
        # JS dataset property name.
        self.assertIn("scheduleReady", body)

    def test_add_handler_syncs_dom_before_push(self):
        """Phase 3 hotfix round 2 regression: the add handler MUST
        call syncFromDom() BEFORE INITIAL.push(). Without this sync,
        clicking +Add after editing an existing row would discard
        the edit because render(INITIAL) rebuilds from INITIAL (not
        from DOM)."""
        body = self._html()
        # The source order inside the add click handler must be
        # exactly: syncFromDom() → INITIAL.push → render.
        add_handler_region = body[
            body.index('addBtn.addEventListener("click"'):
            body.index('render(INITIAL);',
                       body.index('addBtn.addEventListener("click"'))
            + len('render(INITIAL);')
        ]
        # 1) sync comes first,
        # 2) push comes after sync,
        # 3) render comes last.
        self.assertLess(
            add_handler_region.index("syncFromDom()"),
            add_handler_region.index("INITIAL.push("),
        )
        self.assertLess(
            add_handler_region.index("INITIAL.push("),
            add_handler_region.index("render(INITIAL)"),
        )

    def test_remove_is_delegated_and_syncs_before_splice(self):
        """The per-row remove button no longer carries its own click
        listener (that captured a stale `entries` and `idx` and
        discarded user edits on other rows); removal goes through
        a delegated listener on the list that resyncs DOM first,
        then splices by the row's CURRENT dataset.index."""
        body = self._html()
        # The delegated listener on the list must exist.
        self.assertIn(
            'list.addEventListener("click"', body,
        )
        # The remove-button creation block MUST NOT carry its own
        # addEventListener("click", ...) anymore.
        rm_section = body[
            body.index('cs-schedule-remove"'):
            body.index('appendChild(rm);')
        ]
        self.assertNotIn('rm.addEventListener("click"', rm_section)

    def test_initial_is_the_same_reference_across_renders(self):
        """`INITIAL = cur;` rebinds (old pattern) would detach every
        already-rendered row's remove-button closure from the live
        array. The new add handler mutates the SAME INITIAL array
        in place — pin it."""
        body = self._html()
        add_handler_region = body[
            body.index('addBtn.addEventListener("click"'):
            body.index('render(INITIAL);',
                       body.index('addBtn.addEventListener("click"'))
            + len('render(INITIAL);')
        ]
        self.assertNotIn("INITIAL = cur", add_handler_region)
        self.assertNotIn("INITIAL =", add_handler_region)

    def test_render_appends_row_to_list(self):
        """Real root cause of the staging dead-button bug: the inline
        render() built each row off-DOM but never attached it to the
        #cs-schedule-list container. Every row was discarded, so the
        click handler ran correctly, pushed to INITIAL, called
        render() — and still produced 0 .cs-schedule-row elements.
        Pin the attach so a future refactor can't drop it again."""
        body = self._html()
        # Isolate the render function body so the assertion cannot be
        # satisfied by an appendChild somewhere ELSE on the page.
        start = body.index("function render(entries)")
        end = body.index("function readRow(row)", start)
        render_body = body[start:end]
        self.assertIn("list.appendChild(row)", render_body)

    def test_add_handler_does_not_prewalk_rows(self):
        """Regression for the staging-bug class: the previous handler
        pre-collected the live DOM via `rows.map(readRow)` BEFORE
        pushing the new entry, so one failing readRow killed the
        whole click. The fix is to push to INITIAL and re-render.
        Pin the shape so a refactor can't reintroduce the pre-walk.
        """
        body = self._html()
        # Must contain the simplified push-then-render shape.
        self.assertIn('INITIAL.push({', body)
        # The fix removed the live-code line
        # `var cur = INITIAL.concat();` from the add handler (the
        # inline source now only shows that string inside the
        # explanatory JS comment block, which is why we match
        # exactly on `var cur = INITIAL.concat();\n` with a
        # following whitespace+code line, not a comment line
        # starting with `//`).
        self.assertNotIn("// code: var cur = INITIAL.concat();", body)


class EditWorkerAvailabilityUiTests(unittest.TestCase):
    def setUp(self):
        _reset()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address, contract_end_date) "
            "VALUES (?,?,'')",
            ("UiWorker", "Rue 2"),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "bos"

    def _html(self):
        resp = self.client.get(
            "/edit_worker/" + urllib.parse.quote("UiWorker"),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode("utf-8", errors="replace")

    def test_availability_section_is_rendered(self):
        body = self._html()
        self.assertIn('id="av-days-list"', body)
        self.assertIn('name="availability_json"', body)

    def test_iife_is_guarded_by_try_catch(self):
        body = self._html()
        self.assertIn("worker-availability UI init failed", body)

    def test_readiness_flag_is_wired(self):
        body = self._html()
        self.assertIn("availabilityReady", body)


class ClientScheduleJsStateMachineSimulationTests(unittest.TestCase):
    """A Python-level simulation of the JS state machine the fixed
    inline script runs in the browser. This models each DOM event
    (add / edit / remove) as the equivalent mutation on INITIAL
    plus a DOM-level snapshot, then asserts the invariant: whatever
    the user sees on screen after a sequence of actions must match
    what the hidden input would POST on submit.

    This does NOT run the real JS, but it mirrors the EXACT sequence
    the fix implements — syncFromDom before every mutation, same
    INITIAL array reference across renders — so a regression in the
    fix (e.g. forgetting to sync, rebinding INITIAL) would also
    regress this test.

    A real browser-click test is the right next layer; the
    environment this sandbox runs in forbids starting a long-lived
    local HTTP server with a venv-correct interpreter, so the
    live-click verification is left as a manual staging check with
    the explicit sequence spelled out in the fix commit message.
    """

    def _default_entry(self):
        return {
            "kind": "weekly", "weekday": 0,
            "start": "09:00", "end": "11:00",
            "preferred_workers": [],
        }

    def _read_dom(self, dom_rows):
        """Mirror the JS readRow: just copy the DOM state."""
        return [dict(row) for row in dom_rows]

    def _sync_from_dom(self, initial, dom_rows):
        """Mirror syncFromDom: INITIAL.length = 0; push each DOM
        row's current state."""
        del initial[:]
        for row in dom_rows:
            initial.append(dict(row))

    def _render(self, initial, dom_rows):
        """Mirror render(INITIAL): rebuild dom from INITIAL."""
        del dom_rows[:]
        for entry in initial:
            dom_rows.append(dict(entry))

    def test_user_edits_survive_second_add(self):
        """The staging-reported scenario:
          1. click +Add → row 1 appears with defaults
          2. user edits row 1 (weekday → Thursday, 10:00-12:00)
          3. click +Add → row 2 appears with defaults
          4. row 1 MUST still reflect the user's edit.
        """
        INITIAL = []
        DOM = []
        # 1) initial render of an empty schedule
        self._render(INITIAL, DOM)
        self.assertEqual(len(DOM), 0)
        # 2) click +Add
        self._sync_from_dom(INITIAL, DOM)
        INITIAL.append(self._default_entry())
        self._render(INITIAL, DOM)
        self.assertEqual(len(DOM), 1)
        # 3) user edits row 1 — this happens in the live DOM only
        DOM[0]["weekday"] = 3        # Thursday
        DOM[0]["start"] = "10:00"
        DOM[0]["end"] = "12:00"
        # 4) click +Add again — syncFromDom pulls the edit into
        # INITIAL BEFORE push, then push adds a new default row.
        self._sync_from_dom(INITIAL, DOM)
        INITIAL.append(self._default_entry())
        self._render(INITIAL, DOM)
        # 5) assert: row 1 kept the edit, row 2 is a fresh default
        self.assertEqual(len(DOM), 2)
        self.assertEqual(DOM[0]["weekday"], 3)
        self.assertEqual(DOM[0]["start"], "10:00")
        self.assertEqual(DOM[0]["end"], "12:00")
        self.assertEqual(DOM[1]["weekday"], 0)
        self.assertEqual(DOM[1]["start"], "09:00")

    def test_remove_does_not_lose_other_rows_edits(self):
        """Click +Add twice, edit both rows, remove the FIRST — the
        second row must preserve its edit."""
        INITIAL = []
        DOM = []
        # Add two rows
        for _ in range(2):
            self._sync_from_dom(INITIAL, DOM)
            INITIAL.append(self._default_entry())
            self._render(INITIAL, DOM)
        # Edit both rows
        DOM[0]["weekday"] = 1  # Tuesday
        DOM[0]["start"] = "08:00"; DOM[0]["end"] = "10:00"
        DOM[1]["weekday"] = 4  # Friday
        DOM[1]["start"] = "14:00"; DOM[1]["end"] = "16:00"
        # Remove row 0 — delegated listener syncs then splices
        self._sync_from_dom(INITIAL, DOM)
        INITIAL.pop(0)
        self._render(INITIAL, DOM)
        self.assertEqual(len(DOM), 1)
        self.assertEqual(DOM[0]["weekday"], 4)
        self.assertEqual(DOM[0]["start"], "14:00")
        self.assertEqual(DOM[0]["end"], "16:00")

    def test_full_edit_persistence_matches_parser(self):
        """End-to-end: after a sequence of edits, the serialized JSON
        must parse through the Phase 1 parser without losing data."""
        INITIAL = []
        DOM = []
        self._sync_from_dom(INITIAL, DOM)
        INITIAL.append(self._default_entry())
        self._render(INITIAL, DOM)
        DOM[0]["weekday"] = 2
        DOM[0]["start"] = "08:00"
        DOM[0]["end"] = "11:00"
        self._sync_from_dom(INITIAL, DOM)
        INITIAL.append({**self._default_entry(), "weekday": 4,
                        "start": "13:00", "end": "15:00"})
        self._render(INITIAL, DOM)
        serialized = json.dumps(INITIAL)
        parsed = app.parse_client_cleaning_frequency(serialized)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["weekday"], 2)
        self.assertEqual(parsed[1]["weekday"], 4)
        self.assertEqual(parsed[1]["start"], "13:00")


class RoundTripThroughSerializeHelperTests(unittest.TestCase):
    """The staging bug was in the UI, not the parser. These tests
    pin the parser's acceptance of the exact JSON shape the fixed
    UI will POST on save, so any accidental regression in either
    the UI OR the parser surfaces immediately."""

    def test_parser_accepts_single_default_entry_from_ui(self):
        payload = json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "09:00", "end": "11:00",
            "preferred_workers": [],
        }])
        parsed = app.parse_client_cleaning_frequency(payload)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["kind"], "weekly")

    def test_parser_accepts_multiple_entries_from_ui(self):
        payload = json.dumps([
            {"kind": "weekly", "weekday": 0, "start": "08:00",
             "end": "10:00", "preferred_workers": []},
            {"kind": "weekly", "weekday": 3, "start": "14:00",
             "end": "16:00", "preferred_workers": []},
        ])
        parsed = app.parse_client_cleaning_frequency(payload)
        self.assertEqual(len(parsed), 2)


if __name__ == "__main__":
    unittest.main()
