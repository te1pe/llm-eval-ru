"""Тесты scripts/new_task.py: нумерация и заполнение шапки."""
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import new_task  # noqa: E402


class NewTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="new-task-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "raw").mkdir()
        shutil.copy(ROOT / "data" / "raw" / "_TEMPLATE.md", self.tmp / "raw" / "_TEMPLATE.md")

    def test_creates_first_task_with_filled_header(self):
        path = new_task.create_task("ios", "false_premise", self.tmp)
        self.assertEqual(path.name, "ios-001.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("id: ios-001", text)
        self.assertIn("domain: ios", text)
        self.assertIn("task_type: false_premise", text)
        # Секции остаются пустыми: их заполняет человек.
        self.assertIn("## Вопрос", text)
        self.assertIn("## Алиса", text)
        self.assertIn("## GigaChat", text)

    def test_numbering_increments_per_domain(self):
        first = new_task.create_task("ios", "fact", self.tmp)
        second = new_task.create_task("ios", "code", self.tmp)
        other = new_task.create_task("git", "fact", self.tmp)
        self.assertEqual(first.name, "ios-001.md")
        self.assertEqual(second.name, "ios-002.md")
        self.assertEqual(other.name, "git-001.md")

    def test_numbering_continues_after_gap(self):
        (self.tmp / "raw" / "git-007.md").write_text("...", encoding="utf-8")
        path = new_task.create_task("git", "fact", self.tmp)
        self.assertEqual(path.name, "git-008.md")

    def test_main_prints_path(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = new_task.main(["networks", "fact", "--data-dir", str(self.tmp)])
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "raw" / "networks-001.md").is_file())
        self.assertIn("networks-001.md", out.getvalue())

    def test_unknown_domain_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                new_task.main(["telepathy", "fact", "--data-dir", str(self.tmp)])


if __name__ == "__main__":
    unittest.main()
