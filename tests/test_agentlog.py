"""Regression tests for agentlog against made-up session fixtures.

Standard library only. Run from the repository root:

    python3 -m unittest discover -s tests -v

Each test copies tests/fixtures into a temp folder, points agentlog at the
copy, and never reads the real ~/.claude, ~/.codex, ~/.pi or ~/.hermes.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# File mtimes drive the --since/--until window. Pin them so the window tests do
# not depend on when the repo was checked out. Noon UTC keeps each day stable
# in every time zone.
OK_MTIME = "2026-10-01T12:00:00Z"
LIMIT_MTIME = "2026-10-05T12:00:00Z"
MTIME_BY_NAME = {
    "11111111": OK_MTIME,     # claude, finished
    "22222222": LIMIT_MTIME,  # claude, rate limited
    "cccc": OK_MTIME,         # codex, finished
    "dddd": LIMIT_MTIME,      # codex, rate limited
    "eeee": OK_MTIME,         # pi, finished
}


def epoch(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


def load_agentlog():
    """Import the agentlog script as a module (it has no .py suffix)."""
    path = str(REPO / "agentlog")
    loader = importlib.machinery.SourceFileLoader("agentlog_under_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def write_hermes_db(path):
    """Build a small state.db with the columns agentlog reads.

    Only the columns the loader and discovery query are created. The live
    Hermes schema has more columns; these match it for the ones used here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, cwd TEXT,
            started_at REAL NOT NULL, ended_at REAL, last_activity_at REAL,
            title TEXT, message_count INTEGER DEFAULT 0, archived INTEGER DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
            tool_name TEXT, tool_calls TEXT, timestamp REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, compacted INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    t0 = epoch("2026-10-10T09:00:00Z")
    con.execute(
        "INSERT INTO sessions (id, source, cwd, started_at, last_activity_at, "
        "title, message_count) VALUES (?, 'cli', '/root/demo-app', ?, ?, ?, 4)",
        ("hermes-0001", t0, t0 + 60, "Summarise the release notes"),
    )
    rows = [
        # role, content, tool_name, tool_calls, active, compacted
        ("user", "Summarise the release notes.", None, None, 1, 0),
        ("assistant", None, None, json_text(
            [{"function": {"name": "read_file",
                           "arguments": '{"path": "NOTES.md"}'}}]), 0, 1),
        ("tool", "Release 0.3 adds Hermes support.", "read_file", None, 0, 1),
        ("assistant", "Here is the summary: Hermes support is back.", None,
         None, 1, 0),
        # Inactive and not compacted: must never be shown.
        ("assistant", "STALE ROW that must not appear", None, None, 0, 0),
    ]
    for i, (role, content, tool_name, tool_calls, active, compacted) in \
            enumerate(rows):
        con.execute(
            "INSERT INTO messages (session_id, role, content, tool_name, "
            "tool_calls, timestamp, active, compacted) VALUES "
            "('hermes-0001', ?, ?, ?, ?, ?, ?, ?)",
            (role, content, tool_name, tool_calls, t0 + i, active, compacted),
        )
    con.commit()
    con.close()


def json_text(value):
    import json
    return json.dumps(value)


class AgentlogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agentlog-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.copy_fixtures()
        self.mod = load_agentlog()
        self.mod.CLAUDE_PROJECTS = self.tmp / "claude" / "projects"
        self.mod.CODEX_SESSIONS = self.tmp / "codex" / "sessions"
        self.mod.PI_SESSIONS = self.tmp / "pi" / "sessions"
        self.mod.HERMES_DB = self.tmp / "hermes" / "state.db"
        self.mod._HERMES_CON = None

    def copy_fixtures(self):
        for source in ("claude", "codex", "pi"):
            shutil.copytree(FIXTURES / source, self.tmp / source)
        for path in self.tmp.rglob("*.jsonl"):
            for key, when in MTIME_BY_NAME.items():
                if key in path.name:
                    stamp = epoch(when)
                    os.utime(path, (stamp, stamp))

    def run_cli(self, *argv):
        """Run main() with argv. Return (exit_code, stdout_text)."""
        out = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["agentlog", *argv]
        code = 0
        try:
            with contextlib.redirect_stdout(out):
                self.mod.main()
        except SystemExit as exc:
            code = exc.code or 0
        finally:
            sys.argv = old_argv
        return code, out.getvalue()

    def sessions(self, source):
        return {s.sid: s for s in self.mod.discover([source], None)}


