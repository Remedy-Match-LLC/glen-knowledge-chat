import csv, sqlite3
from dashboard import legacy_fmp_invoices as legacy


def _write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def test_build_and_idempotent_merge(tmp_path):
    contacts=tmp_path/"contacts.csv"; inv=tmp_path/"inv.csv"; items=tmp_path/"items.csv"
    _write(contacts,["Contact ID","First","Last","Company Name","email","Phone1"],
           [{"Contact ID":"7","First":"Ada","Last":"A","email":"A@X.com"}])
    _write(inv,["Invoice ID","Contact ID","Invoice Date","Sub Total","Grand Total","Shipping Cost"],
           [{"Invoice ID":"23","Contact ID":"7","Invoice Date":"1/2/2020","Sub Total":"10","Grand Total":"12","Shipping Cost":"2"}])
    _write(items,["Invoice ID","Product ID","Description","Quantity","Price","Line Item Total"],
           [{"Invoice ID":"23.0","Product ID":"4.0","Description":"R","Quantity":"1","Price":"10","Line Item Total":"10"}])
    p=legacy.build_payload(inv,items,contacts)
    assert p["clients"][0][4]=="a@x.com" and p["invoices"][0][2]=="2020-01-02"
    cx=sqlite3.connect(":memory:")
    assert legacy.ingest_payload(cx,p)["invoices"]["inserted"]==1
    first=legacy.ingest_payload(cx,p,commit=True)
    assert first["clients"]["inserted"]==1 and first["items"]["inserted"]==1
    second=legacy.ingest_payload(cx,p,commit=True)
    assert second["invoices"]["inserted"]==0 and second["invoices"]["existing"]==1
