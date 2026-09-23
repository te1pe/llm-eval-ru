"""Сквозной тест: new_task -> ingest -> validate -> run_snippets --run -> report.

Вся цепочка выполняется во временном каталоге на синтетических фикстурах,
поэтому настоящие data/evals.jsonl и data/blind_key.json остаются нетронутыми.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="chain-"))
        self.addCleanup(shutil.rmtree, self.data, True)
        (self.data / "raw").mkdir()
        shutil.copy(ROOT / "data" / "raw" / "_TEMPLATE.md", self.data / "raw" / "_TEMPLATE.md")
        (self.data / "evals.jsonl").write_text("", encoding="utf-8")

    def run_script(self, name, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--data-dir", str(self.data), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode, 0, "{} завершился с кодом {}:\n{}".format(name, result.returncode, result.stderr)
        )
        return result

    def test_full_chain(self):
        # 1. Заготовка нового задания.
        created = self.run_script("new_task.py", "science", "fact")
        new_file = Path(created.stdout.strip())
        self.assertTrue(new_file.is_file())
        self.assertEqual(new_file.name, "science-001.md")

        # 2. «Ручное заполнение» подменяем синтетическими фикстурами.
        for fixture in sorted((FIXTURES / "raw").glob("*.md")):
            shutil.copy(fixture, self.data / "raw" / fixture.name)

        # 3. Сборка датасета.
        ingested = self.run_script("ingest.py")
        self.assertIn("Добавлено новых заданий: 3", ingested.stdout)
        # Незаполненная заготовка в датасет не попала.
        self.assertIn("science-001.md", ingested.stdout)

        records = [
            json.loads(line)
            for line in (self.data / "evals.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(sorted(record["id"] for record in records), ["algorithms-900", "git-900", "ios-900"])

        # 4. Проверка структуры (все записи ещё todo).
        checked = subprocess.run(
            [sys.executable, str(SCRIPTS / "validate.py"), str(self.data / "evals.jsonl")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("todo: 3", checked.stdout)

        # 5. Запуск кода из ответов.
        run = self.run_script("run_snippets.py", "--run", "--timeout", "10")
        results = json.loads((self.data / "snippet_results.json").read_text(encoding="utf-8"))
        statuses = {item["status"] for item in results}
        self.assertIn("ok", statuses)
        self.assertIn("skipped", statuses)
        self.assertIn("не проверен", run.stderr)

        # 6. Отчёт по пустой разметке и после «разметки» синтетических записей.
        blind = self.run_script("report.py")
        self.assertIn("Размеченных заданий пока нет", blind.stdout)

        shutil.copy(FIXTURES / "evals_done.jsonl", self.data / "evals.jsonl")
        shutil.copy(FIXTURES / "blind_key.json", self.data / "blind_key.json")
        done = self.run_script("report.py")
        self.assertIn("## Средние баллы по критериям", done.stdout)
        self.assertIn("## Запуск кода из ответов", done.stdout)
        unblinded = self.run_script("report.py", "--unblind")
        self.assertIn("| Алиса |", unblinded.stdout)

    def test_real_data_files_stay_empty(self):
        """Фикстуры не должны попадать в настоящие данные проекта."""
        evals = (ROOT / "data" / "evals.jsonl").read_text(encoding="utf-8").strip()
        key = json.loads((ROOT / "data" / "blind_key.json").read_text(encoding="utf-8"))
        self.assertEqual(evals, "")
        self.assertEqual(key, {})


if __name__ == "__main__":
    unittest.main()