class ClaudeFixtureTests(AgentlogTestCase):
    def test_discovers_both_sessions_with_cwd(self):
        found = self.sessions("claude")
        self.assertEqual(
            set(found),
            {"11111111-aaaa-4aaa-8aaa-111111111111",
             "22222222-bbbb-4bbb-8bbb-222222222222"})
        for session in found.values():
            self.assertEqual(session.cwd, "/root/demo-app")

    def test_events_keep_text_tools_and_results(self):
        session = self.sessions("claude")["11111111-aaaa-4aaa-8aaa-111111111111"]
        events = session.events()
        kinds = [(e["role"], e["kind"]) for e in events]
        self.assertEqual(kinds[0], ("user", "text"))
        self.assertIn(("assistant", "tool"), kinds)
        self.assertIn(("user", "result"), kinds)
        tool = next(e for e in events if e["kind"] == "tool")
        self.assertEqual(tool["name"], "Bash")
        self.assertEqual(events[-1]["text"],
                         "The directory has README.md and src. Done.")

    def test_first_prompt_is_the_human_request(self):
        session = self.sessions("claude")["11111111-aaaa-4aaa-8aaa-111111111111"]
        self.assertEqual(self.mod.first_user_line(session),
                         "Add a README to demo-app and list the files.")

    def test_finished_session_is_ok(self):
        session = self.sessions("claude")["11111111-aaaa-4aaa-8aaa-111111111111"]
        self.assertEqual(self.mod.session_status(session)["state"], "ok")

    def test_api_error_at_end_is_not_ok(self):
        session = self.sessions("claude")["22222222-bbbb-4bbb-8bbb-222222222222"]
        state = self.mod.session_status(session)["state"]
        self.assertIn(state, {"rate_limited", "error"})
        self.assertNotEqual(state, "ok")


class CodexFixtureTests(AgentlogTestCase):
    def test_discovers_both_sessions_by_session_meta(self):
        found = self.sessions("codex")
        self.assertEqual(set(found), {"codex-ok-0001", "codex-limit-0002"})
        self.assertEqual(found["codex-ok-0001"].cwd, "/root/demo-app")

    def test_events_keep_message_tool_result_and_reasoning(self):
        session = self.sessions("codex")["codex-ok-0001"]
        events = session.events()
        self.assertEqual(events[0]["text"],
                         "Find the failing unit test and fix it.")
        tool = next(e for e in events if e["kind"] == "tool")
        self.assertEqual(tool["name"], "shell")
        result = next(e for e in events if e["kind"] == "result")
        self.assertEqual(result["text"], "1 failed, 20 passed")
        self.assertTrue(any(e["kind"] == "thinking" for e in events))
        self.assertEqual(events[-1]["text"],
                         "The test expected the old date format. "
                         "I fixed it and all 21 pass.")

    def test_clean_task_complete_is_ok(self):
        session = self.sessions("codex")["codex-ok-0001"]
        self.assertEqual(self.mod.session_status(session)["state"], "ok")

    def test_usage_limit_is_rate_limited(self):
        session = self.sessions("codex")["codex-limit-0002"]
        info = self.mod.session_status(session)
        self.assertEqual(info["state"], "rate_limited")
        self.assertEqual(info["resets_at"], 4102444800)


class PiFixtureTests(AgentlogTestCase):
    def test_discovers_session_by_session_record(self):
        self.assertEqual(set(self.sessions("pi")), {"pi-ok-0001"})
        self.assertEqual(self.sessions("pi")["pi-ok-0001"].cwd, "/root/demo-app")

    def test_events_keep_text_tool_call_and_tool_result(self):
        events = self.sessions("pi")["pi-ok-0001"].events()
        self.assertEqual(events[0]["text"], "Rename the config key to snake_case.")
        tool = next(e for e in events if e["kind"] == "tool")
        self.assertEqual(tool["name"], "edit")
        result = next(e for e in events if e["kind"] == "result")
        self.assertEqual(result["text"], "edited config.py")
        self.assertEqual(events[-1]["text"],
                         "The key is now config_name everywhere.")

    def test_pi_status_is_ok_without_error_records(self):
        session = self.sessions("pi")["pi-ok-0001"]
        self.assertEqual(self.mod.session_status(session)["state"], "ok")


