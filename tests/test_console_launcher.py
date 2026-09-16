"""The console tab that launches a local tool must forward the key THIS SERVER
holds, not the one the browser happened to remember. See dashboard/console_launcher."""
from dashboard.console_launcher import local_tool_url


def test_forwards_the_servers_own_key(monkeypatch):
    monkeypatch.delenv("BIOFIELD_LOCAL_URL", raising=False)
    assert local_tool_url("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011", "abc123") == \
        "http://127.0.0.1:8011/?key=abc123"


def test_a_configured_base_wins_and_never_doubles_the_slash(monkeypatch):
    monkeypatch.setenv("BIOFIELD_LOCAL_URL", "http://10.0.0.4:8011/")
    assert local_tool_url("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011", "k") == \
        "http://10.0.0.4:8011/?key=k"


def test_no_secret_means_no_key_in_the_url(monkeypatch):
    monkeypatch.delenv("BIOFIELD_LOCAL_URL", raising=False)
    for blank in ("", None, "   "):
        assert local_tool_url("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011", blank) == \
            "http://127.0.0.1:8011/"


def test_the_key_is_url_encoded(monkeypatch):
    """A rotated key is 32 random chars; one containing & or / would otherwise
    truncate the query or escape the path."""
    monkeypatch.delenv("BIOFIELD_LOCAL_URL", raising=False)
    got = local_tool_url("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011", "a/b&c=d")
    assert got == "http://127.0.0.1:8011/?key=a%2Fb%26c%3Dd"


def test_an_empty_env_var_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("BIOFIELD_LOCAL_URL", "")
    assert local_tool_url("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011", "k") == \
        "http://127.0.0.1:8011/?key=k"


def test_a_path_is_honoured_for_the_tags_page(monkeypatch):
    monkeypatch.delenv("BIOFIELD_LOCAL_URL", raising=False)
    assert local_tool_url("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011", "k",
                          path="/clinical-tags") == \
        "http://127.0.0.1:8011/clinical-tags?key=k"


def test_the_path_never_doubles_a_slash(monkeypatch):
    monkeypatch.setenv("BIOFIELD_LOCAL_URL", "http://127.0.0.1:8011/")
    assert local_tool_url("BIOFIELD_LOCAL_URL", "x", "k", path="clinical-tags") == \
        "http://127.0.0.1:8011/clinical-tags?key=k"
