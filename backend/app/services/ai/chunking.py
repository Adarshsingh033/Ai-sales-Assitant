"""
Document text extraction and structure-aware chunking.

Extraction yields `TextBlock`s that keep page numbers and headings. The chunker packs paragraphs of the
same section into ~500-character chunks (sized for multilingual MiniLM's 128-token window), splits long
paragraphs on sentence boundaries, and carries a short overlap so facts spanning a boundary survive.
"""
import csv
import io
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TextBlock:
    text: str
    page: Optional[int] = None
    heading: Optional[str] = None
    is_heading: bool = False


@dataclass
class Chunk:
    content: str
    section: Optional[str] = None
    page: Optional[int] = None


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


_WS = re.compile(r"[ \t\u00a0]+")


def _clean(text: str) -> str:
    """Collapse the runs of spaces PDF extractors emit (they waste prompt tokens and dilute embeddings)."""
    return _WS.sub(" ", text).strip()


def _looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 80 or line.endswith((".", ",", ";", "?", "!")):
        return False
    words = line.split()
    if len(words) > 10:
        return False
    return line.isupper() or (line[0].isupper() and len(words) <= 6 and not line.endswith(":") and line.istitle())


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def extract_blocks(file_bytes: bytes, file_type: str, filename: str = "") -> List[TextBlock]:
    ext = file_type.lower().strip(".").strip()
    if ext == "pdf":
        return _extract_pdf(file_bytes)
    if ext in ("docx", "doc"):
        return _extract_docx(file_bytes)
    if ext == "csv":
        return _extract_csv(file_bytes, filename)
    if ext in ("txt", "md"):
        return _extract_text(file_bytes.decode("utf-8", errors="replace"))
    raise ValueError(f"Unsupported file type: {file_type}. Supported: pdf, docx, txt, md, csv")


def blocks_to_text(blocks: List[TextBlock]) -> str:
    """Plain text with markdown-style heading markers, so a stored document can be re-chunked with structure intact."""
    return "\n\n".join((f"# {b.text}" if b.is_heading else b.text) for b in blocks if b.text).strip()


def _extract_pdf(file_bytes: bytes) -> List[TextBlock]:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    blocks: List[TextBlock] = []
    for page_no, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        for para in _paragraphs(text) or [text]:
            lines = [_clean(ln) for ln in para.splitlines() if ln.strip()]
            if len(lines) > 1 and _looks_like_heading(lines[0]):
                blocks.append(TextBlock(lines[0], page=page_no, is_heading=True))
                para = " ".join(lines[1:])
            else:
                para = " ".join(lines)
            if para:
                blocks.append(TextBlock(para, page=page_no))
    return blocks


def _extract_docx(file_bytes: bytes) -> List[TextBlock]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(io.BytesIO(file_bytes))
    blocks: List[TextBlock] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = _clean(item.text)
            if not text:
                continue
            style = (item.style.name if item.style is not None else "") or ""
            blocks.append(TextBlock(text, is_heading=style.lower().startswith(("heading", "title"))))
        elif isinstance(item, Table):
            rows = [[c.text.strip() for c in r.cells] for r in item.rows]
            if not rows:
                continue
            header, body = rows[0], rows[1:]
            if body and any(header):
                for row in body:
                    pairs = [f"{h}: {v}" for h, v in zip(header, row) if v]
                    if pairs:
                        blocks.append(TextBlock("; ".join(pairs)))
            else:
                blocks.append(TextBlock(" | ".join(c for c in header if c)))
    return blocks


def _extract_csv(file_bytes: bytes, filename: str) -> List[TextBlock]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    blocks: List[TextBlock] = []
    if not body:
        return [TextBlock(" | ".join(header))]
    for row in body:
        pairs = [f"{h}: {v}" for h, v in zip(header, row) if v.strip()]
        if pairs:
            blocks.append(TextBlock("; ".join(pairs), heading=filename or None))
    return blocks


def _extract_text(text: str) -> List[TextBlock]:
    blocks: List[TextBlock] = []
    for para in _paragraphs(text):
        first, _, rest = para.partition("\n")
        md = _MD_HEADING.match(first)
        if md:
            blocks.append(TextBlock(md.group(1).strip(), is_heading=True))
            if rest.strip():
                blocks.append(TextBlock(" ".join(rest.split())))
        else:
            blocks.append(TextBlock(" ".join(para.split())))
    return blocks


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def _split_long(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: List[str] = []
    buf = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        if len(sentence) > max_chars:  # no sentence punctuation: hard split on words
            words, cur = sentence.split(), ""
            for w in words:
                if cur and len(cur) + 1 + len(w) > max_chars:
                    pieces.append(cur)
                    cur = w
                else:
                    cur = f"{cur} {w}".strip()
            sentence = cur
        if buf and len(buf) + 1 + len(sentence) > max_chars:
            pieces.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def _tail_overlap(text: str, overlap_chars: int) -> str:
    """Last whole sentence(s) of `text` that fit into `overlap_chars`."""
    if overlap_chars <= 0:
        return ""
    tail = ""
    for sentence in reversed(_SENTENCE_SPLIT.split(text)):
        if len(tail) + len(sentence) + 1 > overlap_chars:
            break
        tail = f"{sentence} {tail}".strip()
    return tail


def chunk_blocks(
    blocks: List[TextBlock],
    target_chars: int = 500,
    overlap_chars: int = 80,
    max_chars: Optional[int] = None,
) -> List[Chunk]:
    max_chars = max_chars or int(target_chars * 1.4)
    chunks: List[Chunk] = []
    section: Optional[str] = None
    buf = ""
    buf_page: Optional[int] = None
    buf_section: Optional[str] = None

    def flush(carry_overlap: bool) -> None:
        nonlocal buf, buf_page, buf_section
        content = buf.strip()
        if content:
            chunks.append(Chunk(content=content, section=buf_section, page=buf_page))
        buf = _tail_overlap(content, overlap_chars) if carry_overlap and content else ""
        buf_page = None if not buf else buf_page

    for block in blocks:
        if block.is_heading:
            flush(carry_overlap=False)
            section = block.text.strip()
            continue
        block_section = block.heading or section
        if block_section != buf_section and buf:
            flush(carry_overlap=False)
        buf_section = block_section
        for piece in _split_long(block.text, max_chars):
            if buf and len(buf) + 1 + len(piece) > target_chars:
                flush(carry_overlap=True)
                buf_section = block_section
            buf = f"{buf} {piece}".strip()
            buf_page = buf_page or block.page
    flush(carry_overlap=False)
    return chunks


def chunk_plain_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[str]:
    """Legacy helper: chunk raw text, return just the strings."""
    blocks = _extract_text(text.strip()) if text.strip() else []
    return [c.content for c in chunk_blocks(blocks, target_chars=chunk_size, overlap_chars=overlap)]
