import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from cm import cli, procs, screen, state, tmux
from tests.test_send import PS, SID, WIN


def auq(cursor=1, labels=("Червоний", "Синій"), tabs=" ☐ Колір", check=False, typed=None, chat_cursor=False):
    lines = [tabs, "", "Який колір?", ""]
    n = 0
    for n, lab in enumerate(labels, 1):
        mark = "[ ] " if check else ""
        lines += [f"{'❯' if cursor == n else ' '} {n}. {mark}{lab}", f"     опис {lab}"]
    t = n + 1
    lines.append(f"{'❯' if cursor == t else ' '} {t}. {typed if typed is not None else 'Type something.'}")
    lines += ["────────────", f"{'❯' if cursor == t + 1 else ' '} {t + 1}. Chat about this", "",
              "Enter to select · ↑/↓ to navigate · Esc to cancel"]
    return "\n".join(lines)


EDIT = "Edit file\nDo you want to make this edit to a.py?\n❯ 1. Yes\n  2. No\nEsc to cancel"
BASH = "Bash(rm x)\nDo you want to proceed?\n❯ 1. Yes\n  2. Yes, and don't ask again\n  3. No, and tell Claude what to do differently\nEsc to cancel"
STARTUP = "Yes, I trust this folder\nEnter to confirm"
MULTI_TAB = auq(tabs="← ☐ Колір  ☐ Розмір  ✔ Submit →")


class ScreenTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(screen.classify(auq()), "auq")
        self.assertEqual(screen.classify(auq(cursor=2)), "auq")
        for s in (EDIT, BASH, STARTUP):
            self.assertEqual(screen.classify(s), "permission")
        self.assertEqual(screen.classify("> ready"), "other")
        self.assertEqual(screen.classify("Esc to cancel"), "other")
        q = auq().replace("Який колір?", "Do you want to proceed?")
        self.assertEqual(screen.classify(q), "permission")

    def test_parse(self):
        w = screen.parse_auq(auq())
        self.assertEqual(w, {"current": 1, "options": [(1, "Червоний"), (2, "Синій")], "type_n": 3,
                             "multi_tab": False, "multi_select": False})
        self.assertEqual(screen.parse_auq(auq(cursor=2))["current"], 2)
        self.assertEqual(screen.parse_auq(auq(cursor=3))["current"], 3)
        self.assertEqual(screen.parse_auq(auq(cursor=4))["current"], 4)

    def test_parse_multi(self):
        self.assertTrue(screen.parse_auq(MULTI_TAB)["multi_tab"])
        self.assertTrue(screen.parse_auq(auq(check=True))["multi_select"])
        self.assertIsNone(screen.parse_auq("> ready"))


class FakeWidget:
    """Stateful tmux stand-in: Up/Down move the cursor, text lands in the Type field, Enter closes."""

    def __init__(self, cursor=1, paste_works=True, labels=("Червоний", "Синій"), static=None):
        self.cursor, self.keys, self.pasted, self.closed = cursor, [], None, False
        self.paste_works, self.labels, self.static = paste_works, labels, static
        self.t = len(labels) + 1

    def capture(self, name, lines=50):
        if self.static is not None:
            return self.static
        if self.closed:
            return "> ready"
        return auq(self.cursor, self.labels, typed=self.pasted)

    def send_keys(self, name, *keys):
        for k in keys:
            self.keys.append(k)
            if k == "Down":
                self.cursor = min(self.cursor + 1, self.t + 1)
            elif k == "Up":
                self.cursor = max(self.cursor - 1, 1)
            elif k == "Enter":
                self.closed = True

    def paste(self, name, text):
        self.keys.append("PASTE")
        if self.paste_works:
            self.pasted = text


class AnswerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "XDG_STATE_HOME": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        state.add(state.new_entry(SID, "/tmp", "work"))

    def run_cm(self, fake, *argv, stdin=None, ps=PS, alive=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(tmux, "_find", lambda n: ("@1", False)))
            st.enter_context(mock.patch.object(tmux, "_run", lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr="")))
            st.enter_context(mock.patch.object(tmux, "is_alive", lambda n: alive))
            st.enter_context(mock.patch.object(tmux, "capture", fake.capture))
            st.enter_context(mock.patch.object(tmux, "send_keys", fake.send_keys))
            st.enter_context(mock.patch.object(tmux, "paste_text", fake.paste))
            st.enter_context(mock.patch.object(tmux, "window_pane_pids", lambda n: [200]))
            st.enter_context(mock.patch.object(procs, "ps_all", lambda: procs.parse_ps(ps)))
            st.enter_context(mock.patch.object(cli.time, "sleep", lambda s: None))
            if stdin is not None:
                st.enter_context(mock.patch("sys.stdin", mock.Mock(buffer=io.BytesIO(stdin.encode()))))
            st.enter_context(contextlib.redirect_stdout(out))
            st.enter_context(contextlib.redirect_stderr(err))
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class AnswerTest(AnswerBase):
    def test_option_down(self):
        f = FakeWidget(cursor=1)
        code, out, _ = self.run_cm(f, "answer", "work", "--option", "2", "--expect-label", "Син")
        self.assertEqual(code, 0)
        self.assertEqual(f.keys, ["Down", "Enter"])
        self.assertIn("answered option 2", out)

    def test_option_up_and_same(self):
        f = FakeWidget(cursor=3)
        self.assertEqual(self.run_cm(f, "answer", "work", "--option", "1")[0], 0)
        self.assertEqual(f.keys, ["Up", "Up", "Enter"])
        f = FakeWidget(cursor=1)
        self.assertEqual(self.run_cm(f, "answer", "work", "--option", "1")[0], 0)
        self.assertEqual(f.keys, ["Enter"])

    def test_text(self):
        f = FakeWidget(cursor=1)
        code, out, _ = self.run_cm(f, "answer", "work", "--text", "-", stdin="Свій варіант\n")
        self.assertEqual(code, 0)
        self.assertEqual(f.keys, ["Down", "Down", "PASTE", "Enter"])
        self.assertEqual(f.pasted, "Свій варіант")
        self.assertIn("12 chars", out)
        self.assertNotIn("Свій", out)

    def test_text_not_landed(self):
        f = FakeWidget(paste_works=False)
        code, *_ = self.run_cm(f, "answer", "work", "--text", "-", stdin="abc")
        self.assertEqual(code, 1)
        self.assertNotIn("Enter", f.keys)

    def test_text_multiline_or_bad(self):
        for txt in ("a\nb", "  ", "a\x1bb"):
            f = FakeWidget()
            code, *_ = self.run_cm(f, "answer", "work", "--text", "-", stdin=txt)
            self.assertEqual(code, 1)
            self.assertEqual(f.keys, [])

    def test_arg_errors(self):
        for argv in (["answer", "work"], ["answer", "work", "--option", "1", "--text", "-"],
                     ["answer", "work", "--text", "secret"]):
            f = FakeWidget()
            code, *_ = self.run_cm(f, *argv, stdin="x")
            self.assertEqual(code, 1)
            self.assertEqual(f.keys, [])

    def test_out_of_range(self):
        for n in ("0", "3", "4", "9"):
            f = FakeWidget()
            code, *_ = self.run_cm(f, "answer", "work", "--option", n)
            self.assertEqual(code, 1, n)
            self.assertEqual(f.keys, [])

    def test_refused_without_keys(self):
        for scr in (EDIT, BASH, STARTUP, "> ready", MULTI_TAB, auq(check=True)):
            f = FakeWidget(static=scr)
            code, *_ = self.run_cm(f, "answer", "work", "--option", "2")
            self.assertEqual(code, 4, scr)
            self.assertEqual(f.keys, [])
            code, *_ = self.run_cm(f, "answer", "work", "--text", "-", stdin="x")
            self.assertEqual(code, 4)
            self.assertEqual(f.keys, [])

    def test_expect_label_mismatch(self):
        f = FakeWidget()
        code, *_ = self.run_cm(f, "answer", "work", "--option", "2", "--expect-label", "Зелений")
        self.assertEqual(code, 4)
        self.assertEqual(f.keys, [])

    def test_cursor_not_moving_no_enter(self):
        f = FakeWidget(cursor=1)
        f.send_keys = lambda name, *k: f.keys.extend(k)
        code, *_ = self.run_cm(f, "answer", "work", "--option", "2")
        self.assertEqual(code, 1)
        self.assertNotIn("Enter", f.keys)

    def test_widget_not_closing(self):
        f = FakeWidget()
        orig = f.send_keys

        def keys(name, *k):
            orig(name, *k)
            f.closed = False

        f.send_keys = keys
        code, *_ = self.run_cm(f, "answer", "work", "--option", "1")
        self.assertEqual(code, 5)

    def test_not_managed_dead_foreign(self):
        f = FakeWidget()
        self.assertEqual(self.run_cm(f, "answer", "nope", "--option", "1")[0], 3)
        self.assertEqual(self.run_cm(f, "answer", "work", "--option", "1", alive=False)[0], 3)
        self.assertEqual(self.run_cm(f, "answer", "work", "--option", "1", ps="  PID  PPID ARGS\n")[0], 4)
        self.assertEqual(f.keys, [])

    def test_send_still_refuses_auq(self):
        out = mock.patch.object(tmux, "capture", lambda n, l=50: auq())
        with out:
            self.assertTrue(cli._dialog_on_screen(WIN))


if __name__ == "__main__":
    unittest.main()
