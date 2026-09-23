# llm-eval-ru — manual pairwise annotation of LLM answers

A study project: I manually compare the answers of two Russian-language models
(Alice and GigaChat) to the same questions, score them against a set of
criteria, pick the better answer, justify the choice with links to primary
sources, and write a reference answer.

## Why

Pairwise comparison of answers is the basic procedure in reinforcement learning
from human feedback: an annotator picks the better of two answers, a reward
model learns from those preferences, and that model then guides fine-tuning.
The scheme is described in "Training Language Models to Follow Instructions
with Human Feedback" — [arXiv:2203.02155](https://arxiv.org/abs/2203.02155).

What is reproduced here is exactly the human stage of that process — plus
things the original scheme does not have: a mandatory check of every claim
against a primary source, execution of the code from the answers, and a
reference answer for every task.

The value of the project is in the annotation and the justifications. The
scripts exist only to make the annotation reproducible, blind and verifiable.

**No model APIs are used:** answers are copied from the web chats by hand.

## How the work is organised

### 1. Create a task stub

```bash
python3 scripts/new_task.py ios false_premise
```

The script creates `data/raw/ios-001.md` with the next free number and a filled
header, and prints the path. I take the question from
[data/questions_bank.md](data/questions_bank.md).

### 2. Fill the file in by hand

I ask both models the same question in their web chats and paste the answers
into the "## Алиса" and "## GigaChat" sections as they are, code blocks
included. The format is [data/raw/_TEMPLATE.md](data/raw/_TEMPLATE.md).

### 3. Collect the tasks into a dataset

```bash
python3 scripts/ingest.py
```

For every new id, which answer becomes side **A** and which becomes **B** is
chosen at random (the `secrets` module). The record is appended to
`data/evals.jsonl`, and the "side → model" mapping to `data/blind_key.json`.
The script does not touch ids that are already in, so it can be run any number
of times.

> ⚠️ **Do not open `data/blind_key.json` before the annotation is finished.**
> It is kept in the repository (it is deliberately absent from `.gitignore`),
> but knowing whose answer is whose makes the annotation partial. Unblinding is
> the last step.

### 4. Annotate

I open `data/evals.jsonl` and fill the fields in by hand following the rules in
[GUIDELINES.md](GUIDELINES.md): scores, verdict, justification, sources,
reference answer. Then I change `status` to `"done"`.

### 5. Validate the format

```bash
python3 scripts/validate.py
```

For `todo` only the structure is checked. For `done` — that every score is an
integer 1–5, the verdict is one of {A, B, tie}, the justification and the
reference answer are non-empty, and there is at least one link in the sources.
Duplicate ids and unknown `domain`/`task_type` values are caught too. On
problems: exit code 1 and a list naming the ids.

### 6. Run the code from the answers

```bash
python3 scripts/run_snippets.py          # dry run: only shows what would be executed
python3 scripts/run_snippets.py --run    # actual execution
```

> ⚠️ **Nobody has reviewed the code in the LLM answers.** It can delete files,
> reach out to the network and do anything else. Run it deliberately, and
> preferably in an isolated environment. Without `--run` nothing is executed.

Each block runs in a temporary directory with a 10-second timeout:
`python → python3`, `swift → swift`, `c → clang` plus running the binary,
`java → java <File>.java`. If the tool is not on PATH, the code contains
`import SwiftUI` / `import UIKit`, or the language is not recognised — the
status is `skipped` with a reason. Statuses: `ok`, `compile_error`,
`runtime_error`, `timeout`, `skipped`. The result goes to
`data/snippet_results.json`.

### 7. Build the report

```bash
python3 scripts/report.py > REPORT.md            # blind report: sides A and B only
python3 scripts/report.py --unblind > REPORT.md  # + model names from blind_key.json
```

Only tasks with `status: "done"` make it into the report: averages per
criterion, the number of wins and ties, a breakdown by domain and task type,
the share of tasks where even the winner scores `correctness` < 4, and a
summary of the code runs.

## Data schema

`data/evals.jsonl` — one JSON record per line:

```json
{
  "id": "ios-001",
  "domain": "ios",
  "task_type": "false_premise",
  "prompt": "the question text",
  "answer_a": "the answer from side A",
  "answer_b": "the answer from side B",
  "status": "todo",
  "scores": {
    "a": {"correctness": null, "completeness": null, "clarity": null, "safety": null},
    "b": {"correctness": null, "completeness": null, "clarity": null, "safety": null}
  },
  "verdict": null,
  "rationale": null,
  "sources": [],
  "reference": null
}
```

| Field | Meaning |
|---|---|
| `id` | `<domain>-NNN`, unique |
| `domain` | `ios`, `networks`, `os_c`, `algorithms`, `git`, `history`, `law`, `finance`, `science`, `russian`, `everyday` |
| `task_type` | `fact`, `code`, `debug`, `false_premise`, `explain`, `outdated` |
| `status` | `todo` — not annotated, `done` — fully annotated |
| `scores.a` / `scores.b` | four criteria, integers 1–5 (or `null` before annotation) |
| `verdict` | `A`, `B` or `tie` |
| `rationale` | justification of the verdict with links to sources |
| `sources` | list of links to primary sources |
| `reference` | the reference answer, written by hand |

`data/blind_key.json` — the mapping between sides and models:

```json
{"ios-001": {"a": "GigaChat", "b": "Алиса"}}
```

The criteria and the 1–5 scale are described in [GUIDELINES.md](GUIDELINES.md)
(in Russian).

## Tests

```bash
python3 -m unittest discover -s tests
```

Python 3 standard library only, no dependencies. The tests work in temporary
directories on synthetic fixtures from `tests/fixtures/` and do not modify
`data/evals.jsonl` or `data/blind_key.json`.

## Repository layout

```
llm-eval-ru/
├── README.md                  this file
├── GUIDELINES.md              annotator's manual: criteria and scale
├── data/
│   ├── raw/_TEMPLATE.md       template for entering one task by hand
│   ├── raw/<domain>-NNN.md    tasks filled in by hand
│   ├── questions_bank.md      60 candidate questions without answers
│   ├── evals.jsonl            the annotation dataset
│   └── blind_key.json         the "side → model" key, do not open until the end
├── scripts/                   new_task, ingest, validate, run_snippets, report
└── tests/                     unittest + synthetic fixtures
```

## Results

<!-- The final table is moved here from REPORT.md once the annotation is
     finished and unblinding has been done (scripts/report.py --unblind). -->

Annotation is in progress. The final table will appear here when it is done.

| Model | Tasks | Wins | Ties | Correctness | Completeness | Clarity | Safety | Mean score |
|---|---|---|---|---|---|---|---|---|
| Alice | — | — | — | — | — | — | — | — |
| GigaChat | — | — | — | — | — | — | — | — |

Observations and an analysis of the characteristic errors will be added here
as well.
