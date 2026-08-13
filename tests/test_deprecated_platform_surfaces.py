from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_customer_facing_pages_do_not_route_to_deprecated_platforms():
    paths = [
        ROOT / "static" / "begin-path.html",
        ROOT / "static" / "biofield-ready.html",
        ROOT / "static" / "client-portal.html",
    ]
    customer_copy = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "practicebetter.io" not in customer_copy.lower()
    assert "skool.com" not in customer_copy.lower()
    assert "Practice Better community" not in customer_copy
    assert "I paid in Practice Better" not in customer_copy


def test_staff_console_has_no_deprecated_platform_button():
    console = (ROOT / "static" / "console.html").read_text(encoding="utf-8")
    assert "healingoasis.practicebetter.io" not in console.lower()
    assert "Open Practice Better" not in console


def test_current_destinations_are_named_on_begin_path():
    begin = (ROOT / "static" / "begin-path.html").read_text(encoding="utf-8")
    assert "MentorshipU classroom" in begin
    assert "live community activities" in begin
