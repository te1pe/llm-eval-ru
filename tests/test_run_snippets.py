"""Тесты scripts/run_snippets.py: извлечение блоков и статусы запуска.

Все запускаемые фрагменты — синтетические, написаны здесь же для теста.
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_snippets as rs  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


class ExtractBlocksTest(unittest.TestCase):
    def test_extracts_language_and_code(self):
        text = "текст\n\n```python\nprint(1)\n```\n\nещё текст\n"
        self.assertEqual(rs.extract_blocks(text), [("python", "print(1)")])

    def test_extracts_several_blocks(self):
        text = "```python\na = 1\n```\nмежду\n```c\nint main(void){return 0;}\n```\n"
        blocks = rs.extract_blocks(text)
        self.assertEqual([lang for lang, _ in blocks], ["python", "c"])

    def test_block_without_language(self):
        self.assertEqual(rs.extract_blocks("```\nпросто текст\n```\n"), [("", "просто текст")])

    def test_text_without_blocks(self):
        self.assertEqual(rs.extract_blocks("ответ без кода"), [])

    def test_tilde_fence(self):
        self.assertEqual(rs.extract_blocks("~~~python\nprint(1)\n~~~\n"), [("python", "print(1)")])

    def test_unclosed_block_is_kept(self):
        self.assertEqual(rs.extract_blocks("```python\nprint(1)\n"), [("python", "print(1)")])


class ClassifyTest(unittest.TestCase):
    def test_python_aliases(self):
        for alias in ("python", "python3", "py", "PYTHON"):
            self.assertEqual(rs.classify(alias, "print(1)")[0], "python")

    def test_unknown_language_is_skipped(self):
        language, reason = rs.classify("ruby", "puts 1")
        self.assertIsNone(language)
        self.assertIn("не распознан", reason)

    def test_missing_language_is_skipped(self):
        _, reason = rs.classify("", "какой-то текст")
        self.assertIn("не распознан", reason)

    def test_swiftui_is_skipped(self):
        _, reason = rs.classify("swift", "import SwiftUI\nstruct V: View {}")
        self.assertIn("SwiftUI", reason)

    def test_uikit_is_skipped(self):
        _, reason = rs.classify("swift", "import UIKit\nclass C: UIViewController {}")
        self.assertIn("UIKit", reason)

    def test_empty_block_is_skipped(self):
        _, reason = rs.classify("python", "   \n")
        self.assertIn("пустой", reason)

    def test_known_language_is_not_skipped(self):
        language, reason = rs.classify("python", "print(1)")
        self.assertEqual(language, "python")
        self.assertIsNone(reason)


class RunBlockTest(unittest.TestCase):
    """Статусы на синтетических python-блоках."""

    def test_ok(self):
        status, stderr = rs.run_block("python", "print('синтетика')", timeout=10)
        self.assertEqual(status, "ok")
        self.assertEqual(stderr, "")

    def test_runtime_error(self):
        status, stderr = rs.run_block("python", "raise ValueError('синтетическая ошибка')", timeout=10)
        self.assertEqual(status, "runtime_error")
        self.assertIn("ValueError", stderr)

    def test_timeout(self):
        status, stderr = rs.run_block("python", "import time\ntime.sleep(30)", timeout=1)
        self.assertEqual(status, "timeout")
        self.assertIn("таймаут", stderr)

    def test_compile_error(self):
        status, stderr = rs.run_block("python", "def сломано(:\n    pass", timeout=10)
        self.assertEqual(status, "compile_error")
        self.assertIn("SyntaxError", stderr)

    def test_stderr_is_trimmed_to_20_lines(self):
        code = "import sys\nfor i in range(100):\n    print('строка', i, file=sys.stderr)\nsys.exit(1)"
        _, stderr = rs.run_block("python", code, timeout=10)
        self.assertEqual(len(stderr.split("\n")), 20)

    def test_java_class_name_is_taken_from_code(self):
        self.assertEqual(rs._java_class_name("public class Demo { }"), "Demo")
        self.assertEqual(rs._java_class_name("class Helper { }"), "Helper")
        self.assertEqual(rs._java_class_name("// без класса"), "Main")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="snippets-"))
        self.addCleanup(shutil.rmtree, self.data, True)
        records = [
            {
                "id": "algorithms-900",
                "answer_a": "СИНТЕТИКА A\n\n```python\nprint('привет')\n```",
                "answer_b": "СИНТЕТИКА B\n\n```ruby\nputs 1\n```",
            },
            {
                "id": "ios-900",
                "answer_a": "СИНТЕТИКА A без кода",
                "answer_b": "СИНТЕТИКА B\n\n```swift\nimport SwiftUI\nstruct V: View {}\n```",
            },
        ]
        with (self.data / "evals.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rs.main(argv + ["--data-dir", str(self.data)])
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_does_not_execute_and_does_not_write_file(self):
        code, out, err = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertIn("будет запущено", out)
        self.assertIn("не проверен", err)  # предупреждение о небезопасности
        self.assertFalse((self.data / "snippet_results.json").is_file())

    def test_run_writes_results(self):
        code, out, _ = self.run_main(["--run", "--timeout", "10"])
        self.assertEqual(code, 0)
        results = json.loads((self.data / "snippet_results.json").read_text(encoding="utf-8"))
        by_key = {(item["id"], item["side"]): item for item in results}
        self.assertEqual(by_key[("algorithms-900", "a")]["status"], "ok")
        self.assertEqual(by_key[("algorithms-900", "b")]["status"], "skipped")
        self.assertEqual(by_key[("ios-900", "b")]["status"], "skipped")
        self.assertIn("SwiftUI", by_key[("ios-900", "b")]["note"])
        for item in results:
            self.assertEqual(set(item) >= {"id", "side", "lang", "status", "stderr"}, True)

    def test_no_code_at_all(self):
        (self.data / "evals.jsonl").write_text(
            json.dumps({"id": "git-900", "answer_a": "без кода", "answer_b": "тоже"}) + "\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("не найдено", out)


if __name__ == "__main__":
    unittest.main()
