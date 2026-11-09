import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from cm import cli, config, procs, state, tmux

SID = "11111111-2222-3333-4444-555555555555"
WIN = SID[:8]
PS = f"""  PID  PPID ARGS
  200     1 tmux new-session -d -s cm
  210   200 claude --resume {SID} --remote-control x
"""
PS_FOREIGN = f"""  PID  PPID ARGS
  300     1 claude --resume {SID}
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        state.add(state.new_entry(SID, "/tmp", "work"))

    def run_cm(self, *argv, stdin=None, ps=PS, alive=True, screen="> ready", panes=(200,)):
        calls = []

        def fake_run(*args, check=True, input=None):
            calls.append((args, input))
            return mock.Mock(returncode=0, stdout="", stderr="")

        out, err = io.StringIO(), io.StringIO()
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(mock.patch.object(tmux, "_run", fake_run))
            stack.enter_context(mock.patch.object(tmux, "_find", lambda n: ("@1", False)))
            stack.enter_context(mock.patch.object(tmux, "is_alive", lambda n: alive))
            stack.enter_context(mock.patch.object(tmux, "capture", lambda n, l=50: screen))
            stack.enter_context(mock.patch.object(tmux, "pane_pids", lambda: list(panes)))
            stack.enter_context(mock.patch.object(tmux, "window_pane_pids", lambda n: list(panes)))
            stack.enter_context(mock.patch.object(procs, "ps_all", lambda: procs.parse_ps(ps)))
            stack.enter_context(mock.patch.object(tmux.time, "sleep", lambda s: None))
            if stdin is not None:
                stack.enter_context(mock.patch("sys.stdin", mock.Mock(buffer=io.BytesIO(stdin.encode()))))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue(), calls


class SendTest(Base):
    def test_sequence(self):
        code, out, _, calls = self.run_cm("send", "work", "hello\r\nworld  ")
        self.assertEqual(code, 0)
        self.assertIn("sent 11 chars", out)
        cmds = [c[0] for c in calls]
        self.assertEqual(cmds[0][0], "load-buffer")
        self.assertEqual(calls[0][1], "hello\nworld")
        self.assertEqual(cmds[1][:2], ("paste-buffer", "-p"))
        self.assertIn("@1", cmds[1])
        self.assertEqual(cmds[2], ("send-keys", "-t", "@1", "Enter"))
        self.assertEqual(sum(1 for c in cmds if c[0] == "load-buffer"), 1)

    def test_stdin_multiline(self):
        code, _, _, calls = self.run_cm("send", WIN, "-", stdin="рядок 1\nрядок 2\n")
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][1], "рядок 1\nрядок 2")

    def test_unknown_session(self):
        code, _, err, calls = self.run_cm("send", "nope", "x")
        self.assertEqual(code, 3)
        self.assertEqual(calls, [])

    def test_manager_refused(self):
        state.add(state.new_entry(config.control_id(), "/tmp", "manager"))
        code, _, err, calls = self.run_cm("send", "manager", "x")
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        code, _, err, calls = self.run_cm("restart", "manager")
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])

    def test_dead(self):
        code, *_, calls = self.run_cm("send", "work", "x", alive=False)
        self.assertEqual(code, 3)
        self.assertEqual(calls, [])

    def test_no_own_claude(self):
        for ps in (PS_FOREIGN, "  PID  PPID ARGS\n"):
            code, *_, calls = self.run_cm("send", "work", "x", ps=ps)
            self.assertEqual(code, 4)
            self.assertEqual(calls, [])

    def test_dialog(self):
        code, _, err, calls = self.run_cm("send", "work", "x", screen="Yes, I trust this folder\nEnter to confirm")
        self.assertEqual(code, 4)
        self.assertEqual(calls, [])

    def test_permission_prompts_and_lone_mark(self):
        screens = ["Do you want to proceed?", "Do you want to make this edit to a.py?", "Do you want to create b?",
                   "❯ 1. Yes", " 1. Yes", "Yes, and don't ask again", "No, and tell Claude what to do differently",
                   "Esc to cancel", "Enter to confirm"]
        for scr in screens:
            code, *_, calls = self.run_cm("send", "work", "x", screen="stuff\n" + scr)
            self.assertEqual(code, 4, scr)
            self.assertEqual(calls, [])

    def test_wait_gives_up_and_succeeds(self):
        now = [0.0]
        fake_time = mock.Mock(time=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
        with mock.patch.object(cli, "time", fake_time):
            code, *_, calls = self.run_cm("send", "work", "x", "--wait", "3", screen="Enter to confirm")
            self.assertEqual(code, 4)
            self.assertGreaterEqual(now[0], 3)
        screens = iter(["Enter to confirm", "Enter to confirm", "> ready"])
        with mock.patch.object(cli, "_dialog_on_screen", lambda w: next(screens) != "> ready"), \
                mock.patch.object(cli, "time", mock.Mock(time=lambda: 0.0, sleep=lambda s: None)):
            code, *_, calls = self.run_cm("send", "work", "x", "--wait", "30")
            self.assertEqual(code, 0)

    def test_dead_window_in_paste(self):
        with mock.patch.object(tmux, "_find", lambda n: ("@1", True)):
            with self.assertRaises(tmux.TmuxError):
                tmux.paste_and_submit(WIN, "x")

    def test_paste_failure_deletes_buffer(self):
        calls = []

        def fake(*args, check=True, input=None):
            calls.append(args)
            if args[0] == "paste-buffer":
                raise tmux.TmuxError("boom")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(tmux, "_run", fake), mock.patch.object(tmux, "_find", lambda n: ("@1", False)):
            with self.assertRaises(tmux.TmuxError):
                tmux.paste_and_submit(WIN, "x")
        self.assertEqual(calls[-1][0], "delete-buffer")
        self.assertTrue(calls[0][2].startswith("cm-send-"))

    def test_ambiguous_and_leading_indent_and_dashes(self):
        state.add(state.new_entry(SID[:8] + "-aaaa", "/tmp", "other"))
        code, _, err, calls = self.run_cm("send", WIN, "x")
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", err)
        code, _, err, calls = self.run_cm("who", WIN)
        self.assertEqual(code, 1)
        code, _, _, calls = self.run_cm("send", "work", "--", "  -indented\n")
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][1], "  -indented")

    def test_bad_text(self):
        for text in ("   ", "a\x1b[201~b", "a\x00b", "a\x9bb", "x" * 16001):
            code, *_, calls = self.run_cm("send", "work", text)
            self.assertEqual(code, 1, repr(text[:10]))
            self.assertEqual(calls, [])
        code, *_ = self.run_cm("send", "work", "a\tb\nc")
        self.assertEqual(code, 0)
        code, *_ = self.run_cm("send", "work", "x" * 16000)
        self.assertEqual(code, 0)


class LsWhoTest(Base):
    def test_ls_json(self):
        code, out, *_ = self.run_cm("ls", "--json")
        row = json.loads(out)[0]
        self.assertEqual((row["session_id"], row["window"], row["control"], row["alive"]), (SID, WIN, False, True))

    def test_who(self):
        code, out, *_ = self.run_cm("who", WIN, "--json")
        d = json.loads(out)
        self.assertEqual((d["session_id"], d["managed"], d["alive"]), (SID, True, True))
        self.assertEqual(d["holders"], [{"pid": 210, "remote_control": True, "ours": True}])

    def test_who_foreign(self):
        code, out, *_ = self.run_cm("who", SID, "--json", ps=PS_FOREIGN)
        self.assertEqual(json.loads(out)["holders"], [{"pid": 300, "remote_control": False, "ours": False}])


if __name__ == "__main__":
    unittest.main()
