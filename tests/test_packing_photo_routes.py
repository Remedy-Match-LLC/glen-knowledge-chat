"""Routes for packing photos: who may see, add and delete them, and that the public
/clip/ route can never reach them."""
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest
from PIL import Image

import app as appmod
from dashboard import orders as _orders
from dashboard import rbac


def _jpeg():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (1, 2, 3)).save(out, "JPEG")
    return out.getvalue()


class FakeR2:
    def __init__(self):
        self.files = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.files[Key] = Body

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.files:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.files[Key]), "ContentType": "image/jpeg"}

    def delete_object(self, Bucket, Key):
        self.files.pop(Key, None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(appmod, "LOG_DB", db_path)
    cx = sqlite3.connect(db_path)
    _orders.init_orders_table(cx)
    cx.execute("INSERT INTO orders (id, created_at, source, external_ref, email, name, items_json, "
               "status, updated_at) VALUES (203,'x','dropship','o203','a@b.com','A','[]','new','x')")
    cx.execute("INSERT INTO orders (id, created_at, source, external_ref, email, name, items_json, "
               "status, updated_at) VALUES (204,'x','in-house','o204','c@d.com','C','[]','new','x')")
    cx.commit(); cx.close()
    r2 = FakeR2()
    monkeypatch.setattr(appmod, "_r2", lambda: r2)
    role = {"r": rbac.OWNER}
    monkeypatch.setattr(appmod, "_bos_actor",
                        lambda: SimpleNamespace(role=role["r"]) if role["r"] else None)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), r2, role


def _upload(c, oid, n=1):
    data = {"photos": [(io.BytesIO(_jpeg()), f"IMG_{i}.JPG") for i in range(n)]}
    return c.post(f"/api/orders/{oid}/packing-photos", data=data,
                  content_type="multipart/form-data")


def test_owner_uploads_lists_views_and_deletes(env):
    c, r2, role = env
    r = _upload(c, 203, 2)
    assert r.status_code == 200 and len(r.get_json()["added"]) == 2
    photos = c.get("/api/orders/203/packing-photos").get_json()["photos"]
    assert len(photos) == 2
    img = c.get(f"/api/orders/203/packing-photos/{photos[0]['id']}?size=thumb")
    assert img.status_code == 200 and img.mimetype == "image/jpeg"
    assert img.headers["Cache-Control"].startswith("private")
    assert c.delete(f"/api/orders/203/packing-photos/{photos[0]['id']}").status_code == 200
    assert len(r2.files) == 2


def test_a_va_can_add_and_view_but_not_delete(env):
    c, r2, role = env
    _upload(c, 203)
    pid = c.get("/api/orders/203/packing-photos").get_json()["photos"][0]["id"]
    role["r"] = rbac.VA
    assert _upload(c, 203).status_code == 200
    assert c.get(f"/api/orders/203/packing-photos/{pid}").status_code == 200
    assert c.delete(f"/api/orders/203/packing-photos/{pid}").status_code == 401


def test_nobody_signed_in_gets_nothing(env):
    c, r2, role = env
    _upload(c, 203)
    pid = c.get("/api/orders/203/packing-photos").get_json()["photos"][0]["id"]
    role["r"] = None
    assert c.get("/api/orders/203/packing-photos").status_code == 401
    assert c.get(f"/api/orders/203/packing-photos/{pid}").status_code == 401
    assert _upload(c, 203).status_code == 401
    assert c.delete(f"/api/orders/203/packing-photos/{pid}").status_code == 401


def test_a_photo_cannot_be_read_through_another_order(env):
    c, r2, role = env
    _upload(c, 203)
    pid = c.get("/api/orders/203/packing-photos").get_json()["photos"][0]["id"]
    assert c.get(f"/api/orders/204/packing-photos/{pid}").status_code == 404
    assert c.delete(f"/api/orders/204/packing-photos/{pid}").status_code == 404


def test_the_public_clip_route_never_serves_packing_photos(env):
    c, r2, role = env
    _upload(c, 203)
    key = next(iter(r2.files))
    role["r"] = None
    assert c.get("/clip/" + key).status_code == 404
    assert c.get("/clip//" + key, follow_redirects=True).status_code == 404


def test_a_bad_file_is_reported_and_the_good_ones_kept(env):
    c, r2, role = env
    data = {"photos": [(io.BytesIO(_jpeg()), "ok.jpg"), (io.BytesIO(b"%PDF-1.4"), "x.pdf")]}
    r = c.post("/api/orders/203/packing-photos", data=data, content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and len(j["added"]) == 1 and j["errors"][0]["file"] == "x.pdf"


def test_an_unknown_order_is_404(env):
    c, r2, role = env
    assert _upload(c, 999).status_code == 404
