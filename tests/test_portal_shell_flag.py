"""The shell flag reaches the portal payload, and does so independently of the hub flag.

Both flags on at once is a real state during rollout: the shell ships dark while the
hub is still serving clients. A test that only checks the shell flag in isolation would
miss the two of them being wired to the same value.
"""
import sqlite3

from dashboard import portal_view as pv


def _person(cx):
    cx.executescript("""
        CREATE TABLE people(id INTEGER PRIMARY KEY, email TEXT, name TEXT,
            first_name TEXT, last_name TEXT, roles TEXT,
            address1 TEXT, address2 TEXT, city TEXT, state TEXT, zip TEXT, country TEXT);
        INSERT INTO people(id,email,name,roles) VALUES(1,'c@example.com','A Client','["client"]');
    """)
    return 1


def test_shell_flag_defaults_off():
    with sqlite3.connect(":memory:") as cx:
        pid = _person(cx)
        view = pv.get_portal_view(cx, pid)
    assert view["shell_enabled"] is False


def test_shell_flag_is_independent_of_the_hub_flag():
    with sqlite3.connect(":memory:") as cx:
        pid = _person(cx)
        both = pv.get_portal_view(cx, pid, hub_enabled=True, shell_enabled=True)
        shell_only = pv.get_portal_view(cx, pid, hub_enabled=False, shell_enabled=True)
        hub_only = pv.get_portal_view(cx, pid, hub_enabled=True, shell_enabled=False)
    assert (both["hub_enabled"], both["shell_enabled"]) == (True, True)
    assert (shell_only["hub_enabled"], shell_only["shell_enabled"]) == (False, True)
    assert (hub_only["hub_enabled"], hub_only["shell_enabled"]) == (True, False)
