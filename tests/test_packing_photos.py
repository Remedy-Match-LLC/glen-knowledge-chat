"""Packing photos on the console order card (Glen, 2026-09-25; storage approved 2026-10-13)."""
import io
import sqlite3

import pytest
from PIL import Image

from dashboard import packing_photos as P


def _jpeg(w=3000, h=2000, exif=None, color=(200, 30, 30)):
    im = Image.new("RGB", (w, h), color)
    out = io.BytesIO()
    if exif is not None:
        im.save(out, "JPEG", exif=exif)
    else:
        im.save(out, "JPEG")
    return out.getvalue()


def _gps_exif(orientation=None):
    ex = Image.Exif()
    ex[0x8825] = {1: "N", 2: (19.0, 43.0, 0.0), 3: "W", 4: (155.0, 5.0, 0.0)}   # GPS
    ex[0x010F] = "Apple"
    if orientation:
        ex[0x0112] = orientation
    return ex


def _cx():
    cx = sqlite3.connect(":memory:")
    P.init_table(cx)
    return cx


class Store:
    def __init__(self):
        self.files = {}

    def put(self, k, b):
        self.files[k] = b

    def remove(self, k):
        self.files.pop(k, None)


def test_a_photo_is_resized_and_stored_with_a_thumbnail():
    cx, st = _cx(), Store()
    row = P.add(cx, 203, _jpeg(), put=st.put, uploaded_by="owner")
    assert (row["width"], row["height"]) == (2400, 1600)
    keys = sorted(st.files)
    assert len(keys) == 2 and all(k.startswith("packing-photos/203/") for k in keys)
    thumb = Image.open(io.BytesIO(st.files[[k for k in keys if k.endswith("_t.jpg")][0]]))
    assert max(thumb.size) == P.THUMB_EDGE


def test_no_location_or_camera_data_survives():
    cx, st = _cx(), Store()
    P.add(cx, 1, _jpeg(exif=_gps_exif()), put=st.put)
    for data in st.files.values():
        ex = Image.open(io.BytesIO(data)).getexif()
        assert 0x8825 not in ex and 0x010F not in ex, "EXIF must be dropped"
        assert b"Apple" not in data


def test_a_sideways_phone_photo_is_stored_upright():
    cx, st = _cx(), Store()
    row = P.add(cx, 1, _jpeg(3000, 2000, exif=_gps_exif(orientation=6)), put=st.put)
    assert (row["width"], row["height"]) == (1600, 2400)


@pytest.mark.parametrize("raw,msg", [
    (b"", "empty"),
    (b"%PDF-1.4 not a photo", "not a photo"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "not a photo"),
])
def test_what_is_not_a_photo_is_refused(raw, msg):
    with pytest.raises(P.PhotoError, match=msg):
        P.prepare(raw)


