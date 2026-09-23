#!/usr/bin/env python3
"""Собирает задания из data/raw/*.md в data/evals.jsonl.

Для каждого НОВОГО id случайно (модуль secrets) выбирается, чей ответ станет
стороной A, а чей — стороной B. Соответствие пишется в data/blind_key.json,
чтобы разметка велась вслепую. Уже внесённые id повторно не обрабатываются,
поэтому скрипт можно запускать сколько угодно раз (идемпотентность).

Использование:
    python3 scripts/ingest.py [--data-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

MODEL_A_SIDE = "Алиса"
MODEL_B_SIDE = "GigaChat"

DOMAINS = (
    "ios", "networks", "os_c", "algorithms", "git",
    "history", "law", "finance", "science", "russian", "everyday",
)
TASK_TYPES = ("fact", "code", "debug", "false_premise", "explain", "outdated")
CRITERIA = ("correctness", "completeness", "clarity", "safety")

# Заголовок -> ключ во внутреннем представлении.
SECTION_TITLES = {"Вопрос": "prompt", "Алиса": "alisa", "GigaChat": "gigachat"}

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
PLACEHOLDER_RE = re.compile(r"^<[^<>]*>$")

TEMPLATE_NAME = "_TEMPLATE.md"


class ParseError(ValueError):
    """Файл нельзя разобрать: сломана структура."""


class NotFilledError(ValueError):
    """Файл разобран, но содержательные поля ещё не заполнены человеком."""


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _is_placeholder(value: str) -> bool:
    """Пустое значение или незаменённый плейсхолдер вида <...> из шаблона."""
    stripped = value.strip()
    return not stripped or bool(PLACEHOLDER_RE.match(stripped))


def _trim(lines: list) -> str:
    """Убирает пустые строки по краям блока, внутреннее форматирование не трогает."""
    block = list(lines)
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(block)


def parse_raw(text: str, source: str = "<текст>") -> dict:
    """Разбирает один файл задания.

    Возвращает {id, domain, task_type, prompt, alisa, gigachat}.
    Строки '## ...' внутри блоков кода и заголовки с другими названиями
    считаются частью ответа, а не границей секции.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or lines[index].strip() != "---":
        raise ParseError("{}: нет шапки, ограниченной строками '---'".format(source))

    index += 1
    header = {}
    while index < len(lines) and lines[index].strip() != "---":
        raw_line = lines[index]
        if raw_line.strip():
            if ":" not in raw_line:
                raise ParseError(
                    "{}: строка шапки без двоеточия: {!r}".format(source, raw_line)
                )
            key, value = raw_line.split(":", 1)
            header[key.strip()] = value.strip()
        index += 1
    if index >= len(lines):
        raise ParseError("{}: шапка не закрыта строкой '---'".format(source))
    index += 1

    sections = {}
    current = None
    buffer = []
    fence = None
    for line in lines[index:]:
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
            else:
                heading = HEADING_RE.match(line)
                if heading and heading.group(1) in SECTION_TITLES:
                    if current is not None:
                        sections[current] = _trim(buffer)
                    current = SECTION_TITLES[heading.group(1)]
                    buffer = []
                    continue
        elif (
            match
            and line.strip() == match.group(1)
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
        ):
            fence = None
        if current is None:
            continue  # текст до первой известной секции игнорируем
        buffer.append(line)
    if current is not None:
        sections[current] = _trim(buffer)

    missing_sections = [
        title for title, key in SECTION_TITLES.items() if key not in sections
    ]
    if missing_sections:
        raise ParseError(
            "{}: нет секций: {}".format(source, ", ".join(missing_sections))
        )

    missing_header = [key for key in ("id", "domain", "task_type") if key not in header]
    if missing_header:
        raise ParseError(
            "{}: в шапке нет полей: {}".format(source, ", ".join(missing_header))
        )

    not_filled = [key for key in ("id", "domain", "task_type") if _is_placeholder(header[key])]
    not_filled += [
        title for title, key in SECTION_TITLES.items() if _is_placeholder(sections[key])
    ]
    if not_filled:
        raise NotFilledError(
            "{}: не заполнено: {}".format(source, ", ".join(not_filled))
        )

    if header["domain"] not in DOMAINS:
        raise ParseError(
            "{}: неизвестный domain {!r}".format(source, header["domain"])
        )
    if header["task_type"] not in TASK_TYPES:
        raise ParseError(
            "{}: неизвестный task_type {!r}".format(source, header["task_type"])
        )

    return {
        "id": header["id"],
        "domain": header["domain"],
        "task_type": header["task_type"],
        "prompt": sections["prompt"],
        "alisa": sections["alisa"],
        "gigachat": sections["gigachat"],
    }


