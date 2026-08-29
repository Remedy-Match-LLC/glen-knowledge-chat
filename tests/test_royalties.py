from dashboard import royalties


def _deposit(qbo_id, text, amount=12.34):
    return {
        "Id": qbo_id, "TxnDate": "2026-08-20", "TotalAmt": amount,
        "PrivateNote": text,
        "DepositToAccountRef": {"value": "35", "name": "Healing Oasis Checking"},
        "MetaData": {"LastUpdatedTime": "2026-08-21T00:00:00Z"},
    }


def test_classify_is_conservative():
    assert royalties.classify("AMZN KDP PAYMENT") == ("kindle_kdp", "high")
    assert royalties.classify("AUDIBLE ACX ROYALTY") == ("audible_acx", "high")
    assert royalties.classify("Amazon payment") == ("amazon_review", "review")
    assert royalties.classify("ordinary customer deposit") == (None, None)


def test_sync_is_idempotent_and_keeps_ambiguous_amazon_for_review(tmp_path):
    path = str(tmp_path / "royalties.db")
    rows = [_deposit("1", "AMZN KDP PAYMENT", 10),
            _deposit("2", "Amazon Services", 20),
            _deposit("3", "Local customer", 30)]
    fetch = lambda _query: rows
    first = royalties.sync(path, fetch_fn=fetch)
    second = royalties.sync(path, fetch_fn=fetch)
    assert first["matched"] == 2
    assert second["matched"] == 2
    data = royalties.summary(path)
    assert len(data["payments"]) == 2
    assert data["totals_cents"] == {"kindle_kdp": 1000, "amazon_review": 2000}
    assert data["review_count"] == 1


def test_descriptor_reads_deposit_line_entity():
    dep = {"Line": [{"Description": "royalty", "DepositLineDetail": {
        "Entity": {"name": "Audible ACX", "value": "9"}}}]}
    assert "Audible ACX" in royalties.descriptor(dep)
