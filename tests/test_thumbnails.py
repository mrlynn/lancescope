"""Showing a picture the console had decided not to read.

Heavy columns stay out of a listing, a row browse and a query result — that claim
is the reason this repository exists. Seeing one is therefore not a relaxation of
it but the other half: asked for a row at a time, deliberately, and priced.

The columns here are ordinary `binary`, not Blob V2. That distinction is a cost —
a side file can be seeked into, an ordinary column cannot — and it is also the
common case, because a table somebody builds from their own images has a thumbnail
column and nothing declaring what is in it.
"""

from __future__ import annotations

from server import query


def _key(api, name: str, column: str = "item_id", **params) -> str:
    body = api.get(f"/catalog/tables/{name}/rows", params={"limit": 1, **params}).json()
    return body["rows"][0][column]


# ------------------------------------------------------------------------ sniffing

def test_the_bytes_say_what_they_are():
    assert query.sniff_media_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert query.sniff_media_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert query.sniff_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert query.sniff_media_type(b"\x00\x00\x00\x18ftypmp42") == "video/mp4"


def test_a_bitmap_is_recognised_without_its_two_byte_signature_matching_everything():
    """`BM` alone is two bytes and would claim far too much.

    An uncompressed bitmap is what an image column looks like when somebody stored
    pixels rather than a compressed file — the shape that makes a blob column weigh
    anything — so it is worth recognising, but only when the DIB header size that
    follows the signature is one a BMP actually uses.
    """
    import struct

    def header(dib: int) -> bytes:
        return struct.pack("<2sIHHII", b"BM", 70, 0, 0, 54, dib) + bytes(50)

    assert query.sniff_media_type(header(40)) == "image/bmp"     # BITMAPINFOHEADER
    assert query.sniff_media_type(header(124)) == "image/bmp"    # BITMAPV5HEADER
    assert query.sniff_media_type(header(41)) is None            # not a real one
    # Two matching bytes and nothing behind them is not a bitmap.
    assert query.sniff_media_type(b"BM" + bytes(4)) is None
    assert query.sniff_media_type(b"BMP file, said the text") is None


def test_the_portrait_column_the_roll_ships_is_one_a_browser_can_draw():
    """The Roll exists to be looked at, and it once shipped images nothing could show.

    Its portraits were uncompressed PPM, chosen so the column would weigh what a
    photograph weighs. That part was right and the format was not: no browser draws a
    PPM, so `/blob` served the whole column as `application/octet-stream` and the one
    dataset built by this repository to be *seen* could not be.
    """
    from ingest.build_roll import Arms, portrait_bytes

    painted = portrait_bytes(Arms("azure", "cross", "or"), 200_000)
    assert query.sniff_media_type(painted[:64]) == "image/bmp"


def test_unrecognisable_bytes_are_not_guessed_at():
    # Better octet-stream than a confident wrong type: a browser told `image/png`
    # about something else renders a broken image rather than offering a download.
    assert query.sniff_media_type(b"just some text here") is None
    assert query.sniff_media_type(b"") is None


def test_a_plain_binary_column_is_heavy_without_being_a_side_file(catalog):
    ds = catalog.open("thumbnails", scope="test").ds
    assert query.heavy_binary_columns(ds) == ["thumb"]
    # The Blob V2 fixture's column is a side file, so it is not in this list — the
    # two are read by different mechanisms and cost different things.
    blobs = catalog.open("blobs", scope="test").ds
    assert query.heavy_binary_columns(blobs) == []


# -------------------------------------------------------------------------- serving

def test_a_thumbnail_comes_back_as_an_image(api):
    r = api.get("/catalog/tables/thumbnails/blob",
                params={"column": "thumb", "key_column": "item_id", "key": "a"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content.startswith(b"\xff\xd8\xff")


def test_the_type_is_read_from_the_bytes_not_the_column_name(api):
    # The column is called `thumb`, and row b is a PNG. A name-based guess would
    # have to be wrong about one of these two rows.
    r = api.get("/catalog/tables/thumbnails/blob",
                params={"column": "thumb", "key_column": "item_id", "key": "b"})
    assert r.headers["content-type"] == "image/png"


def test_showing_one_reports_what_it_cost(api):
    r = api.get("/catalog/tables/thumbnails/blob",
                params={"column": "thumb", "key_column": "item_id", "key": "a"})
    # The whole point: the bytes moved, and the console says how many. A viewer that
    # showed the picture and reported nothing would be the one dishonest surface.
    assert int(r.headers["X-Read-Bytes"]) > 0


def test_an_empty_cell_is_a_404_not_an_empty_image(api):
    r = api.get("/catalog/tables/thumbnails/blob",
                params={"column": "thumb", "key_column": "item_id", "key": "c"})
    assert r.status_code == 404


def test_asking_for_a_column_that_holds_no_bytes_says_which_ones_do(api):
    r = api.get("/catalog/tables/thumbnails/blob",
                params={"column": "kind", "key_column": "item_id", "key": "a"})
    assert r.status_code == 404
    assert "thumb" in r.json()["detail"]


def test_a_table_with_no_bytes_at_all_says_so(api):
    r = api.get("/catalog/tables/ordinary/blob",
                params={"key_column": "id", "key": "1"})
    assert r.status_code == 404
    assert "no column of bytes" in r.json()["detail"]


def test_a_range_still_works_on_a_plain_column(api):
    r = api.get("/catalog/tables/thumbnails/blob",
                params={"column": "thumb", "key_column": "item_id", "key": "a"},
                headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.content == b"\xff\xd8\xff\xe0"


def test_the_side_file_path_still_works(api):
    # The Blob V2 column this route was written for, unchanged by the addition.
    r = api.get("/catalog/tables/blobs/blob",
                params={"key_column": "id", "key": "0"},
                headers={"Range": "bytes=0-15"})
    assert r.status_code == 206
    assert len(r.content) == 16


def test_a_thumbnail_never_appears_in_a_row_browse(api):
    # The claim this feature must not quietly undo. Seeing one is a request; it is
    # not something a listing does on your behalf.
    body = api.get("/catalog/tables/thumbnails/rows", params={"limit": 3}).json()
    assert "thumb" not in body["rows"][0]
    omitted = {c["name"]: c for c in body["omitted_columns"]}
    assert "thumb" in omitted
    # And the row browser says how to read it, which is the door this feature walks
    # through rather than a second one cut beside it.
    assert "expand" in omitted["thumb"]["reason"]