class HermesTests(AgentlogTestCase):
    def setUp(self):
        super().setUp()
        write_hermes_db(self.mod.HERMES_DB)

    def test_discovers_the_cli_session(self):
        found = self.sessions("hermes")
        self.assertEqual(set(found), {"hermes-0001"})
        self.assertEqual(found["hermes-0001"].cwd, "/root/demo-app")

    def test_only_active_or_compacted_rows_are_read(self):
        events = self.sessions("hermes")["hermes-0001"].events()
        texts = [e["text"] for e in events]
        self.assertFalse(any("STALE ROW" in t for t in texts))
        self.assertEqual(texts[0], "Summarise the release notes.")
        self.assertEqual(texts[-1],
                         "Here is the summary: Hermes support is back.")

    def test_compacted_tool_call_and_result_are_kept(self):
        events = self.sessions("hermes")["hermes-0001"].events()
        tool = next(e for e in events if e["kind"] == "tool")
        self.assertEqual(tool["name"], "read_file")
        result = next(e for e in events if e["kind"] == "result")
        self.assertIn("Release 0.3 adds Hermes support.", result["text"])

    def test_status_reports_unknown_not_rate_limited(self):
        session = self.sessions("hermes")["hermes-0001"]
        self.assertEqual(self.mod.hermes_status(session)["state"], "unknown")

    def test_status_command_skips_hermes(self):
        code, out = self.run_cli("status", "--source", "hermes")
        self.assertEqual(code, 0)
        self.assertIn("No rate limits in the last 0 sessions.", out)


class CommandTests(AgentlogTestCase):
    def setUp(self):
        super().setUp()
        write_hermes_db(self.mod.HERMES_DB)

    def test_list_shows_every_source(self):
        code, out = self.run_cli("list", "--source", "all", "--project", "any",
                                 "--limit", "50")
        self.assertEqual(code, 0)
        for sid in ("11111111-aaaa", "22222222-bbbb", "codex-ok-0001",
                    "codex-limit-0002", "pi-ok-0001", "hermes-0001"):
            self.assertIn(sid, out)

    def test_search_finds_a_phrase_in_one_source(self):
        code, out = self.run_cli("search", "config_name", "--source", "pi",
                                 "--project", "any")
        self.assertEqual(code, 0)
        self.assertIn("pi:pi-ok-0001", out)
        self.assertNotIn("claude:", out)

    def test_status_lists_the_two_limited_sessions(self):
        code, out = self.run_cli("status", "--source", "claude,codex",
                                 "--project", "any", "-n", "10")
        limited = [line for line in out.splitlines()
                   if line.startswith("RATE LIMITED")]
        self.assertEqual(len(limited), 2)
        self.assertTrue(any("codex-limit-0002" in line for line in limited))
        self.assertTrue(any("22222222-bbbb" in line for line in limited))
        self.assertFalse(any("codex-ok-0001" in line for line in limited))

    def test_resume_carries_the_user_instruction_verbatim(self):
        dest = self.tmp / "brief.md"
        code, _out = self.run_cli("resume", "codex:codex-ok-0001",
                                  "--out", str(dest))
        self.assertEqual(code, 0)
        brief = dest.read_text()
        self.assertIn("Find the failing unit test and fix it.", brief)

    def test_show_hides_inactive_hermes_rows(self):
        code, out = self.run_cli("show", "hermes:hermes-0001")
        self.assertEqual(code, 0)
        self.assertIn("Summarise the release notes.", out)
        self.assertNotIn("STALE ROW", out)

    def test_since_and_until_use_the_window(self):
        _, out = self.run_cli("list", "--source", "claude,codex,pi",
                              "--project", "any", "--until", "2026-10-02")
        self.assertIn("11111111-aaaa", out)
        self.assertNotIn("22222222-bbbb", out)
        _, out = self.run_cli("list", "--source", "claude,codex,pi",
                              "--project", "any", "--since", "2026-10-05")
        self.assertIn("22222222-bbbb", out)
        self.assertNotIn("11111111-aaaa", out)

    def test_bad_date_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _out = self.run_cli("list", "--since", "2026-13-40")
        self.assertEqual(code, 2)

    def test_version_flag_matches_the_module(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                sys.argv = ["agentlog", "--version"]
                self.mod.main()
        self.assertEqual(self.mod.__version__, "0.3.1")


if __name__ == "__main__":
    unittest.main()
