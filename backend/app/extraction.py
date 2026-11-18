"""Turn uploaded files into text for retrieval: text, code, PDF, Office, OCR and local vision.

Optional capabilities degrade gracefully and say what is missing:

* OCR for images and scanned PDF pages: ``pytesseract`` + the Tesseract program
  (``pypdfium2`` renders scanned PDF pages). Install with ``pip install -r backend/requirements-ocr.txt``.
* Image understanding: an Ollama vision model named in ``FRIDAY_VISION_MODEL``
  (for example ``llava:7b``, ``qwen2.5vl:7b`` or ``moondream``).

Image metadata (size, format) is never stored as if it were image content.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import httpx

log = logging.getLogger("friday.extraction")

TEXT_TYPES = {
    "txt", "md", "markdown", "rst", "log", "ini", "cfg", "toml", "yaml", "yml", "env.example",
    "py", "js", "jsx", "ts", "tsx", "java", "kt", "go", "rs", "c", "h", "cpp", "hpp", "cs", "rb", "php",
    "swift", "scala", "sh", "ps1", "bat", "sql", "css", "scss", "vue", "svelte", "lua", "r", "dart",
    "xml", "tex",
}
IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"}
OFFICE_TYPES = {"docx", "pptx", "xlsx"}
STRUCTURED_TYPES = {"csv", "tsv", "json", "jsonl", "html", "htm"}
SUPPORTED_TYPES = TEXT_TYPES | IMAGE_TYPES | OFFICE_TYPES | STRUCTURED_TYPES | {"pdf"}

MIN_PAGE_CHARS = 25  # below this a PDF page is treated as scanned
OCR_MAX_PAGES = int(os.getenv("FRIDAY_OCR_MAX_PAGES", "50"))
VISION_TIMEOUT = float(os.getenv("FRIDAY_VISION_TIMEOUT", "180"))


class ExtractionError(Exception):
    """Raised with a user-facing message when a file cannot be turned into text."""


@dataclass
class Extracted:
    pages: list[tuple[int | None, str]] = field(default_factory=list)  # (page number or None, text)
    method: str = "text"
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(text for _, text in self.pages if text.strip())


def is_supported(file_type: str) -> bool:
    return file_type.lower() in SUPPORTED_TYPES


# ------------------------------------------------------------------ capabilities

def ocr_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def vision_model() -> str:
    return os.getenv("FRIDAY_VISION_MODEL", "").strip()


def capabilities() -> dict[str, object]:
    try:
        import pypdfium2  # noqa: F401
        pdf_render = True
    except ImportError:
        pdf_render = False
    return {"ocr": ocr_available(), "scanned_pdf_ocr": ocr_available() and pdf_render,
            "vision_model": vision_model() or None, "supported_types": sorted(SUPPORTED_TYPES)}


def ocr_image(image) -> str:
    import pytesseract

    languages = os.getenv("FRIDAY_OCR_LANG", "eng")
    return pytesseract.image_to_string(image, lang=languages).strip()


async def describe_image(data: bytes, model: str) -> str:
    """Ask a local Ollama vision model to describe the image and transcribe visible text."""
    ollama = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    payload = {
        "model": model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": ("Describe this image for a searchable knowledge base. List the main subjects, any "
                        "charts or diagrams and what they show, and transcribe all readable text exactly."),
            "images": [base64.b64encode(data).decode("ascii")],
        }],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(VISION_TIMEOUT, connect=5)) as client:
        response = await client.post(f"{ollama}/api/chat", json=payload)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            raise ExtractionError(f"Vision model '{model}' failed: {detail}")
        return (response.json().get("message") or {}).get("content", "").strip()


# ------------------------------------------------------------------ format readers

def _decode(data: bytes) -> str:
    if b"\0" in data[:8192]:
        raise ExtractionError("This file looks binary, not text.")
    for encoding in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_text(raw: str) -> str:
    parser = _HTMLText()
    parser.feed(raw)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx(path: Path) -> list[tuple[int | None, str]]:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
        if text.strip():
            paragraphs.append(text)
    return [(None, "\n\n".join(paragraphs))]


def _pptx(path: Path) -> list[tuple[int | None, str]]:
    pages = []
    with zipfile.ZipFile(path) as archive:
        slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for number, name in enumerate(slides, 1):
            root = ElementTree.fromstring(archive.read(name))
            lines = ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in root.iter(f"{A}p")]
            pages.append((number, "\n".join(line for line in lines if line.strip())))
    return pages


def _xlsx(path: Path) -> list[tuple[int | None, str]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise ExtractionError("Reading .xlsx needs openpyxl: pip install openpyxl") from exc
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    pages = []
    for number, sheet in enumerate(workbook.worksheets, 1):
        rows = []
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value) for value in row]
            if any(cells):
                rows.append(" | ".join(cells))
            if len(rows) >= 20000:
                break
        pages.append((number, f"Sheet: {sheet.title}\n" + "\n".join(rows)))
    return pages


def _delimited(raw: str, delimiter: str) -> str:
    rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
    if not rows:
        return ""
    header = rows[0]
    lines = []
    for row in rows[1:]:
        lines.append("; ".join(f"{h}: {v}" for h, v in zip(header, row) if v))
    return "\n".join(lines) or " | ".join(header)


async def _pdf(path: Path, data: bytes) -> Extracted:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise ExtractionError("This PDF is password-protected.") from exc
        pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Could not read this PDF: {exc}") from exc

    result = Extracted(pages, "pdf-text")
    scanned = [index for index, (_, text) in enumerate(pages) if len(text.strip()) < MIN_PAGE_CHARS]
    if not scanned:
        return result
    try:
        import pypdfium2 as pdfium
    except ImportError:
        pdfium = None
    if not ocr_available() or pdfium is None:
        result.warnings.append(f"{len(scanned)} page(s) look scanned; install OCR support "
                               "(Tesseract + backend/requirements-ocr.txt) to read them.")
        return result
    document = pdfium.PdfDocument(data)
    for index in scanned[:OCR_MAX_PAGES]:
        image = document[index].render(scale=2.5).to_pil()
        text = ocr_image(image)
        if text:
            pages[index] = (index + 1, text)
    if len(scanned) > OCR_MAX_PAGES:
        result.warnings.append(f"OCR stopped after {OCR_MAX_PAGES} scanned pages (FRIDAY_OCR_MAX_PAGES).")
    result.method = "pdf-text+ocr" if len(scanned) < len(pages) else "pdf-ocr"
    return result


async def _image(data: bytes) -> Extracted:
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ExtractionError("This image file could not be opened.") from exc

    sections: list[str] = []
    methods: list[str] = []
    warnings: list[str] = []
    model = vision_model()
    if model:
        try:
            description = await describe_image(data, model)
            if description:
                sections.append(f"Image description ({model}):\n{description}")
                methods.append("vision")
        except (ExtractionError, httpx.HTTPError) as exc:
            warnings.append(f"Vision model unavailable: {exc}")
    if ocr_available():
        text = ocr_image(image.convert("RGB"))
        if text:
            sections.append(f"Text found in image (OCR):\n{text}")
            methods.append("ocr")
    if not sections:
        hint = []
        if not model:
            hint.append("set FRIDAY_VISION_MODEL to a local vision model (e.g. `ollama pull llava:7b`)")
        if not ocr_available():
            hint.append("install Tesseract OCR")
        reason = " and ".join(hint) or "the image contains no readable text"
        raise ExtractionError(f"No content could be extracted from this image: {reason}. "
                              + " ".join(warnings))
    return Extracted([(None, "\n\n".join(sections))], "+".join(methods), warnings)


# ------------------------------------------------------------------ entry point

async def extract(path: Path, file_type: str) -> Extracted:
    file_type = file_type.lower()
    if not is_supported(file_type):
        raise ExtractionError(f"Unsupported file type '.{file_type}'.")
    data = path.read_bytes()
    if not data:
        raise ExtractionError("The file is empty.")
    if file_type == "pdf":
        return await _pdf(path, data)
    if file_type in IMAGE_TYPES:
        return await _image(data)
    try:
        if file_type == "docx":
            return Extracted(_docx(path), "docx")
        if file_type == "pptx":
            return Extracted(_pptx(path), "pptx")
        if file_type == "xlsx":
            return Extracted(_xlsx(path), "xlsx")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ExtractionError(f"This .{file_type} file is damaged or not a real Office document.") from exc

    raw = _decode(data)
    if file_type in {"html", "htm"}:
        return Extracted([(None, _html_text(raw))], "html")
    if file_type in {"csv", "tsv"}:
        return Extracted([(None, _delimited(raw, "\t" if file_type == "tsv" else ","))], file_type)
    if file_type == "json":
        try:
            raw = json.dumps(json.loads(raw), indent=1, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return Extracted([(None, raw)], "code" if file_type not in {"txt", "md", "markdown", "rst", "log"} else "text")
