from pathlib import Path


def test_gunicorn_gevent_worker_has_explicit_packaging_dependency():
    """Gunicorn 26.1 imports packaging from its gevent worker at startup."""
    requirements = {
        line.strip().split("==", 1)[0].split(">=", 1)[0].lower()
        for line in (Path(__file__).resolve().parent.parent / "requirements.txt")
        .read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "gunicorn" in requirements
    assert "gevent" in requirements
    assert "packaging" in requirements
