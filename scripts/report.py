#!/usr/bin/env python3
"""Строит отчёт по размеченным заданиям (status="done").

Использование:
    python3 scripts/report.py > REPORT.md
    python3 scripts/report.py --unblind > REPORT.md   # + статистика по моделям

Без --unblind отчёт остаётся слепым: видны только стороны A и B.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

CRITERIA = ("correctness", "completeness", "clarity", "safety")
CRITERIA_RU = {
    "correctness": "Фактическая точность",
    "completeness": "Полнота",
    "clarity": "Ясность",
    "safety": "Безопасность",
}


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def load_records(evals_path: Path):
    records = []
    if not evals_path.is_file():
        return records
    with evals_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def mean(values):
    values = [value for value in values if isinstance(value, int)]
    if not values:
        return None
    return sum(values) / len(values)


def fmt(value) -> str:
    return "—" if value is None else "{:.2f}".format(value)


def share(part: int, total: int) -> str:
    if not total:
        return "—"
    return "{:.0f}%".format(100.0 * part / total)


def side_averages(records, side):
    return {
        name: mean([record.get("scores", {}).get(side, {}).get(name) for record in records])
        for name in CRITERIA
    }


def overall(averages):
    values = [value for value in averages.values() if value is not None]
    return sum(values) / len(values) if values else None


def count_verdicts(records):
    counts = {"A": 0, "B": 0, "tie": 0}
    for record in records:
        verdict = record.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def weak_winner_stats(records):
    """Задания, где у победителя correctness < 4. Ничьи не учитываются."""
    decided = [record for record in records if record.get("verdict") in ("A", "B")]
    weak = 0
    for record in decided:
        side = record["verdict"].lower()
        value = record.get("scores", {}).get(side, {}).get("correctness")
        if isinstance(value, int) and value < 4:
            weak += 1
    return weak, len(decided)


def group_table(records, field, title):
    lines = [
        "## Разбивка по {}".format(title),
        "",
        "| {} | Заданий | Побед A | Побед B | Ничьих | Средний балл A | Средний балл B |".format(field),
        "|---|---|---|---|---|---|---|",
    ]
    groups = {}
    for record in records:
        groups.setdefault(record.get(field, "—"), []).append(record)
    for key in sorted(groups):
        group = groups[key]
        counts = count_verdicts(group)
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                key,
                len(group),
                counts["A"],
                counts["B"],
                counts["tie"],
                fmt(overall(side_averages(group, "a"))),
                fmt(overall(side_averages(group, "b"))),
            )
        )
    lines.append("")
    return lines


def snippet_summary(path: Path):
    if not path.is_file():
        return []
    try:
        results = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["## Запуск кода из ответов", "", "Файл {} повреждён.".format(path.name), ""]
    counts = {}
    for item in results:
        counts[item.get("status", "—")] = counts.get(item.get("status", "—"), 0) + 1
    lines = [
        "## Запуск кода из ответов",
        "",
        "Всего блоков кода: {}.".format(len(results)),
        "",
        "| Статус | Количество |",
        "|---|---|",
    ]
    for status in sorted(counts):
        lines.append("| {} | {} |".format(status, counts[status]))
    lines.append("")
    lines.append("Код из ответов LLM не проверен: статусы получены запуском в песочнице скрипта.")
    lines.append("")
    return lines


def unblind_section(records, blind_key):
    lines = ["## Разблиндировка: статистика по моделям", ""]
    if not blind_key:
        lines += ["Файл blind_key.json пуст — сопоставить стороны с моделями нельзя.", ""]
        return lines

    models = {}
    for record in records:
        key = blind_key.get(record.get("id"))
        if not key:
            continue
        for side in ("a", "b"):
            model = key.get(side)
            if not model:
                continue
            bucket = models.setdefault(model, {"scores": [], "wins": 0, "ties": 0, "tasks": 0})
            bucket["tasks"] += 1
            bucket["scores"].append(record.get("scores", {}).get(side, {}))
            verdict = record.get("verdict")
            if verdict == "tie":
                bucket["ties"] += 1
            elif isinstance(verdict, str) and verdict.lower() == side:
                bucket["wins"] += 1

    if not models:
        lines += ["Для размеченных заданий нет записей в blind_key.json.", ""]
        return lines

    header = "| Модель | Заданий | Побед | Ничьих | " + " | ".join(
        CRITERIA_RU[name] for name in CRITERIA
    ) + " | Средний балл |"
    lines += [header, "|---" * (5 + len(CRITERIA)) + "|"]
    for model in sorted(models):
        bucket = models[model]
        averages = {
            name: mean([item.get(name) for item in bucket["scores"]]) for name in CRITERIA
        }
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                model,
                bucket["tasks"],
                bucket["wins"],
                bucket["ties"],
                " | ".join(fmt(averages[name]) for name in CRITERIA),
                fmt(overall(averages)),
            )
        )
    lines.append("")
    return lines


def build_report(records, snippets_path: Path, blind_key=None) -> str:
    done = [record for record in records if record.get("status") == "done"]
    todo = [record for record in records if record.get("status") == "todo"]

    lines = [
        "# Отчёт по ручной разметке",
        "",
        "Дата формирования: {}.".format(date.today().isoformat()),
        "",
        "Всего заданий: {}. Размечено (done): {}. В работе (todo): {}.".format(
            len(records), len(done), len(todo)
        ),
        "",
    ]

    if not done:
        lines += [
            "Размеченных заданий пока нет — считать нечего.",
            "",
        ]
        lines += snippet_summary(snippets_path)
        return "\n".join(lines).rstrip() + "\n"

    averages_a = side_averages(done, "a")
    averages_b = side_averages(done, "b")

    lines += [
        "## Средние баллы по критериям",
        "",
        "| Критерий | Сторона A | Сторона B |",
        "|---|---|---|",
    ]
    for name in CRITERIA:
        lines.append(
            "| {} ({}) | {} | {} |".format(
                CRITERIA_RU[name], name, fmt(averages_a[name]), fmt(averages_b[name])
            )
        )
    lines.append(
        "| **Средний балл** | **{}** | **{}** |".format(
            fmt(overall(averages_a)), fmt(overall(averages_b))
        )
    )
    lines.append("")

    counts = count_verdicts(done)
    lines += [
        "## Вердикты",
        "",
        "| Итог | Заданий | Доля |",
        "|---|---|---|",
        "| Победа A | {} | {} |".format(counts["A"], share(counts["A"], len(done))),
        "| Победа B | {} | {} |".format(counts["B"], share(counts["B"], len(done))),
        "| Ничья | {} | {} |".format(counts["tie"], share(counts["tie"], len(done))),
        "",
    ]

    lines += group_table(done, "domain", "доменам")
    lines += group_table(done, "task_type", "типам заданий")

    weak, decided = weak_winner_stats(done)
    lines += [
        "## Качество победителя",
        "",
        "Заданий с явным победителем (без ничьих): {}.".format(decided),
        "Из них у победителя correctness < 4: {} ({}).".format(weak, share(weak, decided)),
        "Такие задания означают, что «лучший» ответ всё равно содержит фактические проблемы.",
        "",
    ]

    lines += snippet_summary(snippets_path)

    if blind_key is not None:
        lines += unblind_section(done, blind_key)

    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Собрать отчёт по размеченным заданиям.")
    parser.add_argument(
        "--unblind",
        action="store_true",
        help="добавить статистику по моделям (читает blind_key.json)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="каталог data/ (по умолчанию — каталог проекта; параметр нужен тестам)",
    )
    args = parser.parse_args(argv)
    data_dir = args.data_dir or default_data_dir()

    records = load_records(data_dir / "evals.jsonl")

    blind_key = None
    if args.unblind:
        key_path = data_dir / "blind_key.json"
        if key_path.is_file() and key_path.read_text(encoding="utf-8").strip():
            blind_key = json.loads(key_path.read_text(encoding="utf-8"))
        else:
            blind_key = {}
        print("Разблиндировка включена: отчёт покажет имена моделей.", file=sys.stderr)

    sys.stdout.write(build_report(records, data_dir / "snippet_results.json", blind_key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