def empty_record(parsed: dict, swap: bool) -> dict:
    """Собирает запись для evals.jsonl. Все поля разметки остаются пустыми."""
    if swap:
        answer_a, answer_b = parsed["gigachat"], parsed["alisa"]
    else:
        answer_a, answer_b = parsed["alisa"], parsed["gigachat"]
    return {
        "id": parsed["id"],
        "domain": parsed["domain"],
        "task_type": parsed["task_type"],
        "prompt": parsed["prompt"],
        "answer_a": answer_a,
        "answer_b": answer_b,
        "status": "todo",
        "scores": {
            "a": {name: None for name in CRITERIA},
            "b": {name: None for name in CRITERIA},
        },
        "verdict": None,
        "rationale": None,
        "sources": [],
        "reference": None,
    }


def side_key(swap: bool) -> dict:
    """Соответствие «сторона -> модель» для blind_key.json."""
    if swap:
        return {"a": MODEL_B_SIDE, "b": MODEL_A_SIDE}
    return {"a": MODEL_A_SIDE, "b": MODEL_B_SIDE}


def load_existing_ids(evals_path: Path) -> set:
    ids = set()
    if not evals_path.is_file():
        return ids
    with evals_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ParseError(
                    "{}: строка {} не разбирается как JSON ({})".format(
                        evals_path, number, error
                    )
                )
            ids.add(record.get("id"))
    return ids


def load_blind_key(path: Path) -> dict:
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def ingest(data_dir: Path, rng=None) -> dict:
    """Основная работа. rng() должен возвращать True/False (нужен тестам)."""
    if rng is None:
        rng = lambda: bool(secrets.randbelow(2))  # noqa: E731 — короткий адаптер

    raw_dir = data_dir / "raw"
    evals_path = data_dir / "evals.jsonl"
    key_path = data_dir / "blind_key.json"

    existing_ids = load_existing_ids(evals_path)
    blind_key = load_blind_key(key_path)

    added, skipped, not_filled, errors = [], [], [], []
    seen_in_batch = {}

    for path in sorted(raw_dir.glob("*.md")):
        if path.name == TEMPLATE_NAME:
            continue
        try:
            parsed = parse_raw(path.read_text(encoding="utf-8"), path.name)
        except NotFilledError as error:
            not_filled.append(str(error))
            continue
        except ParseError as error:
            errors.append(str(error))
            continue

        task_id = parsed["id"]
        if task_id in seen_in_batch:
            errors.append(
                "{}: id {!r} уже встречался в файле {}".format(
                    path.name, task_id, seen_in_batch[task_id]
                )
            )
            continue
        seen_in_batch[task_id] = path.name

        if task_id in existing_ids:
            skipped.append(task_id)
            continue

        swap = bool(rng())
        record = empty_record(parsed, swap)
        with evals_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        blind_key[task_id] = side_key(swap)
        existing_ids.add(task_id)
        added.append(task_id)

    key_path.write_text(
        json.dumps(blind_key, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "added": added,
        "skipped": skipped,
        "not_filled": not_filled,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Собрать задания из data/raw в evals.jsonl.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="каталог data/ (по умолчанию — каталог проекта; параметр нужен тестам)",
    )
    args = parser.parse_args(argv)
    data_dir = args.data_dir or default_data_dir()

    try:
        result = ingest(data_dir)
    except (OSError, ValueError) as error:
        print("Ошибка: {}".format(error), file=sys.stderr)
        return 1

    print("Добавлено новых заданий: {}".format(len(result["added"])))
    for task_id in result["added"]:
        print("  + {}".format(task_id))
    if result["skipped"]:
        print("Уже были в evals.jsonl: {}".format(len(result["skipped"])))
    for message in result["not_filled"]:
        print("  пропущено (не заполнено): {}".format(message))
    for message in result["errors"]:
        print("  ОШИБКА: {}".format(message), file=sys.stderr)
    if result["added"]:
        print("Соответствие «сторона -> модель» дописано в blind_key.json. Не открывайте его до конца разметки.")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
