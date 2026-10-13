"""truststore 0.10.5 broke every chatbot answer in production (2026-10-13).

Its new SSLSocket.do_handshake(self, block) calls super().do_handshake(block). Under the
gevent worker the socket is gevent's, whose do_handshake takes no argument, so each
OpenAI embedding call raised "SSLSocket.do_handshake() takes 1 positional argument but
2 were given". Reproduced with gevent 26.9.0 and httpx: 0.10.4 connects, 0.10.5 raises.
Nothing in this repo imports truststore; a dependency injects it. The pin keeps it below."""
import re
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_keep_truststore_below_the_gevent_breaking_release():
    lines = [l.split("#")[0].strip() for l in (ROOT / "requirements.txt").read_text().splitlines()]
    pins = [l for l in lines if re.match(r"truststore\b", l, re.I)]
    assert len(pins) == 1, pins
    spec = SpecifierSet(pins[0][len("truststore"):])
    assert "0.10.4" in spec and "0.10.5" not in spec
