"""Тесты scripts/ingest.py: парсинг шаблона, идемпотентность, случайность сторон."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


class ParseTest(unittest.TestCase):
    def test_template_is_recognized_as_not_filled(self):
        text = (ROOT / "data" / "raw" / "_TEMPLATE.md").read_text(encoding="utf-8")
        with self.assertRaises(ingest.NotFilledError):
            ingest.parse_raw(text, "_TEMPLATE.md")

    def test_parses_filled_task(self):
        text = (FIXTURES / "raw" / "algorithms-900.md").read_text(encoding="utf-8")
        parsed = ingest.parse_raw(text, "algorithms-900.md")
        self.assertEqual(parsed["id"], "algorithms-900")
        self.assertEqual(parsed["domain"], "algorithms")
        self.assertEqual(parsed["task_type"], "code")
        self.assertIn("СИНТЕТИЧЕСКИЙ ТЕСТОВЫЙ ВОПРОС", parsed["prompt"])
        self.assertIn("print(1 + 1)", parsed["alisa"])

    def test_code_blocks_are_preserved_as_is(self):
        text = (FIXTURES / "raw" / "ios-900.md").read_text(encoding="utf-8")
        parsed = ingest.parse_raw(text, "ios-900.md")
        self.assertIn("```swift", parsed["gigachat"])
        self.assertIn("import SwiftUI", parsed["gigachat"])
        self.assertTrue(parsed["gigachat"].rstrip().endswith("```"))

    def test_unknown_heading_stays_inside_answer(self):
        text = (FIXTURES / "raw" / "algorithms-900.md").read_text(encoding="utf-8")
        parsed = ingest.parse_raw(text, "algorithms-900.md")
        self.assertIn("## Пример", parsed["gigachat"])

    def test_heading_inside_code_block_is_not_a_section(self):
        text = (
            "---\nid: os_c-900\ndomain: os_c\ntask_type: code\n---\n\n"
            "## Вопрос\n\nвопрос\n\n## Алиса\n\n```c\n// ## GigaChat\nint main(void){return 0;}\n```\n\n"
            "## GigaChat\n\nответ b\n"
        )
        parsed = ingest.parse_raw(text, "os_c-900.md")
        self.assertIn("## GigaChat", parsed["alisa"])
        self.assertEqual(parsed["gigachat"], "ответ b")

    def test_missing_section_is_parse_error(self):
        text = "---\nid: git-901\ndomain: git\ntask_type: fact\n---\n\n## Вопрос\n\nвопрос\n"
        with self.assertRaises(ingest.ParseError):
            ingest.parse_raw(text, "git-901.md")

    def test_unknown_domain_is_parse_error(self):
        text = (
            "---\nid: magic-901\ndomain: magic\ntask_type: fact\n---\n\n"
            "## Вопрос\n\nв\n\n## Алиса\n\nа\n\n## GigaChat\n\nб\n"
        )
        with self.assertRaises(ingest.ParseError):
            ingest.parse_raw(text, "magic-901.md")

    def test_missing_header_is_parse_error(self):
        with self.assertRaises(ingest.ParseError):
            ingest.parse_raw("## Вопрос\n\nвопрос\n", "нет-шапки.md")


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="ingest-"))
        self.addCleanup(shutil.rmtree, self.data, True)
        shutil.copytree(FIXTURES / "raw", self.data / "raw")
        shutil.copy(ROOT / "data" / "raw" / "_TEMPLATE.md", self.data / "raw" / "_TEMPLATE.md")
        (self.data / "evals.jsonl").write_text("", encoding="utf-8")

    def records(self):
        text = (self.data / "evals.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def test_creates_records_with_empty_markup_fields(self):
        result = ingest.ingest(self.data)
        self.assertEqual(len(result["added"]), 3)
        self.assertEqual(result["errors"], [])
        for record in self.records():
            self.assertEqual(record["status"], "todo")
            self.assertIsNone(record["verdict"])
            self.assertIsNone(record["rationale"])
            self.assertIsNone(record["reference"])
            self.assertEqual(record["sources"], [])
            for side in ("a", "b"):
                self.assertEqual(
                    record["scores"][side],
                    {"correctness": None, "completeness": None, "clarity": None, "safety": None},
                )

    def test_is_idempotent(self):
        ingest.ingest(self.data)
        first = (self.data / "evals.jsonl").read_text(encoding="utf-8")
        first_key = (self.data / "blind_key.json").read_text(encoding="utf-8")

        result = ingest.ingest(self.data)
        self.assertEqual(result["added"], [])
        self.assertEqual(sorted(result["skipped"]), ["algorithms-900", "git-900", "ios-900"])
        self.assertEqual((self.data / "evals.jsonl").read_text(encoding="utf-8"), first)
        self.assertEqual((self.data / "blind_key.json").read_text(encoding="utf-8"), first_key)

    def test_new_task_added_to_existing_dataset(self):
        ingest.ingest(self.data)
        shutil.copy(
            FIXTURES / "raw" / "git-900.md", self.data / "raw" / "git-901.md"
        )
        text = (self.data / "raw" / "git-901.md").read_text(encoding="utf-8")
        (self.data / "raw" / "git-901.md").write_text(
            text.replace("git-900", "git-901"), encoding="utf-8"
        )
        result = ingest.ingest(self.data)
        self.assertEqual(result["added"], ["git-901"])
        self.assertEqual(len(self.records()), 4)

    def test_template_and_unfilled_files_are_skipped(self):
        # Свежесозданная заготовка ещё не заполнена и не должна попасть в датасет.
        sys.path.insert(0, str(ROOT / "scripts"))
        import new_task

        new_task.create_task("law", "fact", self.data)
        result = ingest.ingest(self.data)
        self.assertEqual(sorted(result["added"]), ["algorithms-900", "git-900", "ios-900"])
        self.assertEqual(len(result["not_filled"]), 1)
        self.assertIn("law-001.md", result["not_filled"][0])

    def test_duplicate_id_in_raw_is_reported(self):
        shutil.copy(FIXTURES / "raw" / "git-900.md", self.data / "raw" / "git-900-копия.md")
        result = ingest.ingest(self.data)
        self.assertTrue(any("уже встречался" in message for message in result["errors"]))

    def test_sides_are_assigned_randomly(self):
        """На 50 заданиях обе ориентации сторон должны встретиться."""
        for number in range(1, 51):
            source = FIXTURES / "raw" / "git-900.md"
            target = self.data / "raw" / "science-{:03d}.md".format(number)
            text = source.read_text(encoding="utf-8")
            text = text.replace("id: git-900", "id: science-{:03d}".format(number))
            text = text.replace("domain: git", "domain: science")
            target.write_text(text, encoding="utf-8")

        ingest.ingest(self.data)
        key = json.loads((self.data / "blind_key.json").read_text(encoding="utf-8"))
        science = [value for name, value in key.items() if name.startswith("science-")]
        self.assertEqual(len(science), 50)
        orientations = {(item["a"], item["b"]) for item in science}
        self.assertEqual(
            orientations,
            {(ingest.MODEL_A_SIDE, ingest.MODEL_B_SIDE), (ingest.MODEL_B_SIDE, ingest.MODEL_A_SIDE)},
        )

    def test_blind_key_matches_answers(self):
        ingest.ingest(self.data)
        key = json.loads((self.data / "blind_key.json").read_text(encoding="utf-8"))
        parsed = ingest.parse_raw(
            (self.data / "raw" / "ios-900.md").read_text(encoding="utf-8"), "ios-900.md"
        )
        record = next(item for item in self.records() if item["id"] == "ios-900")
        expected_a = parsed["alisa"] if key["ios-900"]["a"] == ingest.MODEL_A_SIDE else parsed["gigachat"]
        self.assertEqual(record["answer_a"], expected_a)


if __name__ == "__main__":
    unittest.main()
