#!/usr/bin/env python3
"""Создаёт заготовку задания из шаблона data/raw/_TEMPLATE.md.

Использование:
    python3 scripts/new_task.py <domain> <task_type>

Скрипт находит следующий свободный номер для домена, копирует шаблон
в data/raw/<domain>-NNN.md, проставляет шапку и печатает путь к файлу.
Содержательные поля (вопрос и ответы моделей) заполняет человек вручную.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DOMAINS = (
    "ios", "networks", "os_c", "algorithms", "git",
    "history", "law", "finance", "science", "russian", "everyday",
)
TASK_TYPES = ("fact", "code", "debug", "false_premise", "explain", "outdated")

TEMPLATE_NAME = "_TEMPLATE.md"


def default_data_dir() -> Path:
    """Каталог data/ рядом с корнем проекта."""
    return Path(__file__).resolve().parents[1] / "data"


def next_number(raw_dir: Path, domain: str) -> int:
    """Следующий свободный номер задания для домена (нумерация с 001)."""
    pattern = re.compile(r"^" + re.escape(domain) + r"-(\d+)\.md$")
    used = []
    for path in raw_dir.glob(domain + "-*.md"):
        match = pattern.match(path.name)
        if match:
            used.append(int(match.group(1)))
    return max(used, default=0) + 1


def fill_header(template_text: str, task_id: str, domain: str, task_type: str) -> str:
    """Подставляет значения в YAML-подобную шапку шаблона."""
    values = {"id": task_id, "domain": domain, "task_type": task_type}
    lines = template_text.replace("\r\n", "\n").split("\n")

    # Границы шапки: первая строка '---' и следующая за ней '---'.
    try:
        start = lines.index("---")
        end = lines.index("---", start + 1)
    except ValueError:
        raise ValueError("в шаблоне не найдена шапка, ограниченная строками '---'")

    for i in range(start + 1, end):
        key = lines[i].split(":", 1)[0].strip()
        if key in values:
            lines[i] = "{}: {}".format(key, values[key])
    return "\n".join(lines)


def create_task(domain: str, task_type: str, data_dir: Path) -> Path:
    raw_dir = data_dir / "raw"
    template = raw_dir / TEMPLATE_NAME
    if not template.is_file():
        raise FileNotFoundError("не найден шаблон {}".format(template))

    number = next_number(raw_dir, domain)
    task_id = "{}-{:03d}".format(domain, number)
    target = raw_dir / "{}.md".format(task_id)
    if target.exists():  # подстраховка от гонки и ручных правок
        raise FileExistsError("файл {} уже существует".format(target))

    text = fill_header(template.read_text(encoding="utf-8"), task_id, domain, task_type)
    target.write_text(text, encoding="utf-8")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Создать заготовку задания для ручной разметки.",
    )
    parser.add_argument("domain", choices=DOMAINS, help="домен задания")
    parser.add_argument("task_type", choices=TASK_TYPES, help="тип задания")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="каталог data/ (по умолчанию — каталог проекта; параметр нужен тестам)",
    )
    args = parser.parse_args(argv)

    data_dir = args.data_dir or default_data_dir()
    try:
        path = create_task(args.domain, args.task_type, data_dir)
    except (OSError, ValueError) as error:
        print("Ошибка: {}".format(error), file=sys.stderr)
        return 1

    print(path)
    print("Заполните секции «## Вопрос», «## Алиса», «## GigaChat» вручную.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
