from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "app.py").read_text()


def test_join_affiliate_step_opens_consultative_intake_directly():
    assert "https://illtowell.com/begin/intake?utm_source={slug}" in APP
    assert "utm_medium=affiliate&utm_campaign=begin-deeplink-join" in APP
    assert "Opens the consultative intake" in APP
