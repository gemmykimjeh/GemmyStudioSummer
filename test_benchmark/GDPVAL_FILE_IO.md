# GDPval reference-file reading — setup guide (do it yourself, no pull needed)

Lets the agent actually **read task attachments** (xlsx / pdf / docx / images) instead of
hallucinating "simulated" data (which the rubric scores 0). This is exactly the 3 steps that
were applied to add it — reproduce them on your checkout.

**Why it's this small:** the change lives at the **benchmark Env level**, not in the agents.
So it applies to *every* gdpval agent automatically (`ace_gdpval`, `ace_dual_gdpval`, …).
**`ace_gdpval.py` / `ace_dual_gdpval.py` are NOT touched (0 lines).** Up to this feature the
code is identical to yours — you only need: 1 pip install + 1 new file + 1 small edit.

---

## Step 1 — install extraction libraries

```bash
# from test_benchmark/  (use your venv's pip, or uv pip install --python .venv/Scripts/python.exe ...)
pip install openpyxl python-docx pdfplumber
```
(`pdfplumber` pulls `pdfminer.six` + `pypdfium2`. Image support reuses the `openai` SDK you
already have. Versions used: openpyxl 3.1.5, python-docx 1.2.0, pdfplumber 0.11.10.)

Optionally add them to the `gdpval` extra in `pyproject.toml` so it's permanent:
```toml
gdpval = ["anthropic>=0.40", "datasets>=2.0",
          "openpyxl>=3.1", "python-docx>=1.1", "pdfplumber>=0.11"]
```

## Step 2 — create `harness/benchmarks/gdpval_files.py`

New file. Paste this verbatim:

```python
"""Fetch GDPval reference files and extract their text so the agent can actually
read attachments (xlsx / pdf / docx / csv / txt) + images (via vision) instead of
hallucinating. Cached under external/gdpval_files/. Injected into the task
observation by gdpval.py. Toggles: GDPVAL_INJECT_FILES (default 1),
GDPVAL_VISION (default 1), GDPVAL_FILE_MAXCHARS (default 15000),
GDPVAL_VISION_MODEL (default gemini-3.1-flash-lite), GDPVAL_FILES_CACHE.
"""
from __future__ import annotations

import base64
import hashlib
import os
import urllib.parse
import urllib.request

_TEXT_EXT = {".txt", ".csv", ".md", ".json", ".tsv"}
_IMG_EXT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif"}
_DEFAULT_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "external", "gdpval_files")


def _cache_dir() -> str:
    return os.environ.get("GDPVAL_FILES_CACHE", _DEFAULT_CACHE)


def _fetch(url: str) -> tuple[str, str]:
    fn = urllib.parse.unquote(os.path.basename(url.split("?")[0])) or "file"
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    cache = _cache_dir()
    path = os.path.join(cache, f"{h}_{fn}")
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "gdpval-harness"})
        data = urllib.request.urlopen(req, timeout=90).read()
        with open(path, "wb") as f:
            f.write(data)
    return path, fn


def _describe_image(path: str, fn: str, max_chars: int) -> str:
    """Vision->text: Gemini (multimodal) transcribes/describes the image. Cached."""
    if os.environ.get("GDPVAL_VISION", "1") not in ("1", "true", "True"):
        return f"[{fn}: image, vision disabled (GDPVAL_VISION=0)]"
    mime = _IMG_EXT[os.path.splitext(fn)[1].lower()]
    desc_path = path + ".vision.txt"
    if os.path.exists(desc_path):
        with open(desc_path, encoding="utf-8") as f:
            return f.read()
    try:
        from openai import OpenAI
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        base_url = os.environ.get("GEMINI_BASE_URL",
                                  "https://generativelanguage.googleapis.com/v1beta/openai/")
        api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                   or os.environ.get("GEMINI_KEY_1") or "sk-proxy-rotation")
        model = os.environ.get("GDPVAL_VISION_MODEL", "gemini-3.1-flash-lite")
        client = OpenAI(base_url=base_url, api_key=api_key)
        r = client.chat.completions.create(
            model=model, max_tokens=1500, temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    "Transcribe and describe this image in full detail for a downstream "
                    "agent that cannot see it. Include ALL visible text verbatim, any tables "
                    "as text, chart/graph values and trends, layout, and key visual facts. "
                    "Be thorough and factual."},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
            ]}])
        desc = ("[IMAGE auto-transcribed by vision model]\n"
                + (r.choices[0].message.content or "").strip())[:max_chars]
        try:
            with open(desc_path, "w", encoding="utf-8") as f:
                f.write(desc)
        except Exception:
            pass
        return desc
    except Exception as e:
        return f"[{fn}: image, vision failed: {type(e).__name__}: {e}]"


def _extract(path: str, fn: str, max_chars: int) -> str:
    ext = os.path.splitext(fn)[1].lower()
    try:
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            out: list[str] = []
            size = 0
            for ws in wb.worksheets:
                out.append(f"# Sheet: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    line = "\t".join("" if c is None else str(c) for c in row)
                    out.append(line)
                    size += len(line)
                    if size > max_chars:
                        out.append("... [truncated]")
                        wb.close()
                        return "\n".join(out)
            wb.close()
            return "\n".join(out)
        if ext == ".docx":
            import docx
            d = docx.Document(path)
            paras = [p.text for p in d.paragraphs if p.text]
            for tbl in d.tables:
                for r in tbl.rows:
                    paras.append("\t".join(c.text for c in r.cells))
            return "\n".join(paras)
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                txt = []
                for pg in pdf.pages:
                    txt.append(pg.extract_text() or "")
                    if sum(len(t) for t in txt) > max_chars:
                        break
                return "\n".join(txt)
        if ext in _IMG_EXT:
            return _describe_image(path, fn, max_chars)
        if ext in _TEXT_EXT:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        return f"[{ext or 'binary'} file, {os.path.getsize(path)} bytes -- not text-extractable]"
    except Exception as e:
        return f"[could not extract {fn}: {type(e).__name__}: {e}]"


def reference_files_text(metadata: dict | None) -> str:
    """Injectable block with the extracted content of a task's reference files
    ('' if none / disabled). Never raises."""
    if os.environ.get("GDPVAL_INJECT_FILES", "1") not in ("1", "true", "True"):
        return ""
    urls = (metadata or {}).get("reference_file_urls") or []
    if not urls:
        return ""
    max_chars = int(os.environ.get("GDPVAL_FILE_MAXCHARS", "15000"))
    parts: list[str] = []
    for url in urls:
        try:
            path, fn = _fetch(url)
            text = _extract(path, fn, max_chars)[:max_chars]
            parts.append(f"\n===== ATTACHED FILE: {fn} =====\n{text}")
        except Exception as e:
            parts.append(f"\n===== ATTACHED FILE: {os.path.basename(url)} (fetch failed: {e}) =====")
    return ("\n\n--- ATTACHED REFERENCE FILES (extracted; use this real data, "
            "do NOT simulate) ---" + "".join(parts))
```

