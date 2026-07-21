# GDPval harness — grader changes + file I/O tooling runbook

Two things in one document:

1. **Part A — what changed in the rubric grader** and why (vs. the initial commit).
2. **Part B — a setup runbook** for the file input/output tooling, written so someone
   starting from a machine with **nothing installed** can reproduce this behaviour.

Everything below is FBL/harness side. It is shared by every arm (baseline ACE,
pure LLM, FBL), so it never favours one over another.

---

# Part A — Grader changes

## Problem 1 (the big one): the grader could not check that a file existed

### Original

The judge received the agent's **raw text** and nothing else:

```python
prompt = GRADER_TEMPLATE.format(
    prompt=task.prompt, submission=deliverable,   # <- just the agent's text
    rubric=json.dumps([...]),
)
```

There was **no file production and no file verification anywhere** in the original
grading path — no code execution, no produced-file manifest, no reference to the
gold deliverable's type.

### Why that produced invalid scores

GDPval deliverables are **files** (.xlsx / .docx / .pdf / .pptx), and rubrics contain
criteria such as:

> "The final deliverable is a single Excel workbook with file extension .xlsx."
> "The workbook contains a table of historical annual rate increases for 2020–2024."

The agent could not produce a file, so it **pasted the content inline** — a markdown
table that *looks like* a spreadsheet. The judge, having only that text and no way to
verify anything, would **read the pasted content and award the points anyway**.

