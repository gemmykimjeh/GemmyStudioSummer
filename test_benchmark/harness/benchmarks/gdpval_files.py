"""gdpval_files — INPUT bridge for GDPval (turn every attachment into text the
text-only LLM can actually read, and hand the codegen sandbox the real files).

GDPval tasks ship reference attachments (.xlsx/.pdf/.png/.mp3/...). The agent is a
text-only LLM and cannot open them, so this module:

  * downloads each attachment (cached by URL hash so reruns are free);
  * extracts it to text — spreadsheets/documents/PDF/slides/notebooks locally,
    images/audio/video through a multimodal "vision" model (also cached);
  * exposes both the extracted TEXT (to inject into the prompt) and the local
    PATHS (so gdpval_codegen can copy the originals into the sandbox, letting the
    generated script do ``pd.read_excel('Key Indicators.xlsx')`` directly).

Tuning knobs (all optional; defaults work):

    GDPVAL_INJECT_FILES  1   master switch for injecting attachment text (0 = off)
    GDPVAL_FILE_MAXCHARS 15000  per-file cap on extracted text (costs prompt tokens)
    GDPVAL_FILES_CACHE   temp dir  where downloads + extractions are cached; set a
                                   persistent path so reruns skip download/transcribe
    GDPVAL_VISION        1   image/audio/video -> text via a multimodal model (0 = skip)
    GDPVAL_VISION_MODEL  gemini-3.1-flash-lite   model used for those transcriptions

Nothing here favours one arm over another — the same text is injected for every
agent (baseline ACE, pure LLM, FBL).
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import zipfile

_MAXCHARS = int(os.environ.get("GDPVAL_FILE_MAXCHARS", "15000"))
_VISION_MODEL = os.environ.get("GDPVAL_VISION_MODEL", "gemini-3.1-flash-lite")
_VISION_MAXTOK = 1500  # cap on the image/audio/video description call

_TEXT_EXTS = {".txt", ".csv", ".md", ".json", ".tsv", ".yaml", ".yml", ".py", ".overpassql"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
_CAD_EXTS = {".step", ".stp", ".iges", ".igs"}
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}
_AUDIO_FMT = {".wav": "wav", ".mp3": "mp3", ".m4a": "m4a", ".ogg": "ogg", ".flac": "flac"}


# --------------------------------------------------------------------------- #
# cache + download
# --------------------------------------------------------------------------- #
def _cache_dir() -> str:
    d = os.environ.get("GDPVAL_FILES_CACHE") or os.path.join(
        tempfile.gettempdir(), "gdpval_files_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _filename_of(url: str) -> str:
    tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return tail or "attachment"


def _download(url: str) -> str | None:
    """Download ``url`` into the cache (once). Return the local path, or None."""
    h = _url_hash(url)
    name = _filename_of(url)
    path = os.path.join(_cache_dir(), f"{h}__{name}")
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    try:
        import urllib.request  # noqa: PLC0415
        req = urllib.request.Request(url, headers={"User-Agent": "gdpval-harness/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            data = resp.read()
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"[gdpval_files] download failed {url}: {exc}")
        return None


# --------------------------------------------------------------------------- #
# vision / audio / video via a multimodal model (cached)
# --------------------------------------------------------------------------- #
def _vision_enabled() -> bool:
    return os.environ.get("GDPVAL_VISION", "1") != "0"


def _openai_client():
    """OpenAI-compatible client pointed at the Gemini proxy (or a real endpoint)."""
    try:
        from openai import OpenAI  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    base = os.environ.get("GEMINI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY") or "sk-proxy"
    try:
        return OpenAI(base_url=base, api_key=key) if base else OpenAI(api_key=key)
    except Exception:  # noqa: BLE001
        return None


def _cached_side(path: str, suffix: str) -> str:
    return path + suffix


def _cache_transcript(cache: str, text: str) -> str:
    """Cache a transcription, but never cache a failure/unavailable marker (so a
    rerun re-attempts it once the model/proxy is reachable)."""
    low = text.lower()
    if not (text.startswith("(") and any(w in low for w in ("failed", "unavailable"))):
        try:
            with open(cache, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:  # noqa: BLE001
            pass
    return text


def _describe_image(path: str) -> str:
    cache = _cached_side(path, ".vision.txt")
    if os.path.isfile(cache):
        return open(cache, encoding="utf-8", errors="replace").read()
    client = _openai_client()
    if client is None:
        return "(image; vision model unavailable — not transcribed)"
    ext = os.path.splitext(path)[1].lower()
    mime = _IMAGE_MIME.get(ext, "image/png")
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = client.chat.completions.create(
            model=_VISION_MODEL, max_tokens=_VISION_MAXTOK,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Transcribe and describe this image in full "
                 "detail: all text, numbers, tables, chart data, and layout."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}])
        text = (r.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        text = f"(image; transcription failed: {exc})"
    return _cache_transcript(cache, text)


def _describe_audio(path: str) -> str:
    cache = _cached_side(path, ".audio.txt")
    if os.path.isfile(cache):
        return open(cache, encoding="utf-8", errors="replace").read()
    client = _openai_client()
    if client is None:
        return "(audio; vision/audio model unavailable — not transcribed)"
    ext = os.path.splitext(path)[1].lower()
    fmt = _AUDIO_FMT.get(ext, "mp3")
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = client.chat.completions.create(
            model=_VISION_MODEL, max_tokens=_VISION_MAXTOK,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Transcribe this audio verbatim."},
                {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
            ]}])
        text = (r.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        text = f"(audio; transcription failed: {exc})"
    return _cache_transcript(cache, text)


def _describe_video(path: str) -> str:
    cache = _cached_side(path, ".video.txt")
    if os.path.isfile(cache):
        return open(cache, encoding="utf-8", errors="replace").read()
    client = _openai_client()
    if client is None:
        return "(video; vision model unavailable — not transcribed)"
    try:
        import imageio.v3 as iio  # noqa: PLC0415
        frames = iio.imread(path, index=None)  # all frames (or use a reader)
        n = len(frames)
        picks = [max(0, int(n * p)) for p in (0.1, 0.5, 0.9)] if n else []
        content = [{"type": "text", "text": "These are 3 frames (10%/50%/90%) sampled "
                    "from a video. Describe what the video shows in detail."}]
        from PIL import Image  # noqa: PLC0415
        import io  # noqa: PLC0415
        for idx in picks:
            buf = io.BytesIO()
            Image.fromarray(frames[idx]).save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        client = _openai_client()
        if client is None:
            return "(video; vision model unavailable — not transcribed)"
        r = client.chat.completions.create(
            model=_VISION_MODEL, max_tokens=_VISION_MAXTOK,
            messages=[{"role": "user", "content": content}])
        text = (r.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        text = f"(video; frame sampling/description failed: {exc})"
    return _cache_transcript(cache, text)


# --------------------------------------------------------------------------- #
# local extractors
# --------------------------------------------------------------------------- #
def _extract_xlsx(path: str) -> str:
    import openpyxl  # noqa: PLC0415
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"### sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if any(c is not None for c in row):
                out.append("\t".join("" if c is None else str(c) for c in row))
    wb.close()
    return "\n".join(out)


def _extract_docx(path: str) -> str:
    import docx  # noqa: PLC0415
    d = docx.Document(path)
    out = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for r in t.rows:
            out.append("\t".join(c.text for c in r.cells))
    return "\n".join(out)


def _extract_pdf(path: str) -> str:
    import pdfplumber  # noqa: PLC0415
    out = []
    with pdfplumber.open(path) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            out.append(f"### page {i}\n{pg.extract_text() or ''}")
    return "\n".join(out)


def _extract_pptx(path: str) -> str:
    from pptx import Presentation  # noqa: PLC0415
    prs = Presentation(path)
    out = []
    for i, slide in enumerate(prs.slides, 1):
        out.append(f"### slide {i}")
        for shp in slide.shapes:
            if shp.has_text_frame and shp.text_frame.text.strip():
                out.append(shp.text_frame.text)
            if shp.has_table:
                for r in shp.table.rows:
                    out.append("\t".join(c.text for c in r.cells))
    return "\n".join(out)


def _extract_ipynb(path: str) -> str:
    import nbformat  # noqa: PLC0415
    nb = nbformat.read(path, as_version=4)
    out = []
    for i, cell in enumerate(nb.cells):
        out.append(f"### cell {i} ({cell.cell_type})\n{cell.source}")
    return "\n".join(out)


def _extract_zip(path: str) -> str:
    out = []
    with zipfile.ZipFile(path) as zf:
        tmp = tempfile.mkdtemp(prefix="gdpval_zip_")
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            dest = zf.extract(member, tmp)
            out.append(f"===== ZIP MEMBER: {member} =====")
            out.append(_extract_path(dest))
    return "\n".join(out)


def _extract_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _extract_path(path: str) -> str:
    """Extract a single local file to text based on its extension."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            return _extract_xlsx(path)
        if ext == ".docx":
            return _extract_docx(path)
        if ext == ".pdf":
            return _extract_pdf(path)
        if ext == ".pptx":
            return _extract_pptx(path)
        if ext == ".ipynb":
            return _extract_ipynb(path)
        if ext == ".zip":
            return _extract_zip(path)
        if ext in _TEXT_EXTS:
            return _extract_text(path)
        if ext in _CAD_EXTS:
            return f"(CAD file {os.path.basename(path)}, {os.path.getsize(path)} bytes — validated, not rendered)"
        if ext in _IMAGE_EXTS:
            return _describe_image(path) if _vision_enabled() else "(image; vision disabled)"
        if ext in _AUDIO_EXTS:
            return _describe_audio(path) if _vision_enabled() else "(audio; vision disabled)"
        if ext in _VIDEO_EXTS:
            return _describe_video(path) if _vision_enabled() else "(video; vision disabled)"
    except Exception as exc:  # noqa: BLE001
        return f"(could not extract {os.path.basename(path)}: {exc})"
    return f"(unsupported file type {ext}; {os.path.getsize(path)} bytes)"


