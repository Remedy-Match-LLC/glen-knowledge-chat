"""The funnel's Group Coaching line matches the live class time.

Group Coaching moved to 3:00 PM HST on Wednesday 30 September 2026 (#1780; the MasterClass is
at 2:00 PM). begin_funnel.py still said 2:00 PM. This pins the copy to the scheduler's hour.
"""
import re
from pathlib import Path


def _coaching_hour_from_app():
    m = re.search(r"^LIVE_GROUP_COACHING_HOUR\s*=\s*(\d+)", Path("app.py").read_text(), re.M)
    assert m, "LIVE_GROUP_COACHING_HOUR not found in app.py"
    return int(m.group(1))


def test_the_funnel_names_the_live_group_coaching_hour():
    hour = _coaching_hour_from_app()
    label = f"{hour - 12 if hour > 12 else hour}:00 PM Hawaii time"
    src = Path("begin_funnel.py").read_text()
    line = next(l for l in src.splitlines() if "'Live weekly group coaching'" in l)
    assert label in line, line
    assert "2:00 PM" not in line
