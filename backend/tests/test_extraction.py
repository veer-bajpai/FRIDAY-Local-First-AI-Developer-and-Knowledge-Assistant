"""Document, OCR and image-understanding ingestion."""
from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.app import extraction
from backend.app.extraction import ExtractionError, extract

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _text_image(text: str) -> Image.Image:
    image = Image.new("RGB", (900, 160), "white")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 44)
    except OSError:
        font = ImageFont.load_default(size=44)
    ImageDraw.Draw(image).text((20, 50), text, fill="black", font=font)
    return image


def _docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")


def _pptx(path: Path, slides: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for number, text in enumerate(slides, 1):
            archive.writestr(f"ppt/slides/slide{number}.xml",
                             f"<p:sld xmlns:p='p' {A}><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>")


def run(coro):
    return asyncio.run(coro)


def test_office_formats(tmp_path):
    _docx(tmp_path / "a.docx", ["Quarterly plan", "Ship the indexer"])
    assert "Ship the indexer" in run(extract(tmp_path / "a.docx", "docx")).text
    _pptx(tmp_path / "b.pptx", ["Intro slide", "Roadmap slide"])
    result = run(extract(tmp_path / "b.pptx", "pptx"))
    assert result.pages == [(1, "Intro slide"), (2, "Roadmap slide")]
    (tmp_path / "c.csv").write_text("name,role\nAsha,engineer\n")
    assert "name: Asha; role: engineer" in run(extract(tmp_path / "c.csv", "csv")).text
    (tmp_path / "d.html").write_text("<html><script>x=1</script><p>Visible text</p></html>")
    assert run(extract(tmp_path / "d.html", "html")).text == "Visible text"


def test_bad_and_unsupported_files(tmp_path):
    (tmp_path / "x.docx").write_bytes(b"not a zip")
    with pytest.raises(ExtractionError, match="damaged"):
        run(extract(tmp_path / "x.docx", "docx"))
    (tmp_path / "y.exe").write_bytes(b"MZ")
    with pytest.raises(ExtractionError, match="Unsupported"):
        run(extract(tmp_path / "y.exe", "exe"))
    (tmp_path / "z.txt").write_bytes(b"\0\0binary")
    with pytest.raises(ExtractionError, match="binary"):
        run(extract(tmp_path / "z.txt", "txt"))


@pytest.mark.skipif(not extraction.ocr_available(), reason="Tesseract not installed")
def test_real_ocr_on_image_and_scanned_pdf(tmp_path, monkeypatch):
    monkeypatch.delenv("FRIDAY_VISION_MODEL", raising=False)
    image = _text_image("INVOICE 4471 PAID")
    image.save(tmp_path / "scan.png")
    result = run(extract(tmp_path / "scan.png", "png"))
    assert result.method == "ocr" and "4471" in result.text

    image.save(tmp_path / "scan.pdf", "PDF")
    pdf = run(extract(tmp_path / "scan.pdf", "pdf"))
    assert pdf.method == "pdf-ocr" and "4471" in pdf.text and pdf.pages[0][0] == 1


def test_vision_model_used_and_metadata_never_stored(tmp_path, monkeypatch):
    _text_image("").save(tmp_path / "blank.png")
    monkeypatch.setattr(extraction, "ocr_available", lambda: False)
    monkeypatch.delenv("FRIDAY_VISION_MODEL", raising=False)
    with pytest.raises(ExtractionError, match="FRIDAY_VISION_MODEL"):
        run(extract(tmp_path / "blank.png", "png"))

    async def fake_describe(data, model):
        assert data[:4] == b"\x89PNG"
        return "A blank white banner."

    monkeypatch.setenv("FRIDAY_VISION_MODEL", "llava:7b")
    monkeypatch.setattr(extraction, "describe_image", fake_describe)
    result = run(extract(tmp_path / "blank.png", "png"))
    assert result.method == "vision" and "blank white banner" in result.text
    assert "900" not in result.text and "RGB" not in result.text  # no metadata posing as content


def test_upload_api_rejects_unsupported_and_reports_failures(api, monkeypatch):
    client, _ = api
    assert client.post("/api/documents/upload", files={"file": ("tool.exe", io.BytesIO(b"MZ"))}).status_code == 415
    assert client.post("/api/documents/upload", files={"file": ("e.txt", io.BytesIO(b""))}).status_code == 400
    monkeypatch.setattr(extraction, "ocr_available", lambda: False)
    monkeypatch.delenv("FRIDAY_VISION_MODEL", raising=False)
    buffer = io.BytesIO()
    _text_image("hello").save(buffer, "PNG")
    doc = client.post("/api/documents/upload", files={"file": ("pic.png", io.BytesIO(buffer.getvalue()))}).json()
    stored = client.get(f"/api/documents/{doc['id']}").json()
    assert stored["status"] == "failed" and "vision model" in stored["error"]
    assert stored["chunks"] == 0
