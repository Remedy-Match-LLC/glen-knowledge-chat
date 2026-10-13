"""Packing photos on the console order card.

Glen, 2026-09-25: a drop-ship customer claimed a missing item after binning the box, so
he now photographs every packed box. Rulings: the photos go on the order card, where Rae
looks things up; a drop-ship card reminds whoever packs it, and the reminder never blocks
packing. Glen approved storage in the private file store on 2026-10-13.

The photo bytes live in the R2 bucket under packing-photos/<order>/<random>.jpg, never in
the database. Each upload is re-encoded here: turned upright, converted from HEIC when an
iPhone sends one, resized, and saved as a fresh JPEG. Re-encoding drops every EXIF field,
so no phone location ever reaches storage. Storage is passed in (put, delete) so tests
never touch R2.
"""
import datetime
import io
import secrets

from dashboard import db

PREFIX = "packing-photos/"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024     # one photo; an iPhone HEIC is 2-5 MB
MAX_PER_ORDER = 40
FULL_EDGE = 2400                        # the size order 203's copies were filed at
THUMB_EDGE = 360
_MAX_PIXELS = 80_000_000                # refuse a decompression bomb before decoding


class PhotoError(ValueError):
    """The upload is not a photo this module will store. The message is shown to staff."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_table(cx):
    cx.execute(
        "CREATE TABLE IF NOT EXISTS packing_photos ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, "
        "full_key TEXT NOT NULL, thumb_key TEXT NOT NULL, width INTEGER, height INTEGER, "
        "bytes INTEGER, uploaded_by TEXT, created_at TEXT NOT NULL)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_packing_photos_order "
               "ON packing_photos(order_id)")


def _open_image(raw):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 - without it a HEIC upload is refused below
        pass
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(raw))
        if im.width * im.height > _MAX_PIXELS:
            raise PhotoError("That image is too large to store.")
        im.load()
    except PhotoError:
        raise
    except Exception:
        raise PhotoError("That file is not a photo this page can read. Use a JPEG, PNG or HEIC.")
    return ImageOps.exif_transpose(im)


def _jpeg(im, edge):
    from PIL import Image
    im = im.copy()
    im.thumbnail((edge, edge), Image.LANCZOS)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85, optimize=True)   # no exif= argument: none is written
    return out.getvalue(), im.width, im.height


def prepare(raw):
    """(full_jpeg, thumb_jpeg, width, height) from an uploaded file's bytes."""
    if not raw:
        raise PhotoError("The upload was empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PhotoError("That photo is over 30 MB.")
    im = _open_image(raw)
    full, w, h = _jpeg(im, FULL_EDGE)
    thumb, _, _ = _jpeg(im, THUMB_EDGE)
    return full, thumb, w, h


def count_for(cx, order_id):
    init_table(cx)
    return int(cx.execute("SELECT COUNT(*) FROM packing_photos WHERE order_id=?",
                          (int(order_id),)).fetchone()[0])


def counts(cx):
    """{order_id: photo count} for every order that has photos."""
    init_table(cx)
    return {int(r[0]): int(r[1]) for r in cx.execute(
        "SELECT order_id, COUNT(*) FROM packing_photos GROUP BY order_id").fetchall()}


def add(cx, order_id, raw, *, put, uploaded_by=""):
    """Store one photo for an order. Returns its row as a dict. `put(key, bytes)` writes
    to storage; the row is written only after both files are stored."""
    order_id = int(order_id)
    if count_for(cx, order_id) >= MAX_PER_ORDER:
        raise PhotoError(f"This order already has {MAX_PER_ORDER} photos.")
    full, thumb, w, h = prepare(raw)
    stem = f"{PREFIX}{order_id}/{secrets.token_urlsafe(18)}"
    full_key, thumb_key = stem + ".jpg", stem + "_t.jpg"
    put(full_key, full)
    put(thumb_key, thumb)
    now = _now()
    from dashboard import dbwrite
    pid = dbwrite.insert_returning_id(
        cx,
        "INSERT INTO packing_photos (order_id, full_key, thumb_key, width, height, bytes, "
        "uploaded_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (order_id, full_key, thumb_key, w, h, len(full), (uploaded_by or "")[:40], now))
    cx.commit()
    return {"id": pid, "order_id": order_id, "width": w, "height": h,
            "uploaded_by": uploaded_by or "", "created_at": now}


def list_for(cx, order_id):
    init_table(cx)
    rows = cx.execute(
        "SELECT id, width, height, uploaded_by, created_at FROM packing_photos "
        "WHERE order_id=? ORDER BY id", (int(order_id),)).fetchall()
    return [{"id": int(r[0]), "width": r[1], "height": r[2], "uploaded_by": r[3] or "",
             "created_at": r[4]} for r in rows]


def key_for(cx, order_id, photo_id, size):
    """The storage key of one photo of this order, or None. A photo id from another
    order never resolves, so a guessed id cannot reach a different client's box."""
    init_table(cx)
    row = cx.execute("SELECT full_key, thumb_key FROM packing_photos WHERE id=? AND order_id=?",
                     (int(photo_id), int(order_id))).fetchone()
    if not row:
        return None
    return row[1] if size == "thumb" else row[0]


def delete(cx, order_id, photo_id, *, remove):
    """Delete one photo: the row first, then both files. True when a row was removed."""
    init_table(cx)
    row = cx.execute("SELECT full_key, thumb_key FROM packing_photos WHERE id=? AND order_id=?",
                     (int(photo_id), int(order_id))).fetchone()
    if not row:
        return False
    cx.execute("DELETE FROM packing_photos WHERE id=? AND order_id=?",
               (int(photo_id), int(order_id)))
    cx.commit()
    for k in row:
        try:
            remove(k)
        except Exception as e:  # noqa: BLE001 - an orphan file is harmless; the row is gone
            print(f"[packing-photos] could not remove {k}: {e!r}", flush=True)
    return True
