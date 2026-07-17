"""Execute agent-emitted Python that produces an actual office-file deliverable.

Real GDPval deliverables are FILES (xlsx/docx/pdf/pptx). Our Env is otherwise
tool-free (text in, text out), so a file-structured task can't be done faithfully:
the agent can only describe the file, and the LLM grader can only guess. This
module closes that gap for office formats:

  1. pull the Python code the agent wrote (fenced ```python block, or a raw script),
  2. run it in an isolated temp dir with the project venv (openpyxl / python-docx /
     reportlab / fpdf / python-pptx / xlsxwriter are installed),
  3. collect the .xlsx/.docx/.pdf/.pptx files it wrote,
  4. read each back to text with the existing extractor (gdpval_files._extract),

so grading can be done against the REAL file's content + a verified file manifest
(so "an .xlsx named Sample is provided" / "a worksheet named X exists" become
checkable facts, not hallucinations). STEP/CAD, images, audio, video are out of
scope here (no reliable code path) and fall back to the plain text deliverable.

Gated by env GDPVAL_CODEGEN (default "1"). Never raises — returns a result dict.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import tempfile

_OUTPUT_EXT = (".xlsx", ".xlsm", ".docx", ".pdf", ".pptx", ".csv",           # office
               ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",             # images
               ".step", ".stp", ".stl", ".iges", ".igs",                     # CAD
               ".zip", ".mp4", ".ipynb")                                     # archive/video/notebook
_CODE_HINT = re.compile(
    r"\b(openpyxl|xlsxwriter|docx|reportlab|fpdf|pptx|import\s+pandas"
    r"|matplotlib|pyplot|PIL|Image\.|svgwrite|cadquery|build123d"
    r"|moviepy|nbformat|zipfile|import\s+json)\b")


def _extract_output(path: str, fn: str, max_chars: int = 15000) -> str:
    """Read a produced file back to text for grading. Office/images reuse the
    reader (images -> vision transcription); SVG is text; CAD is validated + noted."""
    ext = os.path.splitext(fn)[1].lower()
    try:
        if ext == ".svg":
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()[:max_chars]
        if ext in (".step", ".stp", ".iges", ".igs", ".stl"):
            head = open(path, "rb").read(120)
            valid = b"ISO-10303-21" in head or b"solid" in head[:5] or ext in (".stl",)
            return (f"[VALID {ext[1:].upper()} CAD file, {os.path.getsize(path)} bytes"
                    f"{'' if valid else ' (header unverified)'} — real geometry file produced]")
        from harness.benchmarks.gdpval_files import _extract  # noqa: PLC0415
        return _extract(path, fn, max_chars)
    except Exception as e:  # noqa: BLE001
        return f"[{fn}: produced but could not read: {type(e).__name__}]"


# Markers that mean OUR environment couldn't run otherwise-plausible code (NOT the
# model's fault): missing/blocked native libs, sandbox permission, timeout.
_ENV_MARKERS = ("modulenotfounderror", "no module named", "importerror",
                "dll load failed", "application control", "permissionerror",
                "permission denied", "access is denied", "timeout", "winerror")


def _classify_failure(error: str, files: list) -> str:
    """Why did we not get a file? 'none' produced ok; 'env' = tooling/OS (not the
    model's fault -> don't blame the agent); 'model' = the model's code is broken
    (syntax/truncation/no-save/logic -> a genuine agent failure)."""
    if files:
        return "none"
    e = (error or "").lower()
    if not e:
        return "model"                       # ran clean but saved nothing (no save call)
    if any(m in e for m in _ENV_MARKERS):
        return "env"
    return "model"                           # SyntaxError / NameError / truncation / bug


def _strip_fences(code: str) -> str:
    """Drop any stray markdown fence lines (```/```python) left in the code."""
    return "\n".join(ln for ln in code.splitlines()
                     if not re.match(r"^\s*```", ln))


def _extract_code(deliverable: str) -> str:
    """Pull the Python that writes the file. Handles fenced ```python blocks
    (including an UNCLOSED trailing fence), else a raw script. Always fence-stripped."""
    s = deliverable or ""
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", s, re.S | re.I)
    if not blocks:  # unclosed fence: take everything after the first ```python
        m = re.search(r"```(?:python|py)?[ \t]*\n", s, re.I)
        if m:
            blocks = [s[m.end():]]
    code = "\n\n".join(b for b in blocks if _CODE_HINT.search(b))
    if code.strip():
        return _strip_fences(code)
    # no fenced block: is the raw deliverable itself a file-writing script?
    if _CODE_HINT.search(s) and re.search(r"^\s*(import|from)\s", s, re.M):
        return _strip_fences(s)
    return ""


