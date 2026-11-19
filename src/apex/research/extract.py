"""Turn files into plain text plus metadata. Local, deterministic, no network.

Supported: .txt/.md, .html/.htm, .pdf (text layer only; scanned PDFs need OCR, not yet
supported), .csv (dataset summary), .vtt/.srt (transcripts the user obtained through an
authorised route, e.g. their own channel's captions or a platform's official export).

Copyright: APEX only imports files the operator supplies. Record why use is lawful in the
document's ``licence_note``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

SUPPORTED = {".txt": "doc", ".md": "doc", ".html": "html", ".htm": "html", ".pdf": "pdf",
             ".csv": "csv", ".vtt": "transcript", ".srt": "transcript"}


class UnsupportedFormat(ValueError):
    pass


@dataclass
class ExtractedDoc:
    text: str
    source_type: str
    title: str
    authors: str = ""
    published: str | None = None
    metadata: dict = field(default_factory=dict)


def extract_file(path: str | Path) -> ExtractedDoc:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFormat(
            f"{path.name}: unsupported type {ext!r}; supported {sorted(SUPPORTED)}")
    if ext == ".pdf":
        return _pdf(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        return extract_html(raw, fallback_title=path.stem)
    if ext == ".csv":
        return _csv(path)
    if ext in (".vtt", ".srt"):
        return ExtractedDoc(transcript_to_text(raw), "transcript", path.stem)
    title = next((ln.lstrip("# ").strip() for ln in raw.splitlines() if ln.strip()), path.stem)
    return ExtractedDoc(raw, "doc", title[:300] or path.stem)


# ----------------------------------------------------------------------------- HTML
class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
             "article", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        a = {k: v or "" for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: ANN001
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def extract_html(html: str, fallback_title: str = "untitled") -> ExtractedDoc:
    p = _HtmlText()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    m = p.meta
    title = (m.get("og:title") or m.get("citation_title") or p.title or fallback_title).strip()
    authors = m.get("author") or m.get("citation_author") or ""
    published = (m.get("citation_publication_date") or m.get("article:published_time")
                 or m.get("date") or None)
    return ExtractedDoc(text, "html", title, authors, published, {"meta": m})


# ------------------------------------------------------------------------------ PDF
def _pdf(path: Path) -> ExtractedDoc:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(pg.extract_text() or "") for pg in reader.pages]
    info = reader.metadata or {}
    title = (getattr(info, "title", None) or "").strip() or path.stem
    authors = (getattr(info, "author", None) or "").strip()
    created = info.get("/CreationDate") if hasattr(info, "get") else None
    published = None
    if isinstance(created, str) and re.match(r"D:\d{8}", created):
        published = f"{created[2:6]}-{created[6:8]}-{created[8:10]}"
    text = "\n\n".join(pages).strip()
    return ExtractedDoc(text, "pdf", title, authors, published,
                        {"pages": len(pages), "has_text": bool(text)})


# ------------------------------------------------------------------------------ CSV
def _csv(path: Path) -> ExtractedDoc:
    df = pd.read_csv(path, nrows=200_000)
    lines = [f"Dataset {path.name}: {len(df)} rows, {len(df.columns)} columns.",
             "Columns: " + ", ".join(f"{c} ({t})" for c, t in df.dtypes.astype(str).items())]
    num = df.select_dtypes("number")
    if not num.empty:
        lines.append("Summary statistics:\n" + num.describe().T.round(6).to_string())
    lines.append("First rows:\n" + df.head(5).to_string(index=False))
    return ExtractedDoc("\n".join(lines), "csv", path.stem,
                        metadata={"rows": len(df), "columns": list(map(str, df.columns))})


# ----------------------------------------------------------------------- transcripts
_TS = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s*-->\s*.*$")


def transcript_to_text(raw: str) -> str:
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        skip = s.startswith(("WEBVTT", "NOTE", "Kind:", "Language:"))
        if not s or skip or s.isdigit() or _TS.match(s):
            continue
        s = re.sub(r"<[^>]+>", "", s).strip()  # inline VTT tags
        if s and (not out or out[-1] != s):  # auto-captions repeat lines
            out.append(s)
    return " ".join(out)
