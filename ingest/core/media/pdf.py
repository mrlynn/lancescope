"""PDF: one row per page, and the cheapest second medium there is.

A page is structurally identical to a keyframe with a transcript window already
attached — a picture plus the words that go with it — which is the row shape the
demo's `moments` table has always had. So this reuses the image path almost
entirely: render the page, thumbnail it, embed it, and put its text layer in the
column full-text search indexes.

**pypdfium2 to render, pypdf to read.** Not PyMuPDF and not poppler, for three
reasons in order. Neither needs an external binary, so PDF is the one non-image
medium that could work in the packaged app at all. PyMuPDF is AGPL, and this ships
as a signed desktop application. And a poppler dependency would put `pdftoppm` on
the list of things a user has to install before the second-most-useful medium works.

**A scanned page still earns its row.** With no text layer there is nothing to index,
but the rendered page is exactly as embeddable as a photograph — "the page with the
org chart" works on a scan, which is most of why rendering is worth the cost.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingest.core.media.base import Extraction, Item
from ingest.core.media.thumbs import thumbnail

EXTENSIONS = frozenset({".pdf"})

# Long edge, in pixels, of the render handed to the embedder. Enough for a slide's
# title and a figure's shape; far below what would be needed to read body text,
# which is what the text layer is for.
RENDER_LONG_EDGE = 1024

# A page count past which a single file dominates a run. Not a cap — a book is
# legitimately six hundred pages — but worth saying out loud, because one file
# quietly becoming most of the table is the kind of surprise that shows up as
# "why is this taking so long".
LOUD_PAGE_COUNT = 200


def _clean(text: str) -> str:
    """Collapse the whitespace a PDF text layer arrives wrapped in.

    Extraction preserves the visual line breaks of a two-column layout, which turns
    one sentence into eight lines and makes phrase search miss. Words are what the
    inverted index wants.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def prettify(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()


class PdfHandler:
    kind = "pdf"
    extensions = EXTENSIONS

    def extract(self, src: Path, work: Path) -> Extraction:
        import pypdfium2 as pdfium
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        warnings: list[str] = []

        # Text and metadata first: a document that cannot be read at all should fail
        # before anything is rendered to disk.
        title = ""
        page_text: list[str] = []
        try:
            reader = PdfReader(str(src))
            if reader.is_encrypted:
                # An empty password opens the common case — a document encrypted to
                # forbid printing rather than to keep anyone out.
                try:
                    reader.decrypt("")
                except (NotImplementedError, PdfReadError):
                    pass
            title = _clean(str((reader.metadata or {}).get("/Title") or ""))
            for page in reader.pages:
                try:
                    page_text.append(_clean(page.extract_text() or ""))
                except Exception:                                  # noqa: BLE001
                    # One unreadable page in a long document is not a reason to lose
                    # the other four hundred.
                    page_text.append("")
        except PdfReadError as e:
            raise ValueError(f"not a readable PDF: {e}") from e
        except Exception as e:                                     # noqa: BLE001
            warnings.append(f"{src.name}: no text could be read ({type(e).__name__})")

        try:
            doc = pdfium.PdfDocument(str(src))
        except Exception as e:                                     # noqa: BLE001
            raise ValueError(f"could not be opened for rendering: {e}") from e

        pages = len(doc)
        if pages == 0:
            # Belt and braces: pdfium refuses to open a zero-page document at all,
            # so this is reached only if that ever changes. It is one line, and the
            # alternative is a table with a row that renders nothing.
            raise ValueError("this PDF has no pages")
        if pages > LOUD_PAGE_COUNT:
            warnings.append(
                f"{src.name} has {pages:,} pages, so it will account for "
                f"{pages:,} rows on its own.")

        out_dir = work / f"pdf-{abs(hash(str(src))):x}"
        out_dir.mkdir(parents=True, exist_ok=True)

        items: list[Item] = []
        empty_pages = 0
        try:
            for i in range(pages):
                page = doc[i]
                w_pt, h_pt = page.get_size()
                scale = RENDER_LONG_EDGE / max(w_pt, h_pt, 1)
                image = page.render(scale=scale).to_pil()

                render_path = out_dir / f"{i:05d}.jpg"
                image.convert("RGB").save(render_path, "JPEG", quality=88)

                text = page_text[i] if i < len(page_text) else ""
                if text:
                    source = "pdf-text"
                else:
                    empty_pages += 1
                    # Not "none": a row nothing can find by text is worse than a row
                    # findable by the document it came from. The label says the text
                    # is weak, which is what a disappointing result needs beside it.
                    text = f"{title or prettify(src.stem)} page {i + 1}"
                    source = "filename"

                items.append(Item(
                    ordinal=i,
                    page=i + 1,
                    text=text,
                    text_source=source,
                    image_path=render_path,
                    thumb_jpeg=thumbnail(image),
                    title=title or prettify(src.stem),
                    width=image.width,
                    height=image.height,
                    meta={"pages": pages, "page": i + 1},
                ))
                page.close()
        finally:
            doc.close()

        if empty_pages == pages:
            warnings.append(
                f"{src.name} has no text layer on any page — it is probably a scan. "
                f"The pages are still embedded, so searching by what they look like "
                f"works; searching their words does not.")

        return Extraction(items=items, chunks=(), warnings=tuple(warnings))
