#!/usr/bin/env python3
"""Запускает блоки кода из ответов моделей.

ВНИМАНИЕ: код в ответах LLM никто не проверял. Он может удалять файлы,
ходить в сеть и делать что угодно ещё. Запускайте только осознанно и лучше
в изолированном окружении. Без флага --run скрипт ничего не выполняет.

Использование:
    python3 scripts/run_snippets.py            # dry-run: что будет запущено
    python3 scripts/run_snippets.py --run      # реальный запуск

Поддерживаются python -> python3, swift -> swift, c -> clang + запуск бинарника,
java -> java <File>.java. Остальное, отсутствующие в PATH инструменты и код
с import SwiftUI / import UIKit получают статус skipped с причиной.
Результат: data/snippet_results.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WARNING = (
    "ВНИМАНИЕ: код из ответов LLM не проверен. Запускать его можно только осознанно, "
    "лучше в изолированном окружении."
)

DEFAULT_TIMEOUT = 10

FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_+#.-]*)\s*$")
FENCE_ANY_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

LANG_ALIASES = {
    "python": "python", "python3": "python", "py": "python",
    "swift": "swift",
    "c": "c",
    "java": "java",
}

TOOLS = {"python": "python3", "swift": "swift", "c": "clang", "java": "java"}

UI_IMPORTS = ("import SwiftUI", "import UIKit")

JAVA_PUBLIC_CLASS_RE = re.compile(r"\bpublic\s+(?:final\s+|abstract\s+)?class\s+(\w+)")
JAVA_CLASS_RE = re.compile(r"\bclass\s+(\w+)")
JAVA_COMPILE_ERROR_RE = re.compile(r"\.java:\d+: error:")
SWIFT_COMPILE_ERROR_RE = re.compile(r"\.swift:\d+:\d+: error:")
PYTHON_COMPILE_ERROR_RE = re.compile(r"^(SyntaxError|IndentationError|TabError):", re.MULTILINE)


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def extract_blocks(text: str):
    """Достаёт fenced-блоки кода. Возвращает список пар (язык, код)."""
    if not isinstance(text, str):
        return []
    blocks = []
    fence = None
    lang = ""
    buffer = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if fence is None:
            opening = FENCE_OPEN_RE.match(line)
            if opening:
                fence = opening.group(1)
                lang = opening.group(2).lower()
                buffer = []
                continue
            # Блок с «грязной» информационной строкой (```python title=...)
            any_fence = FENCE_ANY_RE.match(line)
            if any_fence:
                fence = any_fence.group(1)
                lang = line.strip()[len(any_fence.group(1)):].strip().split()[0].lower()
                buffer = []
            continue
        closing = FENCE_ANY_RE.match(line)
        if (
            closing
            and line.strip() == closing.group(1)
            and closing.group(1)[0] == fence[0]
            and len(closing.group(1)) >= len(fence)
        ):
            blocks.append((lang, "\n".join(buffer)))
            fence = None
            continue
        buffer.append(line)
    if fence is not None:  # незакрытый блок (обрезанный ответ) всё равно берём
        blocks.append((lang, "\n".join(buffer).rstrip("\n")))
    return blocks


def classify(lang: str, code: str):
    """Возвращает (нормализованный язык, причина пропуска или None)."""
    normalized = LANG_ALIASES.get((lang or "").strip().lower())
    if normalized is None:
        return None, "язык не распознан: {!r}".format(lang or "не указан")
    if not code.strip():
        return normalized, "пустой блок кода"
    if normalized == "swift":
        for marker in UI_IMPORTS:
            if marker in code:
                return normalized, "UI-код ({}) не запускается из консоли".format(marker)
    tool = TOOLS[normalized]
    if shutil.which(tool) is None:
        return normalized, "инструмент {} не найден в PATH".format(tool)
    return normalized, None


def _first_lines(text: str, limit: int = 20) -> str:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    return "\n".join(lines[:limit]).strip()


def _java_class_name(code: str) -> str:
    match = JAVA_PUBLIC_CLASS_RE.search(code) or JAVA_CLASS_RE.search(code)
    return match.group(1) if match else "Main"


def run_block(lang: str, code: str, timeout: int = DEFAULT_TIMEOUT):
    """Выполняет один блок кода во временном каталоге.

    Возвращает (статус, stderr). Статус: ok | compile_error | runtime_error | timeout.
    """
    with tempfile.TemporaryDirectory(prefix="snippet-") as workdir:
        work = Path(workdir)
        if lang == "python":
            source = work / "snippet.py"
            source.write_text(code, encoding="utf-8")
            status, stderr = _execute([TOOLS["python"], source.name], work, timeout)
            if status == "runtime_error" and PYTHON_COMPILE_ERROR_RE.search(stderr):
                status = "compile_error"
            return status, stderr
        if lang == "swift":
            source = work / "snippet.swift"
            source.write_text(code, encoding="utf-8")
            status, stderr = _execute([TOOLS["swift"], source.name], work, timeout)
            if status == "runtime_error" and SWIFT_COMPILE_ERROR_RE.search(stderr):
                status = "compile_error"
            return status, stderr
        if lang == "java":
            name = _java_class_name(code)
            source = work / "{}.java".format(name)
            source.write_text(code, encoding="utf-8")
            status, stderr = _execute([TOOLS["java"], source.name], work, timeout)
            if status == "runtime_error" and JAVA_COMPILE_ERROR_RE.search(stderr):
                status = "compile_error"
            return status, stderr
        if lang == "c":
            source = work / "snippet.c"
            source.write_text(code, encoding="utf-8")
            status, stderr = _execute(
                [TOOLS["c"], source.name, "-o", "snippet"], work, timeout
            )
            if status != "ok":
                return ("compile_error" if status == "runtime_error" else status), stderr
            return _execute(["./snippet"], work, timeout)
        return "skipped", "язык {!r} не поддерживается".format(lang)


def _execute(command, cwd: Path, timeout: int):
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return "timeout", "превышен таймаут {} c".format(timeout)
    except OSError as error:
        return "skipped", "не удалось запустить {}: {}".format(command[0], error)
    if completed.returncode == 0:
        return "ok", _first_lines(completed.stderr)
    return "runtime_error", _first_lines(completed.stderr)


def collect_tasks(records):
    """Собирает список блоков кода из всех записей: (id, сторона, язык, код)."""
    tasks = []
    for record in records:
        for side in ("a", "b"):
            for lang, code in extract_blocks(record.get("answer_" + side, "")):
                tasks.append(
                    {
                        "id": record.get("id"),
                        "side": side,
                        "lang": lang or "",
                        "code": code,
                    }
                )
    return tasks


def load_records(evals_path: Path):
    records = []
    if not evals_path.is_file():
        return records
    with evals_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Найти и (по флагу --run) выполнить блоки кода из ответов моделей.",
        epilog=WARNING,
    )
    parser.add_argument("--run", action="store_true", help="действительно выполнить код")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="каталог data/ (по умолчанию — каталог проекта; параметр нужен тестам)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="таймаут на блок в секундах (по умолчанию {})".format(DEFAULT_TIMEOUT),
    )
    args = parser.parse_args(argv)

    data_dir = args.data_dir or default_data_dir()
    print(WARNING, file=sys.stderr)

    records = load_records(data_dir / "evals.jsonl")
    tasks = collect_tasks(records)
    if not tasks:
        print("Блоков кода в ответах не найдено.")
        return 0

    results = []
    for task in tasks:
        normalized, skip_reason = classify(task["lang"], task["code"])
        label = "{} [{}] {}".format(task["id"], task["side"], task["lang"] or "без языка")
        if skip_reason is not None:
            print("{}: skipped — {}".format(label, skip_reason))
            results.append(
                {
                    "id": task["id"],
                    "side": task["side"],
                    "lang": task["lang"],
                    "status": "skipped",
                    "stderr": "",
                    "note": skip_reason,
                }
            )
            continue
        if not args.run:
            print(
                "{}: будет запущено через {} ({} строк кода)".format(
                    label, TOOLS[normalized], len(task["code"].split("\n"))
                )
            )
            continue
        status, stderr = run_block(normalized, task["code"], args.timeout)
        print("{}: {}".format(label, status))
        results.append(
            {
                "id": task["id"],
                "side": task["side"],
                "lang": task["lang"],
                "status": status,
                "stderr": stderr,
                "note": "",
            }
        )

    if not args.run:
        print("\nЭто была проверка без запуска (dry-run). Для выполнения добавьте --run.")
        return 0

    out_path = data_dir / "snippet_results.json"
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\nРезультаты записаны в {}".format(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
