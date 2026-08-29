# tests/test_pg_pool_checks_connections.py
"""The Postgres pool must validate a connection before handing it out.

psycopg_pool's `check` defaults to None: it never tests a pooled connection on
checkout. Render's Postgres closes idle connections, so a dead one sits in the
pool and is served to the next request, whose first I/O dies with

    consuming input failed: SSL error: unexpected eof while reading

43 of those in 31 hours of production logs on 2026-08-29, each a 500 to whoever
made that request, indiscriminate across every endpoint.

VERIFIED BEHAVIOURALLY against a real Postgres 16, with a control -- the fix is
not merely "the kwarg is present":

    connect -> query -> return to pool
    pg_terminate_backend() every backend on that database
    connect again:
        without check=  ->  AdminShutdown: terminating connection ...   (500)
        with    check=  ->  SELECT 1 returns 1                          (recovered)

CI is secretless and has no Postgres, so this test pins the CONFIGURATION. To
redo the behavioural proof, repeat the sequence above against a local PG.
"""
import ast
import pathlib


def _pool_call():
    src = (pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "db.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ConnectionPool"):
            return node
    return None


def test_the_pool_is_constructed_with_a_connection_check():
    call = _pool_call()
    assert call is not None, "ConnectionPool() construction is gone"
    kw = {k.arg: k for k in call.keywords}
    assert "check" in kw, (
        "pool has no check= : a connection closed server-side while idle will be "
        "served to the next request and 500 it")


def test_the_check_is_the_librarys_own_validator():
    """`check=` must be a real validator, not a truthy placeholder."""
    call = _pool_call()
    kw = {k.arg: k for k in call.keywords}
    node = kw["check"].value
    rendered = ast.unparse(node)
    assert "check_connection" in rendered, rendered


def test_lifetime_and_idle_are_left_at_library_defaults():
    """max_lifetime (3600s) and max_idle (600s) already default sensibly. Pinning
    that we do NOT set them keeps the diff honest: `check` was the actual gap,
    and someone re-tuning these should have to think about it rather than
    inherit a number nobody chose."""
    call = _pool_call()
    kw = {k.arg for k in call.keywords}
    assert "max_lifetime" not in kw and "max_idle" not in kw
