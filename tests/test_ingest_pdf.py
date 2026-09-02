"""PDF pages as rows.

The claim worth testing is not that a PDF can be opened — it is that a page behaves
like a keyframe with its transcript already attached, and therefore lands in the
same table, in the same shape, as a photograph. These tests use real decoders
against real PDFs, because a faked PDF handler would prove nothing about the one
thing that is hard here: getting text and pixels off the same page.
"""

from __future__ import annotations

import lance
import pytest

from ingest.core.media import IMPLEMENTED, handler_for
from ingest.core.media.pdf import LOUD_PAGE_COUNT, PdfHandler
from ingest.core.run import RunRequest, run
from tests.conftest import EMPTY_PDF, MINIMAL_PDF, PNG_1X1

pytest.importorskip("pypdfium2", reason="the PDF decoders are in the test group")
pytest.importorskip("pypdf", reason="the PDF decoders are in the test group")


@pytest.fixture
def one_page(tmp_path):
    p = tmp_path / "quarterly.pdf"
    p.write_bytes(MINIMAL_PDF)
    return p


def extract(path, work):
    return handler_for("pdf").extract(path, work)


# ------------------------------------------------------------------- one page

def test_a_pdf_becomes_one_row_per_page(one_page, work_dir):
    ex = extract(one_page, work_dir)
    assert len(ex.items) == 1
    assert ex.items[0].page == 1
    assert ex.items[0].ordinal == 0


def test_a_page_carries_both_its_words_and_its_picture(one_page, work_dir):
    """The whole reason PDF is the cheap second medium: one page yields the two
    things the schema already has columns for."""
    item = extract(one_page, work_dir).items[0]
    assert item.text_source == "pdf-text"
    assert "Kubernetes" in item.text
    assert item.image_path is not None and item.image_path.exists()
    assert item.thumb_jpeg.startswith(b"\xff\xd8"), "a real JPEG thumbnail"
    assert item.width and item.height


def test_a_page_is_rendered_large_enough_to_embed_and_thumbnailed_small(
        one_page, work_dir):
    from ingest.core.media.pdf import RENDER_LONG_EDGE

    item = extract(one_page, work_dir).items[0]
    assert max(item.width, item.height) == RENDER_LONG_EDGE
    # Tens of kilobytes: inlined in the row, per `ingest/build_lance.py`.
    assert len(item.thumb_jpeg) < 100_000


def test_the_text_layer_is_unwrapped_rather_than_stored_line_by_line(tmp_path, work_dir):
    """A two-column layout extracts with the visual line breaks intact, which turns
    one sentence into eight lines and makes phrase search miss."""
    from ingest.core.media.pdf import _clean

    assert _clean("Revenue grew\nacross   the\n\nnorthern region") == (
        "Revenue grew across the northern region")


# --------------------------------------------------------------- awkward files

@pytest.mark.parametrize("name,content", [
    ("truncated.pdf", EMPTY_PDF),        # a download that stopped early
    ("actually-a-png.pdf", PNG_1X1),     # a file named for what it is not
])
def test_a_pdf_that_cannot_be_read_is_refused_as_one_file_not_as_a_crash(
        tmp_path, work_dir, name, content):
    """`run` catches ValueError per file and keeps going, so what matters is that
    the refusal is a ValueError naming this file rather than whatever the decoder
    happened to raise."""
    p = tmp_path / name
    p.write_bytes(content)
    with pytest.raises(ValueError) as excinfo:
        extract(p, work_dir)
    assert str(excinfo.value), "a refusal with no sentence in it is not a refusal"


def test_a_scan_with_no_text_layer_is_still_worth_a_row_and_says_so(
        tmp_path, work_dir):
    """The reason rendering is worth its cost: "the page with the org chart" works
    on a scan, and the label explains why its words search badly."""
    from PIL import Image

    p = tmp_path / "scanned-notes.pdf"
    Image.new("RGB", (612, 792), (200, 60, 60)).save(p)

    ex = extract(p, work_dir)
    assert len(ex.items) == 1
    item = ex.items[0]
    assert item.text_source == "filename"
    assert "scanned-notes" in item.text, "a row nothing can find by text is worse"
    assert item.image_path.exists(), "it is still embedded"
    assert any("probably a scan" in w for w in ex.warnings), ex.warnings


def test_a_very_long_document_says_it_will_dominate_the_table(monkeypatch, tmp_path,
                                                              work_dir):
    from PIL import Image

    monkeypatch.setattr("ingest.core.media.pdf.LOUD_PAGE_COUNT", 1)
    p = tmp_path / "book.pdf"
    pages = [Image.new("RGB", (612, 792), (250, 250, 250)) for _ in range(3)]
    pages[0].save(p, save_all=True, append_images=pages[1:])

    ex = extract(p, work_dir)
    assert any("rows on its own" in w for w in ex.warnings), ex.warnings
    assert LOUD_PAGE_COUNT > 1, "the real threshold should not be one"


# ------------------------------------------------------------- into a table

def test_pdfs_and_images_land_in_one_table_told_apart_by_kind(
        tmp_path, dest_root, work_dir, fake_embedder):
    """The reason there is one table and not four: a folder with a photo and a
    report in it is one question, not two."""
    src = tmp_path / "mixed"
    src.mkdir()
    (src / "photo.png").write_bytes(PNG_1X1)
    (src / "quarterly.pdf").write_bytes(MINIMAL_PDF)

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="library",
                       kinds=("image", "pdf")), fake_embedder, work_dir=work_dir)

    rows = lance.dataset(r.uri).to_table(columns=["kind", "page", "text_source"]).to_pylist()
    assert sorted(x["kind"] for x in rows) == ["image", "pdf"]
    pdf_row = next(x for x in rows if x["kind"] == "pdf")
    assert pdf_row["page"] == 1 and pdf_row["text_source"] == "pdf-text"
    # `page` is null for an image — a nullable column, not a second table.
    assert next(x for x in rows if x["kind"] == "image")["page"] is None


def test_the_kind_column_is_a_filter_rather_than_a_second_query(
        tmp_path, dest_root, work_dir, fake_embedder):
    src = tmp_path / "mixed"
    src.mkdir()
    for i in range(3):
        (src / f"photo-{i}.png").write_bytes(PNG_1X1)
    (src / "quarterly.pdf").write_bytes(MINIMAL_PDF)

    r = run(RunRequest(source=str(src), destination=str(dest_root), name="library",
                       kinds=("image", "pdf")), fake_embedder, work_dir=work_dir)
    ds = lance.dataset(r.uri)
    assert ds.to_table(filter="kind = 'pdf'").num_rows == 1
    assert ds.to_table(filter="kind = 'image'").num_rows == 3


def test_a_run_cleans_up_the_pages_it_rendered(
        tmp_path, dest_root, work_dir, fake_embedder):
    """A PDF renders a JPEG per page. Left behind, the cache would grow by every
    page of every document ever ingested."""
    src = tmp_path / "docs"
    src.mkdir()
    (src / "quarterly.pdf").write_bytes(MINIMAL_PDF)

    run(RunRequest(source=str(src), destination=str(dest_root), name="docs",
                   kinds=("pdf",)), fake_embedder, work_dir=work_dir)
    assert list(work_dir.iterdir()) == [], f"left behind: {list(work_dir.iterdir())}"


def test_pdf_is_declared_as_something_this_build_can_actually_ingest():
    assert "pdf" in IMPLEMENTED
    assert PdfHandler().extensions == frozenset({".pdf"})
