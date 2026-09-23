#!/usr/bin/env python3
"""Проверяет data/evals.jsonl.

Для записей со status="todo" проверяется только структура: набор полей и их типы.
Для status="done" дополнительно проверяется содержательная часть:
  * все восемь баллов — целые числа от 1 до 5;
  * verdict ∈ {A, B, tie};
  * rationale и reference непустые;
  * в sources есть хотя бы одна ссылка вида http(s)://...

Также ловятся дубли id и неизвестные domain/task_type.
При любых проблемах скрипт печатает список и завершается с кодом 1.

Использование:
    python3 scripts/validate.py [путь/к/evals.jsonl]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOMAINS = (
    "ios", "networks", "os_c", "algorithms", "git",
    "history", "law", "finance", "science", "russian", "everyday",
)
TASK_TYPES = ("fact", "code", "debug", "false_premise", "explain", "outdated")
CRITERIA = ("correctness", "completeness", "clarity", "safety")
STATUSES = ("todo", "done")
VERDICTS = ("A", "B", "tie")

TEXT_FIELDS = ("id", "domain", "task_type", "prompt", "answer_a", "answer_b", "status")
REQUIRED_FIELDS = TEXT_FIELDS + ("scores", "verdict", "rationale", "sources", "reference")

URL_RE = re.compile(r"https?://\S+")


def default_evals_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "evals.jsonl"


def load_records(path: Path):
    """Читает JSONL. Возвращает (записи, проблемы чтения)."""
    records, problems = [], []
    if not path.is_file():
        return records, ["файл {} не найден".format(path)]
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                problems.append("строка {}: не разбирается как JSON ({})".format(number, error))
                continue
            if not isinstance(record, dict):
                problems.append("строка {}: ожидался объект JSON".format(number))
                continue
            records.append((number, record))
    return records, problems


def _label(number: int, record: dict) -> str:
    task_id = record.get("id")
    if isinstance(task_id, str) and task_id:
        return task_id
    return "строка {} (без id)".format(number)


def check_structure(number: int, record: dict, problems: list) -> None:
    where = _label(number, record)

    for field in REQUIRED_FIELDS:
        if field not in record:
            problems.append("{}: нет поля {!r}".format(where, field))

    for field in TEXT_FIELDS:
        value = record.get(field)
        if field in record and (not isinstance(value, str) or not value.strip()):
            problems.append("{}: поле {!r} должно быть непустой строкой".format(where, field))

    if isinstance(record.get("domain"), str) and record["domain"] not in DOMAINS:
        problems.append("{}: неизвестный domain {!r}".format(where, record["domain"]))
    if isinstance(record.get("task_type"), str) and record["task_type"] not in TASK_TYPES:
        problems.append("{}: неизвестный task_type {!r}".format(where, record["task_type"]))
    if isinstance(record.get("status"), str) and record["status"] not in STATUSES:
        problems.append("{}: неизвестный status {!r}".format(where, record["status"]))

    scores = record.get("scores")
    if not isinstance(scores, dict):
        problems.append("{}: поле 'scores' должно быть объектом".format(where))
    else:
        for side in ("a", "b"):
            side_scores = scores.get(side)
            if not isinstance(side_scores, dict):
                problems.append("{}: нет объекта scores.{}".format(where, side))
                continue
            for name in CRITERIA:
                if name not in side_scores:
                    problems.append("{}: нет критерия scores.{}.{}".format(where, side, name))
                    continue
                value = side_scores[name]
                if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                    problems.append(
                        "{}: scores.{}.{} должен быть целым числом или null".format(where, side, name)
                    )
            unknown = sorted(set(side_scores) - set(CRITERIA))
            if unknown:
                problems.append(
                    "{}: лишние критерии в scores.{}: {}".format(where, side, ", ".join(unknown))
                )

    if not isinstance(record.get("sources", []), list):
        problems.append("{}: поле 'sources' должно быть списком".format(where))

    for field in ("verdict", "rationale", "reference"):
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            problems.append("{}: поле {!r} должно быть строкой или null".format(where, field))


def check_done(number: int, record: dict, problems: list) -> None:
    """Дополнительные проверки для полностью размеченных заданий."""
    where = _label(number, record)

    scores = record.get("scores")
    if isinstance(scores, dict):
        for side in ("a", "b"):
            side_scores = scores.get(side)
            if not isinstance(side_scores, dict):
                continue
            for name in CRITERIA:
                value = side_scores.get(name)
                if isinstance(value, bool) or not isinstance(value, int):
                    problems.append(
                        "{}: scores.{}.{} не проставлен (нужно целое 1..5)".format(where, side, name)
                    )
                elif not 1 <= value <= 5:
                    problems.append(
                        "{}: scores.{}.{} = {} вне диапазона 1..5".format(where, side, name, value)
                    )

    verdict = record.get("verdict")
    if verdict not in VERDICTS:
        problems.append(
            "{}: verdict должен быть одним из {}, получено {!r}".format(
                where, "/".join(VERDICTS), verdict
            )
        )

    for field in ("rationale", "reference"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append("{}: поле {!r} не должно быть пустым".format(where, field))

    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("{}: в sources нужен хотя бы один источник".format(where))
    elif not any(isinstance(item, str) and URL_RE.search(item) for item in sources):
        problems.append("{}: ни один источник в sources не содержит ссылку http(s)://".format(where))


def validate_records(records) -> list:
    """records — список пар (номер строки, запись). Возвращает список проблем."""
    problems = []
    seen = {}
    for number, record in records:
        check_structure(number, record, problems)
        if record.get("status") == "done":
            check_done(number, record, problems)
        task_id = record.get("id")
        if isinstance(task_id, str) and task_id:
            if task_id in seen:
                problems.append(
                    "{}: дубль id (строки {} и {})".format(task_id, seen[task_id], number)
                )
            else:
                seen[task_id] = number
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Проверить корректность evals.jsonl.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="путь к evals.jsonl (по умолчанию data/evals.jsonl проекта)",
    )
    args = parser.parse_args(argv)
    path = args.path or default_evals_path()

    records, problems = load_records(path)
    problems = problems + validate_records(records)

    total = len(records)
    done = sum(1 for _, record in records if record.get("status") == "done")
    todo = sum(1 for _, record in records if record.get("status") == "todo")

    if problems:
        print("Найдено проблем: {}".format(len(problems)), file=sys.stderr)
        for problem in problems:
            print("  - {}".format(problem), file=sys.stderr)
        return 1

    print("Проверено записей: {} (done: {}, todo: {}). Проблем не найдено.".format(total, done, todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