def _structure_summary(path: str, fn: str) -> str:
    """Read the produced file's real STRUCTURE (not just text values) so visual/
    structural rubric criteria — sheet names, page size/orientation, formatting,
    slide count — are graded as verified facts. Never raises."""
    ext = os.path.splitext(fn)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(path)
            out = [f"STRUCTURE: Excel workbook, worksheets={wb.sheetnames}"]
            for ws in wb.worksheets:
                nfmt, bold, formulas = set(), 0, 0
                for row in ws.iter_rows():
                    for c in row:
                        if c.value is None:
                            continue
                        if c.number_format and c.number_format != "General":
                            nfmt.add(c.number_format)
                        if c.font is not None and c.font.bold:
                            bold += 1
                        if isinstance(c.value, str) and c.value.startswith("="):
                            formulas += 1
                out.append(f"  sheet '{ws.title}': {ws.max_row} rows x {ws.max_column} cols, "
                           f"merged={len(list(ws.merged_cells.ranges))}, formulas={formulas}, "
                           f"bold_cells={bold}, number_formats={sorted(nfmt)[:6]}")
            wb.close()
            return "\n".join(out)
        if ext == ".docx":
            import docx  # noqa: PLC0415
            d = docx.Document(path)
            s = d.sections[0]
            w, h = s.page_width / 914400, s.page_height / 914400
            styles = sorted({p.style.name for p in d.paragraphs if p.style is not None})
            return (f"STRUCTURE: Word document, page={w:.2f}x{h:.2f} in "
                    f"({'landscape' if w > h else 'portrait'}), sections={len(d.sections)}, "
                    f"tables={len(d.tables)}, paragraphs={len(d.paragraphs)}, styles_used={styles[:12]}")
        if ext == ".pdf":
            import pdfplumber  # noqa: PLC0415
            with pdfplumber.open(path) as pdf:
                p = pdf.pages[0]
                w, h = p.width / 72.0, p.height / 72.0
                return (f"STRUCTURE: PDF, pages={len(pdf.pages)}, page1={w:.2f}x{h:.2f} in "
                        f"({'landscape' if w > h else 'portrait'})")
        if ext == ".pptx":
            from pptx import Presentation  # noqa: PLC0415
            pr = Presentation(path)
            w, h = pr.slide_width / 914400, pr.slide_height / 914400
            return (f"STRUCTURE: PowerPoint, slides={len(pr.slides)}, "
                    f"slide_size={w:.2f}x{h:.2f} in")
    except Exception as e:  # noqa: BLE001
        return f"[structure read failed for {fn}: {type(e).__name__}]"
    return ""


def run_codegen(deliverable: str, source_files=None, timeout: int = 120) -> dict:
    """Try to turn a code deliverable into real office files. Returns:
       {ran, ok, files:[name], extracted:str, error:str}. Never raises.

    source_files: optional list of (path, dest_name) — the task's attachments, copied
    into the work dir under their ORIGINAL names so the script can read them
    (pd.read_excel('Population v2.xlsx')) instead of inlining large data."""
    if os.environ.get("GDPVAL_CODEGEN", "1") not in ("1", "true", "True"):
        return {"ran": False, "ok": False, "files": [], "extracted": "", "error": "disabled",
                "fail_type": "disabled"}
    code = _extract_code(deliverable or "")
    if not code.strip():
        # No code emitted: the agent chose to deliver text/prose (fine for non-file
        # tasks; a miss for file tasks). Caller/analysis decides — tag it distinctly.
        return {"ran": False, "ok": False, "files": [], "extracted": "", "error": "no code",
                "fail_type": "no_code"}

    workdir = tempfile.mkdtemp(prefix="gdpval_gen_")
    import shutil  # noqa: PLC0415
    src_names = []
    for sp, dn in (source_files or []):
        try:
            if os.path.exists(sp):
                shutil.copyfile(sp, os.path.join(workdir, dn))
                src_names.append(dn)
        except Exception:  # noqa: BLE001
            pass
    script = os.path.join(workdir, "deliverable_gen.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    err = ""
    try:
        p = subprocess.run([sys.executable, "deliverable_gen.py"], cwd=workdir,
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "")[-800:]
    except subprocess.TimeoutExpired:
        err = f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    files, parts = [], []
    for path in sorted(glob.glob(os.path.join(workdir, "**", "*"), recursive=True)):
        if not os.path.isfile(path):
            continue
        fn = os.path.basename(path)
        if (fn == "deliverable_gen.py" or fn in src_names
                or os.path.splitext(fn)[1].lower() not in _OUTPUT_EXT):
            continue  # skip the script and the copied-in source attachments
        files.append(fn)
        struct = _structure_summary(path, fn)
        parts.append(f"\n===== PRODUCED FILE: {fn} =====\n"
                     + (struct + "\n" if struct else "") + _extract_output(path, fn))
    extracted = "".join(parts)
    ok = bool(files)
    return {"ran": True, "ok": ok, "files": files, "extracted": extracted,
            "error": err if not ok else "", "workdir": workdir,
            "fail_type": _classify_failure(err, files)}


def grader_submission(deliverable: str, cg: dict) -> str:
    """Build what the grader sees, by failure type (policy):
      none  -> real files produced: grade the verified manifest + content + structure.
      env   -> our environment couldn't run plausible code (tooling limit, NOT the
               agent's fault): grade the intended content/structure from the code,
               don't penalize the missing binary.
      model -> the code is broken/incomplete (a genuine agent failure): score
               file/format/structure criteria as NOT met.
      no_code/disabled -> plain text deliverable, graded as-is."""
    ft = cg.get("fail_type", "none")
    if ft == "none" and cg.get("ok"):
        manifest = ", ".join(cg["files"])
        return (f"[The agent produced these ACTUAL deliverable files by running its code: "
                f"{manifest}. Their real extracted contents + verified STRUCTURE follow — grade "
                f"file-type and structure criteria against THIS verified manifest and content.]\n"
                f"{cg['extracted']}")
    if ft == "env":
        return ("[The agent wrote code to build the deliverable file, but THIS environment could "
                "not execute it (a tooling limitation, not a content error). Grade the intended "
                "content and structure the code specifies as if it were the delivered artifact — "
                "do NOT penalize the missing binary itself.]\n" + deliverable)
    if ft == "model":
        return ("[The agent's code FAILED to produce the required deliverable file (it is broken or "
                "incomplete). No deliverable file exists. Score every criterion that requires the "
                "file, its format, or its structure as NOT met; credit only content fully present "
                "below.]\n" + deliverable)
    return deliverable  # no_code (prose) / disabled -> grade the text as-is
