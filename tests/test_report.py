"""Тесты scripts/report.py на синтетической фикстуре."""
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

import report  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="report-"))
        self.addCleanup(shutil.rmtree, self.data, True)
        shutil.copy(FIXTURES / "evals_done.jsonl", self.data / "evals.jsonl")
        shutil.copy(FIXTURES / "blind_key.json", self.data / "blind_key.json")
        self.records = report.load_records(self.data / "evals.jsonl")
        self.snippets = self.data / "snippet_results.json"

    def build(self, blind_key=None):
        return report.build_report(self.records, self.snippets, blind_key)

    def test_counts_done_and_todo(self):
        text = self.build()
        self.assertIn("Всего заданий: 5. Размечено (done): 4. В работе (todo): 1.", text)

    def test_criteria_averages(self):
        # correctness: A = (5+1+3+3)/4 = 3.00, B = (2+5+3+2)/4 = 3.00
        text = self.build()
        line = next(row for row in text.split("\n") if "correctness" in row)
        self.assertEqual(line.count("3.00"), 2)

    def test_verdict_counts(self):
        text = self.build()
        self.assertIn("| Победа A | 2 |", text)
        self.assertIn("| Победа B | 1 |", text)
        self.assertIn("| Ничья | 1 |", text)

    def test_domain_breakdown(self):
        text = self.build()
        self.assertIn("## Разбивка по доменам", text)
        for domain in ("algorithms", "ios", "git", "networks"):
            self.assertIn("| {} |".format(domain), text)

    def test_task_type_breakdown(self):
        text = self.build()
        self.assertIn("## Разбивка по типам заданий", text)
        for task_type in ("code", "false_premise", "explain", "fact"):
            self.assertIn("| {} |".format(task_type), text)

    def test_weak_winner_share(self):
        # Победители: A(5), B(5), A(3) -> один из трёх слабее четырёх.
        text = self.build()
        self.assertIn("Заданий с явным победителем (без ничьих): 3.", text)
        self.assertIn("Из них у победителя correctness < 4: 1 (33%).", text)

    def test_todo_records_are_not_counted(self):
        text = self.build()
        self.assertNotIn("law-900", text)

    def test_blind_report_has_no_model_names(self):
        text = self.build()
        self.assertNotIn("Алиса", text)
        self.assertNotIn("GigaChat", text)

    def test_unblind_shows_models(self):
        key = json.loads((self.data / "blind_key.json").read_text(encoding="utf-8"))
        text = self.build(key)
        self.assertIn("## Разблиндировка: статистика по моделям", text)
        self.assertIn("| Алиса |", text)
        self.assertIn("| GigaChat |", text)

    def test_unblind_counts_wins_per_model(self):
        key = json.loads((self.data / "blind_key.json").read_text(encoding="utf-8"))
        text = self.build(key)
        # Победы: algorithms-900 A=Алиса, ios-900 B=Алиса, networks-900 A=GigaChat.
        alisa = next(row for row in text.split("\n") if row.startswith("| Алиса |"))
        giga = next(row for row in text.split("\n") if row.startswith("| GigaChat |"))
        self.assertEqual(alisa.split("|")[3].strip(), "2")
        self.assertEqual(giga.split("|")[3].strip(), "1")

    def test_snippet_summary_included_when_file_exists(self):
        self.snippets.write_text(
            json.dumps(
                [
                    {"id": "algorithms-900", "side": "a", "lang": "python", "status": "ok", "stderr": ""},
                    {"id": "ios-900", "side": "b", "lang": "swift", "status": "skipped", "stderr": ""},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        text = self.build()
        self.assertIn("## Запуск кода из ответов", text)
        self.assertIn("Всего блоков кода: 2.", text)
        self.assertIn("| ok | 1 |", text)
        self.assertIn("| skipped | 1 |", text)

    def test_no_snippet_section_without_file(self):
        self.assertNotIn("## Запуск кода из ответов", self.build())

    def test_empty_dataset(self):
        text = report.build_report([], self.snippets, None)
        self.assertIn("Размеченных заданий пока нет", text)

    def test_main_writes_markdown_to_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main(["--data-dir", str(self.data)])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("# Отчёт по ручной разметке"))

    def test_main_unblind_flag(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main(["--unblind", "--data-dir", str(self.data)])
        self.assertEqual(code, 0)
        self.assertIn("Разблиндировка", out.getvalue())
        self.assertIn("| Алиса |", out.getvalue())


if __name__ == "__main__":
    unittest.main()
