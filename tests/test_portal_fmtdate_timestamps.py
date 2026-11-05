"""Runs tests/test_portal_fmtdate_timestamps.js, which checks the portal's own fmtDate on
order timestamps (My Remedies showed them raw until 4 Nov 2026)."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_fmtdate_reads_order_timestamps():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, str(ROOT / "tests" / "test_portal_fmtdate_timestamps.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ok test_portal_fmtdate_timestamps" in out.stdout
