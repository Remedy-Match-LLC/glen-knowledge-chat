"""The small powder jar (Glen, 2026-09-23): jar Ø35 x H30 mm with a thin 70 x 2 x 5 mm
stick scoop packed alongside, for 5-MTHF and Adenosyl Cobalamin."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from dashboard import shipping as sh

P = json.loads((Path(__file__).resolve().parents[1] / "data" / "products.json")
               .read_text())["products"]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    sh.init_shipping_schema(cx)
    cx.commit()
    cx.close()
    return path


def test_the_two_powders_use_the_jar():
    assert P["5mthf"]["bottle_type"] == "Small powder jar"
    assert P["adenosyl-cobalamin"]["bottle_type"] == "Small powder jar"


def test_prod_knows_the_name():
    assert "Small powder jar" in sh.PROD_BOTTLE_NAMES


def test_the_jar_is_its_own_size(db):
    """The stick scoop slides between jars; the 500 mg scoop belongs to 30 g / 45 g."""
    assert sh.get_bottle_dims(db_path=db)["Small powder jar"] == (35, 30)


@pytest.mark.parametrize("n,boxes", [(1, ["S"]), (20, ["S"]), (21, ["M"])])
def test_jars_alone(db, n, boxes):
    assert sh.pick_boxes({"Small powder jar": n}, db_path=db) == boxes


def test_a_mixed_order_is_not_over_boxed(db):
    """Without the S count this fell to geometry and went Medium."""
    assert sh.pick_boxes({"Small powder jar": 4, "30 Caps": 4}, db_path=db) == ["S"]


def test_an_existing_catalog_gets_it_on_deploy(tmp_path):
    """Prod's catalog is not fresh: the backfill must add the row there too."""
    path = str(tmp_path / "old.db")
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    sh.init_shipping_schema(cx)
    cx.execute("DELETE FROM box_capacity WHERE bottle_type_id IN "
               "(SELECT id FROM bottle_types WHERE name='Small powder jar')")
    cx.execute("DELETE FROM bottle_types WHERE name='Small powder jar'")
    cx.commit()
    sh.init_shipping_schema(cx)
    cx.commit()
    cx.close()
    assert sh.get_bottle_dims(db_path=path)["Small powder jar"] == (35, 30)
    assert sh.pick_boxes({"Small powder jar": 4, "30 Caps": 4}, db_path=path) == ["S"]