def test_an_oversized_upload_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(P, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(P.PhotoError, match="30 MB"):
        P.prepare(_jpeg(50, 50) + b"x" * 200)


def test_a_decompression_bomb_is_refused(monkeypatch):
    monkeypatch.setattr(P, "_MAX_PIXELS", 1000)
    with pytest.raises(P.PhotoError):
        P.prepare(_jpeg(100, 100))


def test_an_order_holds_at_most_the_limit(monkeypatch):
    monkeypatch.setattr(P, "MAX_PER_ORDER", 2)
    cx, st = _cx(), Store()
    P.add(cx, 5, _jpeg(10, 10), put=st.put)
    P.add(cx, 5, _jpeg(10, 10), put=st.put)
    with pytest.raises(P.PhotoError, match="already has 2"):
        P.add(cx, 5, _jpeg(10, 10), put=st.put)


def test_a_photo_id_from_another_order_never_resolves():
    cx, st = _cx(), Store()
    row = P.add(cx, 7, _jpeg(10, 10), put=st.put)
    assert P.key_for(cx, 7, row["id"], "full")
    assert P.key_for(cx, 8, row["id"], "full") is None
    assert P.delete(cx, 8, row["id"], remove=st.remove) is False
    assert len(st.files) == 2


def test_delete_removes_the_row_and_both_files():
    cx, st = _cx(), Store()
    row = P.add(cx, 7, _jpeg(10, 10), put=st.put)
    assert P.delete(cx, 7, row["id"], remove=st.remove) is True
    assert st.files == {} and P.photos_for(cx, 7) == [] and P.counts(cx) == {}


def test_a_failed_store_writes_no_row():
    cx = _cx()

    def boom(k, b):
        raise RuntimeError("r2 down")
    with pytest.raises(RuntimeError):
        P.add(cx, 9, _jpeg(10, 10), put=boom)
    assert P.photos_for(cx, 9) == []


def test_an_iphone_heic_photo_is_stored_as_jpeg():
    pillow_heif = pytest.importorskip("pillow_heif")
    im = Image.new("RGB", (800, 600), (10, 120, 40))
    heif = pillow_heif.from_pillow(im)
    out = io.BytesIO()
    heif.save(out, quality=80)
    full, thumb, w, h = P.prepare(out.getvalue())
    assert full[:3] == b"\xff\xd8\xff" and (w, h) == (800, 600)


def test_only_photo_formats_are_opened():
    # EPS would run Ghostscript; TIFF and BMP have no purpose here (security review).
    for fmt in ("EPS", "TIFF", "BMP"):
        out = io.BytesIO()
        try:
            Image.new("RGB", (20, 20)).save(out, fmt)
        except Exception:  # noqa: BLE001 - a writer this Pillow lacks proves nothing
            continue
        with pytest.raises(P.PhotoError, match="not a photo"):
            P.prepare(out.getvalue())


def test_a_jpeg_comment_does_not_survive():
    im = Image.new("RGB", (40, 40))
    out = io.BytesIO()
    im.save(out, "JPEG", comment=b"SECRETCOMMENT")
    assert b"SECRETCOMMENT" in out.getvalue()
    full, thumb, _, _ = P.prepare(out.getvalue())
    assert b"SECRETCOMMENT" not in full and b"SECRETCOMMENT" not in thumb


def test_a_large_jpeg_is_decoded_at_reduced_scale(monkeypatch):
    raw = _jpeg(6000, 4000)            # made before the spy, so only prepare() is seen
    seen = {}
    real = Image.Image.load

    def spy(self):
        seen.setdefault("size", self.size)
        return real(self)
    monkeypatch.setattr(Image.Image, "load", spy)
    P.prepare(raw)
    assert max(seen["size"]) <= 3000, seen


def test_a_failed_row_write_removes_the_stored_files():
    cx, st = _cx(), Store()
    cx.execute("DROP TABLE packing_photos")
    cx.execute("CREATE TABLE packing_photos (id INTEGER PRIMARY KEY, order_id INTEGER, "
               "CHECK (0))")                      # every insert fails

    def count_ok(c, o):
        return 0
    orig = P.count_for
    P.count_for = count_ok
    try:
        with pytest.raises(Exception):
            P.add(cx, 9, _jpeg(10, 10), put=st.put, remove=st.remove)
    finally:
        P.count_for = orig
    assert st.files == {}


def test_a_failed_second_put_removes_the_first():
    cx, st = _cx(), Store()
    calls = []

    def put(k, b):
        calls.append(k)
        if len(calls) == 2:
            raise RuntimeError("r2 down")
        st.put(k, b)
    with pytest.raises(RuntimeError):
        P.add(cx, 9, _jpeg(10, 10), put=put, remove=st.remove)
    assert st.files == {} and P.photos_for(cx, 9) == []


def test_setup_leaves_no_transaction_open():
    # Postgres holds a table lock from CREATE INDEX until commit; two uploads deadlocked.
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE t (x)")   # make the connection's next DDL join a transaction
    cx.execute("INSERT INTO t VALUES (1)")
    P.count_for(cx, 1)
    assert not cx.in_transaction


def test_a_failed_row_write_rolls_back_so_the_connection_still_works():
    cx, st = _cx(), Store()

    class Boom(Exception):
        pass

    def put(k, b):
        st.put(k, b)
    import dashboard.dbwrite as dbw
    real = dbw.insert_returning_id
    dbw.insert_returning_id = lambda *a, **k: (_ for _ in ()).throw(Boom())
    try:
        with pytest.raises(Boom):
            P.add(cx, 4, _jpeg(10, 10), put=put, remove=st.remove)
    finally:
        dbw.insert_returning_id = real
    assert P.photos_for(cx, 4) == [] and st.files == {}


def test_a_large_png_is_shrunk_before_any_copy(monkeypatch):
    out = io.BytesIO()
    Image.new("RGB", (4000, 3000), (9, 9, 9)).save(out, "PNG")
    raw = out.getvalue()
    sizes = []
    real = Image.Image.copy

    def spy(self):
        sizes.append(self.size)
        return real(self)
    monkeypatch.setattr(Image.Image, "copy", spy)
    P.prepare(raw)
    assert sizes and max(max(s) for s in sizes) <= P.FULL_EDGE, sizes
