import json

import biofield_local_app


def test_default_profile_fetch_uses_consolidated_clinical_endpoint(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"profile": {"historical_intake_count": 1}}).encode()

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setenv("CONSOLE_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr("urllib.request.urlopen", fake_open)

    profile = biofield_local_app._default_fetch_profile("Rebecca Navo@example.com")

    assert "/api/console/clinical-profile/" in seen["url"]
    assert "Rebecca%20Navo%40example.com" in seen["url"]
    assert profile["historical_intake_count"] == 1
