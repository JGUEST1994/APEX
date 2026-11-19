import numpy as np
import pytest
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.models import ChunkVector, ResearchDocument
from apex.db.session import make_engine, make_session_factory
from apex.research.concepts import extract_concepts, find_formulas, find_rules
from apex.research.embeddings import HashingEmbedder
from apex.research.extract import UnsupportedFormat, extract_file, extract_html, transcript_to_text
from apex.research.library import (
    chunk_text,
    find_by_concept,
    formulas_for,
    import_file,
    keyword_search,
    search,
)


def _pdf_bytes(text: str, title: str = "Momentum Paper", author: str = "A. Researcher") -> bytes:
    """Build a minimal one-page PDF with a real text layer (no external libraries)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Title ({title}) /Author ({author}) /CreationDate (D:20240115000000Z) >>".encode(),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


VWAP_TEXT = """VWAP mean reversion intraday.
VWAP = sum(price * volume) / sum(volume)
If price closes 2 standard deviations below VWAP, buy and exit at VWAP.
Use a stop-loss at 3 deviations and fixed fractional position sizing with 0.5% risk per trade.
"""


def test_extract_text_and_markdown(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# VWAP Notes\n\nbody")
    ex = extract_file(f)
    assert ex.title == "VWAP Notes" and ex.source_type == "doc"


def test_extract_html_strips_chrome():
    html = """<html><head><title>T</title><meta name="author" content="Jane Doe">
    <meta property="og:title" content="Breakouts that work">
    <meta name="citation_publication_date" content="2021-05-01"></head>
    <body><nav>menu</nav><script>var x=1;</script><h1>Opening range</h1>
    <p>Breakout above the opening range high.</p><footer>cookie</footer></body></html>"""
    ex = extract_html(html)
    assert ex.title == "Breakouts that work" and ex.authors == "Jane Doe"
    assert ex.published == "2021-05-01"
    assert "Breakout above" in ex.text and "menu" not in ex.text and "var x" not in ex.text


def test_extract_pdf(tmp_path):
    f = tmp_path / "paper.pdf"
    f.write_bytes(_pdf_bytes("Time series momentum earns returns across futures"))
    ex = extract_file(f)
    assert "momentum" in ex.text.lower()
    assert ex.title == "Momentum Paper" and ex.authors == "A. Researcher"
    assert ex.published == "2024-01-15" and ex.metadata["pages"] == 1


def test_extract_csv(tmp_path):
    f = tmp_path / "returns.csv"
    f.write_text("date,ret\n2024-01-01,0.01\n2024-01-02,-0.02\n")
    ex = extract_file(f)
    assert "2 rows" in ex.text and "ret" in ex.text and ex.metadata["columns"] == ["date", "ret"]


def test_transcripts():
    vtt = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000
<c>today we trade</c> the VWAP

00:00:03.000 --> 00:00:05.000
today we trade the VWAP

00:00:05.000 --> 00:00:07.000
bounce at the open"""
    assert transcript_to_text(vtt) == "today we trade the VWAP bounce at the open"
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nworld\n"
    assert transcript_to_text(srt) == "Hello world"


def test_unsupported(tmp_path):
    f = tmp_path / "x.docx"
    f.write_text("x")
    with pytest.raises(UnsupportedFormat):
        extract_file(f)


def test_concepts_formulas_rules():
    out = extract_concepts(VWAP_TEXT)
    assert {"vwap", "mean_reversion"} <= set(out["concepts"])
    assert {"stop_loss", "position_sizing"} <= set(out["risk_techniques"])
    assert any(f.startswith("VWAP = sum(") for f in out["formulas"])
    assert any("buy" in r for r in out["rules"])
    assert out["status"] == "hypotheses_untested"
    assert find_formulas("x = y") == []  # no maths operator -> not a formula
    assert find_rules("Prices go up.") == []


def test_hashing_embedder():
    e = HashingEmbedder()
    v = e.embed(["vwap mean reversion", "vwap mean reversion", "futures roll yield"])
    assert v.shape == (3, 384)
    assert np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-5)
    assert v[0] @ v[1] > 0.99 and v[0] @ v[2] < 0.5


def test_chunking():
    assert [len(c) for c in chunk_text("x" * 2500, size=1000, overlap=100)] == [1000, 1000, 700]


def _corpus(session, tmp_path):
    tmp_path = tmp_path / "corpus"
    tmp_path.mkdir()
    (tmp_path / "vwap.md").write_text("# VWAP Notes\n\n" + VWAP_TEXT * 3)
    (tmp_path / "orb.html").write_text(
        "<title>ORB</title><p>Opening range breakout: when price breaks the first 30 minute high, "
        "enter long. Momentum confirms with volume.</p>")
    (tmp_path / "mom.pdf").write_bytes(_pdf_bytes("Momentum = log(P_t / P_t-12) ranks assets"))
    for f in sorted(tmp_path.iterdir()):
        import_file(session, f, licence_note="test fixture written for APEX")
    session.commit()


def test_import_and_search(session, tmp_path):
    _corpus(session, tmp_path)
    docs = session.query(ResearchDocument).all()
    assert len(docs) == 3
    assert session.query(ChunkVector).count() == sum(len(d.chunks) for d in docs)

    hits = search(session, "Find all research covering VWAP mean-reversion strategies.")
    assert hits[0].title == "VWAP Notes"  # md title from first line
    assert keyword_search(session, "opening range breakout")[0].title == "ORB"
    assert keyword_search(session, "the and") == []

    assert {d.title for d in find_by_concept(session, "momentum")} == {"ORB", "Momentum Paper"}
    formulas = formulas_for(session, "vwap")
    assert formulas and formulas[0][0] == "VWAP Notes"


def test_import_requires_licence_note(session, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("vwap")
    with pytest.raises(ValueError, match="licence_note"):
        import_file(session, f, licence_note=" ")


def test_reimport_is_deduplicated(session, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text(VWAP_TEXT)
    a = import_file(session, f, licence_note="own notes")
    b = import_file(session, f, licence_note="own notes")
    assert a.id == b.id


def test_research_api(db_url, session, tmp_path):
    _corpus(session, tmp_path)
    engine = make_engine(db_url)
    client = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                                   make_session_factory(engine)))
    docs = client.get("/research/documents").json()
    assert len(docs) == 3
    one = client.get(f"/research/documents/{docs[0]['id']}").json()
    assert "concepts" in one["extracted"]
    assert client.get("/research/documents/9999").status_code == 404
    assert client.get("/research/search", params={"q": "vwap"}).json()[0]["title"] == "VWAP Notes"
    engine.dispose()
