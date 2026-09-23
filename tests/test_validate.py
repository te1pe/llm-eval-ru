"""Тесты scripts/validate.py: все ветки проверок."""
import contextlib
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

DONE = {
    "id": "ios-900",
    "domain": "ios",
    "task_type": "fact",
    "prompt": "СИНТЕТИЧЕСКИЙ вопрос.",
    "answer_a": "СИНТЕТИЧЕСКИЙ ответ A.",
    "answer_b": "СИНТЕТИЧЕСКИЙ ответ B.",
    "status": "done",
    "scores": {
        "a": {"correctness": 5, "completeness": 4, "clarity": 4, "safety": 5},
        "b": {"correctness": 2, "completeness": 3, "clarity": 4, "safety": 5},
    },
    "verdict": "A",
    "rationale": "СИНТЕТИЧЕСКОЕ обоснование.",
    "sources": ["https://example.org/doc"],
    "reference": "СИНТЕТИЧЕСКИЙ эталон.",
}

TODO = {
    "id": "git-900",
    "domain": "git",
    "task_type": "explain",
    "prompt": "СИНТЕТИЧЕСКИЙ вопрос.",
    "answer_a": "СИНТЕТИЧЕСКИЙ ответ A.",
    "answer_b": "СИНТЕТИЧЕСКИЙ ответ B.",
    "status": "todo",
    "scores": {
        "a": {"correctness": None, "completeness": None, "clarity": None, "safety": None},
        "b": {"correctness": None, "completeness": None, "clarity": None, "safety": None},
    },
    "verdict": None,
    "rationale": None,
    "sources": [],
    "reference": None,
}


def problems_for(*records):
    return validate.validate_records(list(enumerate(records, 1)))


def changed(base, **fields):
    record = copy.deepcopy(base)
    record.update(fields)
    return record


class ValidateTest(unittest.TestCase):
    def test_valid_records_have_no_problems(self):
        self.assertEqual(problems_for(DONE, TODO), [])

    def test_fixture_file_is_valid(self):
        records, read_problems = validate.load_records(FIXTURES / "evals_done.jsonl")
        self.assertEqual(read_problems, [])
        self.assertEqual(validate.validate_records(records), [])

    def test_todo_ignores_empty_markup_fields(self):
        # Незаполненные баллы и пустые источники для todo — норма.
        self.assertEqual(problems_for(TODO), [])

    def test_todo_structure_is_still_checked(self):
        broken = copy.deepcopy(TODO)
        del broken["scores"]["a"]["safety"]
        problems = problems_for(broken)
        self.assertTrue(any("scores.a.safety" in message for message in problems))

    def test_missing_field(self):
        broken = copy.deepcopy(DONE)
        del broken["reference"]
        problems = problems_for(broken)
        self.assertTrue(any("нет поля 'reference'" in message for message in problems))

    def test_unknown_domain(self):
        problems = problems_for(changed(DONE, domain="magic"))
        self.assertTrue(any("неизвестный domain" in message for message in problems))

    def test_unknown_task_type(self):
        problems = problems_for(changed(DONE, task_type="opinion"))
        self.assertTrue(any("неизвестный task_type" in message for message in problems))

    def test_unknown_status(self):
        problems = problems_for(changed(DONE, status="in_progress"))
        self.assertTrue(any("неизвестный status" in message for message in problems))

    def test_duplicate_id(self):
        problems = problems_for(DONE, changed(DONE, verdict="B"))
        self.assertTrue(any("дубль id" in message for message in problems))
        self.assertTrue(any(message.startswith("ios-900") for message in problems))

    def test_empty_prompt(self):
        problems = problems_for(changed(DONE, prompt="   "))
        self.assertTrue(any("'prompt'" in message for message in problems))

    def test_done_score_out_of_range(self):
        broken = copy.deepcopy(DONE)
        broken["scores"]["a"]["clarity"] = 7
        problems = problems_for(broken)
        self.assertTrue(any("вне диапазона 1..5" in message for message in problems))

    def test_done_score_not_filled(self):
        broken = copy.deepcopy(DONE)
        broken["scores"]["b"]["safety"] = None
        problems = problems_for(broken)
        self.assertTrue(any("не проставлен" in message for message in problems))

    def test_done_score_not_integer(self):
        broken = copy.deepcopy(DONE)
        broken["scores"]["b"]["safety"] = 4.5
        problems = problems_for(broken)
        self.assertTrue(any("scores.b.safety" in message for message in problems))

    def test_bool_is_not_a_score(self):
        broken = copy.deepcopy(DONE)
        broken["scores"]["a"]["safety"] = True
        problems = problems_for(broken)
        self.assertTrue(any("scores.a.safety" in message for message in problems))

    def test_extra_criterion_reported(self):
        broken = copy.deepcopy(DONE)
        broken["scores"]["a"]["style"] = 5
        problems = problems_for(broken)
        self.assertTrue(any("лишние критерии" in message for message in problems))

    def test_bad_verdict(self):
        problems = problems_for(changed(DONE, verdict="a"))
        self.assertTrue(any("verdict" in message for message in problems))

    def test_missing_verdict_for_done(self):
        problems = problems_for(changed(DONE, verdict=None))
        self.assertTrue(any("verdict" in message for message in problems))

    def test_tie_is_allowed(self):
        self.assertEqual(problems_for(changed(DONE, verdict="tie")), [])

    def test_empty_rationale(self):
        problems = problems_for(changed(DONE, rationale="  "))
        self.assertTrue(any("'rationale'" in message for message in problems))

    def test_empty_reference(self):
        problems = problems_for(changed(DONE, reference=None))
        self.assertTrue(any("'reference'" in message for message in problems))

    def test_sources_empty(self):
        problems = problems_for(changed(DONE, sources=[]))
        self.assertTrue(any("хотя бы один источник" in message for message in problems))

    def test_sources_without_url(self):
        problems = problems_for(changed(DONE, sources=["документация Apple"]))
        self.assertTrue(any("http(s)://" in message for message in problems))

    def test_sources_not_a_list(self):
        problems = problems_for(changed(DONE, sources="https://example.org"))
        self.assertTrue(any("списком" in message for message in problems))

    def test_broken_json_line(self):
        tmp = Path(tempfile.mkdtemp(prefix="validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "evals.jsonl"
        path.write_text(
            json.dumps(DONE, ensure_ascii=False) + "\n{не json}\n", encoding="utf-8"
        )
        records, problems = validate.load_records(path)
        self.assertEqual(len(records), 1)
        self.assertTrue(any("не разбирается как JSON" in message for message in problems))


class ValidateMainTest(unittest.TestCase):
    def run_main(self, records):
        tmp = Path(tempfile.mkdtemp(prefix="validate-main-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "evals.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = validate.main([str(path)])
        return code, out.getvalue(), err.getvalue()

    def test_exit_code_zero_on_clean_file(self):
        code, out, _ = self.run_main([DONE, TODO])
        self.assertEqual(code, 0)
        self.assertIn("Проблем не найдено", out)

    def test_exit_code_one_and_ids_listed_on_problems(self):
        code, _, err = self.run_main([changed(DONE, verdict="X")])
        self.assertEqual(code, 1)
        self.assertIn("ios-900", err)

    def test_empty_file_is_valid(self):
        code, out, _ = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("Проверено записей: 0", out)

    def test_missing_file_is_reported(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = validate.main([str(ROOT / "нет-такого-файла.jsonl")])
        self.assertEqual(code, 1)
        self.assertIn("не найден", err.getvalue())


if __name__ == "__main__":
    unittest.main()