## Step 3 — edit `harness/benchmarks/gdpval.py`

Find the gdpval **Env class** (the one with `observation()` / `instructions()` / `tools()`)
and change its `__init__` + `observation`:

```python
# BEFORE
    def __init__(self, task: Task) -> None:
        self.task = task

    def observation(self) -> str:
        return self.task.prompt

# AFTER
    def __init__(self, task: Task) -> None:
        self.task = task
        self._obs: str | None = None

    def observation(self) -> str:
        if self._obs is None:
            from harness.benchmarks.gdpval_files import reference_files_text  # noqa: PLC0415
            self._obs = self.task.prompt + reference_files_text(self.task.metadata)
        return self._obs
```

That's it — every gdpval agent now receives the file content through `observation()`.

---

## Coverage
- **Text extraction (no API):** xlsx (openpyxl), pdf (pdfplumber), docx (python-docx), csv/txt.
- **Images (png/jpg/jpeg/webp):** transcribed to text by a **Gemini vision** call, cached per file.
  → needs `GEMINI_BASE_URL` (your rotation proxy) or `GEMINI_API_KEY` set.
- **Not covered:** psd / audio (wav, mp3) / video (mp4) / step / zip → noted as `[binary...]` only.

## Env toggles (all optional, defaults shown)
| var | default | meaning |
|---|---|---|
| `GDPVAL_INJECT_FILES` | `1` | master on/off |
| `GDPVAL_VISION` | `1` | image transcription on/off |
| `GDPVAL_FILE_MAXCHARS` | `15000` | per-file text cap |
| `GDPVAL_VISION_MODEL` | `gemini-3.1-flash-lite` | vision model |
| `GDPVAL_FILES_CACHE` | `external/gdpval_files/` | download cache (gitignored) |

## Verify (no run needed)
```bash
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 python -c "
import harness.benchmarks.gdpval
from harness.registry import get_benchmark
b=get_benchmark('gdpval'); b=b() if isinstance(b,type) else b
t=[x for x in b.load_tasks() if (x.metadata or {}).get('reference_file_urls')][0]
from harness.benchmarks.gdpval_files import reference_files_text
print(reference_files_text(t.metadata)[:400])
"
```
Should print the extracted spreadsheet/doc content (not empty).

## Notes
- Cache is gitignored → first run re-downloads reference files (once), then caches.
- Cost: file injection adds prompt tokens only (**RPD/call count unchanged, TPM higher**).
  Image vision = +1 Gemini call per image (cached, one-time).
- A/B stays fair: baseline & FBL use the same Env, so both see the files.