def _extract_cached(path: str) -> str:
    """Extract with a text cache keyed by the source file path (extraction is reused)."""
    cache = path + ".extract.txt"
    if os.path.isfile(cache):
        return open(cache, encoding="utf-8", errors="replace").read()
    text = _extract_path(path)
    try:
        with open(cache, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:  # noqa: BLE001
        pass
    return text


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def local_paths(urls: list[str]) -> list[str]:
    """Download the attachments and return their local paths (for the sandbox)."""
    paths = []
    for url in urls or []:
        p = _download(url)
        if p:
            paths.append(p)
    return paths


def files_text(urls: list[str]) -> str:
    """Extracted-text block for all attachments, ready to inject into the prompt."""
    if os.environ.get("GDPVAL_INJECT_FILES", "1") == "0":
        return ""
    blocks = []
    for url in urls or []:
        path = _download(url)
        if not path:
            blocks.append(f"===== ATTACHMENT (download failed): {_filename_of(url)} =====")
            continue
        name = _filename_of(url)
        text = _extract_cached(path)
        if len(text) > _MAXCHARS:
            text = text[:_MAXCHARS] + "\n...[truncated]"
        blocks.append(f"===== ATTACHMENT: {name} =====\n{text}")
    return "\n\n".join(blocks)


def augment_prompt(prompt: str, urls: list[str]) -> str:
    """Append the attachments' extracted text to the task prompt (if any)."""
    text = files_text(urls)
    if not text:
        return prompt
    return (f"{prompt}\n\n"
            f"# ATTACHED REFERENCE FILES (extracted contents; the originals are "
            f"available to your code under these exact filenames)\n{text}")
