import os
import tempfile
import unittest
from unittest import mock

from cm import config, daemon, state


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name,
                                         "CLAUDE_CONFIG_DIR": self.tmp.name + "/claude"})
        p.start()
        self.addCleanup(p.stop)

    def test_roundtrip_and_find(self):
        a = state.new_entry("abcdef12-0000", "/a", "alpha", "plan")
        b = state.new_entry("abcdef99-0000", "/b", "beta")
        state.add(a)
        state.add(b)
        self.assertEqual(len(state.load()), 2)
        s = state.load()
        self.assertEqual(state.find(s, "alpha")["id"], a["id"])
        self.assertEqual(state.find(s, "abcdef9")["name"], "beta")
        with self.assertRaises(state.StateError):
            state.find(s, "abcdef")
        with self.assertRaises(state.StateError):
            state.find(s, "nope")
        with self.assertRaises(state.StateError):
            state.add(state.new_entry("zzz", "/c", "alpha"))
        state.update(a["id"], restarts=3)
        self.assertEqual(state.find(state.load(), "alpha")["restarts"], 3)
        state.remove(a["id"])
        self.assertEqual([x["name"] for x in state.load()], ["beta"])

    def test_empty_and_control_id_stable(self):
        self.assertEqual(state.load(), [])
        self.assertEqual(config.control_id(), config.control_id())
        e = daemon.ensure_control()
        self.assertEqual(daemon.ensure_control()["id"], e["id"])
        self.assertEqual(len(state.load()), 1)

    def test_build_command(self):
        s = state.new_entry("11111111-2222", "/a", "alpha", "plan")
        with mock.patch("cm.claude_store.session_exists", return_value=False):
            c = daemon.build_command(s)
        self.assertIn("--session-id 11111111-2222", c)
        self.assertIn("--permission-mode plan", c)
        with mock.patch("cm.claude_store.session_exists", return_value=True):
            self.assertIn("--resume 11111111-2222", daemon.build_command(s))


if __name__ == "__main__":
    unittest.main()
