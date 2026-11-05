"""Resume text extraction and Word export."""
import io

from docx import Document
from pypdf import PdfReader

MAX_UPLOAD_BYTES = 5_000_000
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def extract(name, raw):
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload is limited to 5 MB.")
    name = name.lower()
    if name.endswith(".pdf"):
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
    if name.endswith(".docx"):
        doc = Document(io.BytesIO(raw))
        paragraphs = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                paragraphs.append(" | ".join(c.text for c in row.cells))
        return "\n".join(paragraphs)
    if name.endswith(".txt"):
        return raw.decode("utf-8")
    raise ValueError("Unsupported file type")


def docx_bytes(text):
    doc = Document()
    for line in text.splitlines():
        if line.strip():
            doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