That is the invalid case: **credit granted for a file that was never created.**
Format and structure criteria ("is a .xlsx", "has these sheets", "cells formatted as
currency") were judged against prose that merely resembled the artifact. The
benchmark silently degraded from "produce the deliverable" to "describe the
deliverable", and the scores were not honest.

### Fixed

The agent emits code, the harness **executes it**, a **real file** is produced, and the
judge is shown a verified manifest of what actually exists on disk:

```python
if ft == "none" and cg.get("ok"):
    manifest = ", ".join(cg["files"])
    return (f"[The agent produced these ACTUAL deliverable files by running its code: "
            f"{manifest}. Their real extracted contents + verified STRUCTURE follow — grade "
            f"file-type and structure criteria against THIS verified manifest and content.]\n"
            f"{cg['extracted']}")
```

Failure is reported honestly instead of hidden. When the agent's code is broken the
judge is **explicitly told not to give file credit**:

```python
if ft == "model":
    return ("[The agent's code FAILED to produce the required deliverable file (it is broken or "
            "incomplete). No deliverable file exists. Score every criterion that requires the "
            "file, its format, or its structure as NOT met; credit only content fully present "
            "below.]\n" + deliverable)
```

And when the *environment* (not the agent) is at fault — e.g. Windows Application
Control blocking the CAD kernel — the judge is told to grade intended content and not
penalise the missing binary:

```python
if ft == "env":
    return ("[The agent wrote code to build the deliverable file, but THIS environment could "
            "not execute it (a tooling limitation, not a content error). Grade the intended "
            "content and structure the code specifies as if it were the delivered artifact — "
            "do NOT penalize the missing binary itself.]\n" + deliverable)
```

## Problem 2: a malformed judge reply scored a good deliverable 0

### Original

One call, no retry, no temperature, no validation of the reply:

```python
g = client.messages.create(
    model=self.grader_model, max_tokens=self.max_grader_tokens,
    messages=[{"role": "user", "content": prompt}],
)
grades = _parse_grades(_text_of(g))
```

`_parse_grades` returns an **empty dict** when it cannot read the reply, and the scorer
treats **any criterion absent from the dict as unmet**. So a truncated or malformed
judge reply meant *every* criterion failed → **score 0**.

| # | Defect | Consequence |
|---|---|---|
| 1 | Malformed/truncated reply → `{}` → all criteria unmet | A good deliverable scores **0 from measurement noise**. Measured: a submission that scored 0.000 re-graded to **0.707** consistently |
| 2 | No `temperature` set (non-deterministic) | The same submission scores differently across runs — corrupts A/B comparison |
| 3 | No retry | One transient hiccup becomes the final score |
| 4 | No check on how much of the reply parsed | 3 of 94 criteria parsed still counted as a valid verdict; the other 91 auto-failed |

### Fixed — `grade_with_retry`

```python
def grade_with_retry(client, model, max_tokens, prompt, n_criteria, tries=3):
    grades: dict = {}
    need = max(1, n_criteria // 2)          # (3) at least half must parse
    for attempt in range(tries):            # (2) up to 3 attempts
        try:
            g = client.messages.create(
                model=model, max_tokens=max_tokens,
                temperature=0.0 if attempt == 0 else 0.5,   # (1) deterministic, then jitter
                messages=[{"role": "user", "content": prompt}],
            )
            grades = _parse_grades(_text_of(g))
        except Exception:                   # (4) transient error -> retry, not 0
            grades = {}
        if len(grades) >= need:
            break
    return grades
```

Jittering to 0.5 on retry matters: at temperature 0 a malformed reply reproduces
*identically*, so the jitter is what lets it escape.

**Measured effect:** 100 tasks produced **4 zeros** (the same pipeline without the
scaffold produced 14). Re-grading those 4 showed 3 were **consistently 0** — genuine
content failures, not noise. Zeros became a trustworthy signal.

> Correction to an earlier note: `max_grader_tokens` was **already 4096 originally**.
> The "1500 → 4096" claim was wrong — that 1500 belongs to the image/audio
> description call in `gdpval_files.py` and has nothing to do with the grader.

## Problem 3: a crashed script counted as a produced file

```python
# before
if files:
    return "none"      # success as long as SOME file exists — exit code ignored
```

A script that raised **after** writing a stub file was recorded as a clean success.
One task's script died on a `KeyError` after writing a near-empty workbook and was
scored as a delivered file meeting **1 of 43** criteria.

```python
# after — a non-empty `error` means the process exited non-zero or timed out
e = (error or "").lower()
if files and not e:
    return "none"
```

Plus an **empty-artifact** check: a file with no content at all (0 bytes, workbook with
no non-empty cell, document with no text, deck with no slides) is not counted as
produced, so the repair loop keeps working instead of accepting a stub.

Deliberately **not** implemented: any "output must be proportionate to input" rule.
A one-line KPI or an executive one-pager is a legitimate deliverable; comparing output
size to input size would fail those, and would be fitting to the subset of tasks that
happen to be row-preserving transforms.

## Still open

If all 3 attempts fail, `grade_with_retry` returns `{}` and the caller reads it as
"nothing met" → **0**. "The judge said nothing" and "the judge said no to everything"
are encoded identically. The original let the exception **propagate** (a visible task
error), so this change traded a loud failure for a silent wrong number.

This actually bit us: during a measurement run the proxy died, every reference file
came back `0.000 / n_met=0`, and the near-conclusion was "the harness under-credits
even the gold answers". Only reading the raw response revealed `WinError 10061`
(connection refused).

**Recommended fix:** return the verdict *plus* whether grading succeeded (raise, or
return `None`, after 3 failures) so the caller records **"measurement failed"** rather
than 0.

---

# Part B — File I/O tooling runbook (from a bare machine)

The agent is a **text-only LLM**. It cannot open a spreadsheet or save a `.docx`. Two
bridges make file work possible:

```
     attachments (.xlsx/.pdf/.png/.mp3/…)          deliverable file (.xlsx/.docx/…)
                    │                                          ▲
                    ▼                                          │
        [ gdpval_files.py ]  ──► text ──► LLM ──► ```python ──► [ gdpval_codegen.py ]
            INPUT bridge                                     OUTPUT bridge
                                                                  │
                                                                  ▼
                                                      read back ──► grader
```

- **INPUT** (`gdpval_files.py`): turn every attachment into text the model can read.
- **OUTPUT** (`gdpval_codegen.py`): execute the model's script in a sandbox, collect the
  real files, read them back, and hand a verified manifest to the grader.

## Step 1 — Python environment

Python 3.12. Everything below is CPU-only; no GPU needed.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
```

## Step 2 — Install the tooling

Two distinct sets. Both must be in the **same** interpreter, because the sandbox runs
the generated script with `sys.executable`.

```bash
# --- INPUT bridge: reading attachments -------------------------------------
pip install openpyxl==3.1.5          # .xlsx/.xlsm  (values + formulas)
pip install python-docx==1.2.0       # .docx
pip install pdfplumber==0.11.10      # .pdf  (text extraction)
pip install python-pptx==1.0.2       # .pptx (slide text + tables)
pip install pillow==11.3.0           # images -> re-encode for vision
pip install imageio==2.37.3          # video -> sample frames for vision
pip install nbformat==5.10.4         # .ipynb

# --- OUTPUT bridge: libraries the GENERATED code may import ----------------
pip install pandas==3.0.3 numpy==2.4.6
pip install xlsxwriter==3.2.9        # alt xlsx writer
pip install reportlab==5.0.0 fpdf2==2.8.7   # pdf writers
pip install matplotlib==3.11.0       # charts
pip install svgwrite==1.4.3          # .svg
pip install moviepy==2.2.1           # .mp4
# cadquery (.step CAD) is optional — see "known environment limits"

# --- LLM clients ----------------------------------------------------------
pip install openai==2.44.0 anthropic==0.116.0 litellm==1.91.0
```

**Why the output set matters:** the model writes `import openpyxl` / `import pandas`
in its deliverable. If a library is missing, the script dies with
`ModuleNotFoundError`, which the harness classifies as an **`env`** failure (see
Step 6) — i.e. the agent is not blamed, but you also never get the file. Install them
all or your scores are measuring your machine, not the agent.

## Step 3 — Configure the input bridge

`gdpval_files.py` turns each attachment into text. Defaults work; these env vars tune it:

| Variable | Default | What it does |
|---|---|---|
| `GDPVAL_INJECT_FILES` | `1` | Master switch for injecting attachment text into the prompt. `0` disables all file input. |
| `GDPVAL_FILE_MAXCHARS` | `15000` | Per-file cap on extracted text. Raise for large workbooks; costs prompt tokens. |
| `GDPVAL_FILES_CACHE` | temp dir | Where downloaded attachments and their extractions are cached. Set it to a persistent path so reruns do not re-download or re-transcribe. |
| `GDPVAL_VISION` | `1` | Enables image/audio/video → text via a multimodal model. `0` skips them. |
| `GDPVAL_VISION_MODEL` | `gemini-3.1-flash-lite` | Model used for those transcriptions. |

Format coverage:

| Kind | Extensions | How it becomes text |
|---|---|---|
| Spreadsheet | `.xlsx .xlsm` | openpyxl → tab-separated cells per sheet |
| Document | `.docx` | python-docx → paragraphs + tables |
| PDF | `.pdf` | pdfplumber → page text |
| Slides | `.pptx` | python-pptx → per-slide text and tables |
| Plain text | `.txt .csv .md .json .tsv .yaml .yml .py .overpassql` | read as-is |
| Image | `.png .jpg .jpeg .webp .gif` | vision model transcribes/describes (cached) |
| Audio | `.wav .mp3 .m4a .ogg .flac` | sent as `input_audio` → transcript (cached `.audio.txt`) |
| Video | `.mp4 .mov .avi .webm .mkv` | imageio samples 3 frames (10%/50%/90%) → vision (cached `.video.txt`) |
| Archive | `.zip` | unpacked, each member recursively extracted |
| Notebook | `.ipynb` | nbformat → cells |
| CAD | `.step .stp .iges .igs` | validated + noted (not rendered) |

Caching matters: vision and audio calls are the expensive part, and they are keyed by
file hash, so a rerun costs nothing.

## Step 4 — Configure the output bridge

`gdpval_codegen.py` is the sandbox.

| Variable | Default | What it does |
|---|---|---|
| `GDPVAL_CODEGEN` | `1` | `0` disables execution entirely — the deliverable is then graded as plain text (this is the "no tools" mode). |

How one task flows:

1. Extract the ```python block from the deliverable (handles an unclosed fence).
2. Create a temp workdir; **copy the task's attachments in under their original
   filenames** so the script can `pd.read_excel('Key Indicators.xlsx')` instead of
   retyping the data.
3. Run `sys.executable deliverable_gen.py` in that dir, `timeout=120s`, captured output.
4. Collect produced files by extension, **excluding the script itself and the copied-in
   sources**. Recognised output types:
   `.xlsx .xlsm .docx .pdf .pptx .csv`, `.png .jpg .jpeg .gif .webp .svg`,
   `.step .stp .stl .iges .igs`, `.zip .mp4 .ipynb`.
5. Read each produced file back to text (same readers as Step 3) and prepend a verified
   **structure summary** — sheet names, row×col counts, merged cells, number formats,
   bold cells, page size/orientation, slide count.
6. Hand the manifest + content to the grader.

**Spreadsheet formulas:** the structure summary reports formulas by *shape*, collapsing
a filled-down column to one line (`[936x] T2..T937  =Gn/In`). Reading a workbook with
`data_only=True` returns only cached values, so criteria like "computes X with a
formula" could never be verified — one gold file holds 3,744 formulas, so listing them
individually is not an option either.

## Step 5 — LLM routing (proxy)

All LLM traffic goes through a local LiteLLM proxy on `:4000` that fans out across
several API keys.

```bash
# configs/gemini_keys.env  (gitignored — real keys live only here)
GEMINI_KEY_1=...
GEMINI_KEY_2=...

# start it (leave running in its own terminal)
bash scripts/start_proxy.sh
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:4000/health   # expect 200
```

Point both surfaces at it:

```bash
export GEMINI_BASE_URL=http://localhost:4000/v1   # the agent brain
export ANTHROPIC_BASE_URL=http://localhost:4000   # the grader
export GEMINI_API_KEY=sk-proxy-rotation           # placeholder; the proxy holds real keys
```

⚠️ **Know what you are actually calling.** The proxy config ends with a catch-all:

```yaml
- model_name: "*"
  litellm_params: { model: gemini/gemini-3.1-flash-lite, api_key: os.environ/GEMINI_KEY_1 }
```

so a request for `claude-sonnet-4-6` is served by **gemini-3.1-flash-lite**. Verified by
asking the model directly ("I am a large language model, trained by Google"). Generator,
reflector, curator and grader are therefore all the **same** small model. If you want a
genuinely stronger grader, add a real provider entry — do not assume the model name is
honoured.

Launch on Windows via `python scripts/_proxy_launch.py`, not the `litellm.exe`
trampoline: Application Control blocks uv-generated `.exe` launchers, `python.exe` is
allowed.

## Step 6 — Understand the failure classes

Every task lands in exactly one bucket, and each is graded differently:

| `fail_type` | Meaning | Grading policy |
|---|---|---|
| `none` | Script ran clean **and** produced a non-empty file | Grade the verified manifest + content |
| `model` | Script crashed / timed out / saved nothing / wrote an empty file | Tell the judge **no file exists**; file/format/structure criteria are NOT met |
| `env` | Blocked by this machine's tooling (`ModuleNotFoundError`, DLL load failure, Application Control, permission) | Not the agent's fault — grade intended content, do not penalise the missing binary |
| `no_code` | Prose deliverable, no code emitted | Grade the text as-is |

Keep this distinction. Without it, a missing library on your machine looks identical to
a broken agent, and your comparison measures your install.

## Step 7 — Smoke test before trusting any number

```bash
python - <<'EOF'
import sys; sys.path.insert(0, '.')
from harness.benchmarks.gdpval_codegen import run_codegen
good  = "```python\nimport openpyxl\nwb=openpyxl.Workbook(); ws=wb.active\nws['A1']='Region'; ws['B1']='Sales'\nwb.save('out.xlsx')\n```"
crash = "```python\nimport openpyxl\nwb=openpyxl.Workbook(); ws=wb.active\nws['A1']='partial'\nwb.save('partial.xlsx')\nraise KeyError('x')\n```"
empty = "```python\nimport openpyxl\nopenpyxl.Workbook().save('blank.xlsx')\n```"
for name, code in [('clean', good), ('crash-after-write', crash), ('empty file', empty)]:
    r = run_codegen(code)
    print(f"{name:20} fail_type={r['fail_type']:6} ok={r['ok']!s:5} files={r['files']}")
EOF
```

Expected:

```
clean                fail_type=none   ok=True  files=['out.xlsx']
crash-after-write    fail_type=model  ok=False files=['partial.xlsx']
empty file           fail_type=model  ok=False files=[]
```

Read the two failure rows carefully — they check different fixes:

- **crash-after-write** writes a cell first, so the stub file is non-empty and *is*
  listed in `files`; it must still classify as `model`. If it reports `none`, the
  exit-code fix (Problem 3) is missing and crashed scripts are being scored as
  delivered files.
- **empty file** saves a workbook with no cells. It classifies as `model` **and**
  disappears from `files` — that is the empty-artifact check. Note that a crash which
  writes *nothing* trips both guards and also yields `files=[]`.

## Known environment limits

- **CAD (`.step`)**: `cadquery` depends on the OpenCASCADE kernel (`OCP.dll`), which
  Windows Smart App Control / Application Control blocks on this machine. Such tasks
  classify as **`env`** and are graded on intended content. They work on macOS.
- **torch**: pin `torch==2.10.0`. 2.13.0 is blocked by Smart App Control here, which
  breaks every `from ace import …`.
- **Offline runs**: set `HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1` and pre-warm the
  dataset + `all-mpnet-base-v2` sentence-transformer cache.
- **Encoding**: always run with `PYTHONUTF8=1` on Windows; several extractors emit
  non-ASCII and the default cp949 console encoding raises `UnicodeEncodeError`.
