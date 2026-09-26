import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_portal_fold_rules():
    r = subprocess.run(["node", str(ROOT / "tests" / "test_portal_folds_rules.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout and "OK2" in r.stdout and "OK3" in r.stdout
