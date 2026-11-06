import os
import tempfile
import unittest
from unittest import mock

from cm import cli, config, daemon, procs, state

SID = "11111111-2222-3333-4444-555555555555"
PS = f"""  PID  PPID ARGS
  100     1 /sbin/launchd
  200   100 tmux new-session -d -s cm
  210   200 claude --resume {SID} --remote-control mac: x
  300     1 /home/u/.local/bin/claude --resume {SID}
  400     1 vim notes-{SID}.txt
  500     1 claude --resume 99999999-0000
"""


class ProcsTest(unittest.TestCase):
    def test_parse_and_find(self):
        ps = procs.parse_ps(PS)
        self.assertEqual(len(ps), 6)
        found = procs.find_session_procs(SID, ps)
        self.assertEqual([p.pid for p in found], [210, 300])
        self.assertTrue(found[0].remote_control)
        self.assertFalse(found[1].remote_control)

    def test_split_ours(self):
        ps = procs.parse_ps(PS)
        found = procs.find_session_procs(SID, ps)
        ours, foreign = procs.split_ours(found, ps, [200])
        self.assertEqual([p.pid for p in ours], [210])
        self.assertEqual([p.pid for p in foreign], [300])

    def test_decide(self):
        plain = procs.Proc(1, 0, f"claude --resume {SID}")
        remote = procs.Proc(2, 0, f"claude --resume {SID} --remote-control x")
        self.assertEqual(procs.decide([], False, False), "launch")
        self.assertEqual(procs.decide([plain], True, False), "fork")
        self.assertEqual(procs.decide([remote], True, False), "fork")
        self.assertEqual(procs.decide([plain], False, True), "kill")
        with self.assertRaises(procs.ConflictError):
            procs.decide([plain], False, False)
        with self.assertRaises(procs.ConflictError):
            procs.decide([remote], False, True)


class NamingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name,
                                         "CLAUDE_CONFIG_DIR": self.tmp.name + "/claude"})
        p.start()
        self.addCleanup(p.stop)

    def test_display_name_and_defaults(self):
        with mock.patch.object(config, "short_host", return_value="macmini"):
            self.assertEqual(config.display_name("fdesk"), "macmini: fdesk")
            config.config_path().parent.mkdir(parents=True)
            config.config_path().write_text('{"host_label": "home", "default_permission_mode": "plan"}')
            self.assertEqual(config.display_name("x"), "home: x")
            self.assertEqual(config.default_permission_mode(), "plan")

    def test_command_defaults_and_fork(self):
        with mock.patch.object(config, "short_host", return_value="macmini"):
            s = state.new_entry("aaaaaaaa-1", "/a", "fdesk")
            self.assertEqual(config.default_permission_mode(), "auto")
            c = daemon.build_command(s)
            self.assertIn("'macmini: fdesk'", c)
            self.assertIn("--permission-mode auto", c)
            cid = config.control_id()
            ctl = state.new_entry(cid, "/c", "manager")
            self.assertIn("'macmini: manager'", daemon.build_command(ctl))
            f = state.new_entry("bbbbbbbb-2", "/a", "fk")
            f["fork_from"] = "aaaaaaaa-1"
            self.assertIn("--resume aaaaaaaa-1 --fork-session --session-id bbbbbbbb-2", daemon.build_command(f))

    def test_clone_command(self):
        with mock.patch("shutil.which", return_value="/usr/bin/gh"):
            self.assertEqual(cli.clone_command("o/r", "/d")[:3], ["gh", "repo", "clone"])
            self.assertEqual(cli.clone_command("https://x/y.git", "/d")[0], "git")
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(cli.clone_command("o/r", "/d")[0], "git")


if __name__ == "__main__":
    unittest.main()


class StartupPromptsTest(unittest.TestCase):
    def test_answers_trust_then_chrome_until_ready(self):
        screens = iter([
            "Yes, I trust this folder\nEnter to confirm",
            "remote-control is active\n❯ No, keep browser tools off\n  Yes, use my browser\nEnter to confirm",
            "remote-control is active\n❯ Try something",
        ])
        with mock.patch.object(daemon.time, "sleep"), \
             mock.patch.object(daemon.tmux, "capture", side_effect=lambda *a: next(screens)), \
             mock.patch.object(daemon.tmux, "send_keys") as keys:
            daemon._answer_startup_prompts("abc")
        self.assertEqual(keys.call_args_list,
                         [mock.call("abc", "Down"), mock.call("abc", "Enter")] * 2)
