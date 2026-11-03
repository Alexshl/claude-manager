import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cm import claude_store, config


def write(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        p.start()
        self.addCleanup(p.stop)

    def test_parse_string_and_list_content(self):
        f = self.home / ".claude/projects/-a-b/11111111-aaaa.jsonl"
        write(f, ["garbage{", {"type": "user", "cwd": "/a/b", "sessionId": "11111111-aaaa",
                               "message": {"content": [{"type": "text", "text": "hello   world"}]}}])
        s = claude_store.parse_session_file(f)
        self.assertEqual(s.cwd, "/a/b")
        self.assertEqual(s.title, "hello world")
        self.assertEqual(s.id, "11111111-aaaa")

    def test_summary_wins_and_skips_pseudo(self):
        f = self.home / ".claude/projects/x/22222222.jsonl"
        write(f, [{"type": "user", "message": {"content": "<command-name>/x</command-name>"}},
                  {"type": "user", "cwd": "/c", "message": {"content": "real"}},
                  {"type": "summary", "summary": "Sum"}])
        self.assertEqual(claude_store.parse_session_file(f).title, "Sum")

    def test_title_truncated_and_missing_cwd(self):
        f = self.home / ".claude/projects/x/33333333.jsonl"
        write(f, [{"type": "user", "message": {"content": "z" * 500}}])
        s = claude_store.parse_session_file(f)
        self.assertIsNone(s.cwd)
        self.assertEqual(len(s.title), claude_store.TITLE_LEN)

    def test_list_filter_and_prefix(self):
        write(self.home / ".claude/projects/p1/aaaa1111.jsonl", [{"cwd": "/one", "type": "user", "message": {"content": "1"}}])
        write(self.home / ".claude/projects/p2/bbbb2222.jsonl", [{"cwd": "/two", "type": "user", "message": {"content": "2"}}])
        self.assertEqual(len(claude_store.list_sessions()), 2)
        self.assertEqual([s.id for s in claude_store.list_sessions(dir_filter="/two")], ["bbbb2222"])
        self.assertEqual(len(claude_store.list_sessions(limit=1)), 1)
        self.assertEqual(claude_store.find_by_prefix("aaaa").cwd, "/one")
        self.assertTrue(claude_store.session_exists("bbbb2222"))
        with self.assertRaises(LookupError):
            claude_store.find_by_prefix("zzz")

    def test_trust_folder_preserves_and_backs_up(self):
        cj = self.home / ".claude.json"
        cj.write_text(json.dumps({"other": 1, "projects": {"/x": {"k": 2}}}))
        with mock.patch.object(config, "claude_json_path", return_value=cj):
            claude_store.trust_folder("/x")
            claude_store.trust_folder("/y")
        d = json.loads(cj.read_text())
        self.assertEqual(d["other"], 1)
        self.assertEqual(d["projects"]["/x"], {"k": 2, "hasTrustDialogAccepted": True})
        self.assertTrue(d["projects"]["/y"]["hasTrustDialogAccepted"])
        bak = json.loads((self.home / ".claude.json.cm-bak").read_text())
        self.assertNotIn("hasTrustDialogAccepted", bak["projects"]["/x"])


if __name__ == "__main__":
    unittest.main()
