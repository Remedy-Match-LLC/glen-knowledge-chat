from pathlib import Path


SHELL = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text()


def test_unlock_gifting_sends_unenrolled_members_to_affiliate_program():
    gifting_block = SHELL.split('card.key === "give"', 1)[1].split(
        "box.appendChild(gb);", 1
    )[0]

    assert 'location.href = "/affiliate"' in gifting_block
    assert 'location.href = "/begin/match"' not in gifting_block
