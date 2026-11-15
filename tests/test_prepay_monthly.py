import dashboard.prepay as prepay

def test_public_tiers_exclude_3mo_and_6mo():
    keys = [t["key"] for t in prepay.tiers_public()]
    assert keys == ["1mo", "12mo"]

def test_monthly_total_and_savings():
    assert prepay.monthly_total_cents("12mo") == 10000 * 12        # 120000
    assert prepay.upfront_savings_cents("12mo") == 120000 - 100000 # 20000

def test_commitment_flag_and_fields():
    by = {t["key"]: t for t in prepay.tiers_public()}
    assert set(by) == {"1mo", "12mo"}
    assert by["12mo"]["commitment"] is True and by["1mo"]["commitment"] is False
    assert by["12mo"]["monthly_cents"] == 10000
    assert by["12mo"]["monthly_total_cents"] == 120000
    assert by["12mo"]["upfront_savings_cents"] == 20000
