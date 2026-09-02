"""Secrets from `.cred`, and the export that makes them work.

Two claims worth testing. The environment wins, which is the rule the rest of the
server already follows. And a token read from the file is put back into the
environment — not for convenience, but because listing a Hub repository is an HTTP
call this code makes and *opening* one is pylance calling `huggingface_hub`, which
reads `HF_TOKEN` from the environment and nowhere else. Without the export a private
dataset would list and then fail to open.

Nothing here uses a real token, and nothing here touches the network.
"""

from __future__ import annotations

import pytest

from server import credentials


@pytest.fixture
def cred_file(tmp_path, monkeypatch):
    path = tmp_path / ".cred"
    monkeypatch.setenv(credentials.CRED_FILE, str(path))
    for name in credentials.EXPORTED:
        monkeypatch.delenv(name, raising=False)
    return path


def test_a_missing_cred_file_is_empty_rather_than_an_error(cred_file):
    """Most installs have none, and the server must start anyway."""
    assert credentials.load() == {}
    assert credentials.resolve("HF_TOKEN") == (None, None)
    assert credentials.arm() == []


def test_a_value_in_the_file_is_found_and_says_where_it_came_from(cred_file):
    cred_file.write_text("HF_TOKEN=hf_example\n")
    assert credentials.resolve("HF_TOKEN") == ("hf_example", "cred")


def test_the_environment_wins_over_the_file(cred_file, monkeypatch):
    """A deployment that exports a value should not be overridden by a file someone
    edited months ago — and should be told which one is in play."""
    cred_file.write_text("HF_TOKEN=from_file\n")
    monkeypatch.setenv("HF_TOKEN", "from_env")
    assert credentials.resolve("HF_TOKEN") == ("from_env", "env")


def test_arming_exports_the_token_so_lance_can_see_it_too(cred_file, monkeypatch):
    cred_file.write_text("HF_TOKEN=hf_example\n")
    import os

    assert credentials.arm() == ["HF_TOKEN"]
    assert os.environ["HF_TOKEN"] == "hf_example"


def test_arming_never_overwrites_something_already_exported(cred_file, monkeypatch):
    cred_file.write_text("HF_TOKEN=from_file\n")
    monkeypatch.setenv("HF_TOKEN", "from_env")
    import os

    assert credentials.arm() == []
    assert os.environ["HF_TOKEN"] == "from_env"


def test_quotes_comments_and_junk_are_survivable(cred_file):
    cred_file.write_text(
        '# a comment\n\nHF_TOKEN="quoted"\nAPPLE_ID=someone@example.com\n'
        "not a pair\n=novalue\n")
    values = credentials.load()
    assert values["HF_TOKEN"] == "quoted"
    assert values["APPLE_ID"] == "someone@example.com"


def test_an_unreadable_cred_file_does_not_stop_the_server(cred_file, monkeypatch):
    cred_file.write_bytes(b"\xff\xfe\x00binary nonsense")
    assert isinstance(credentials.load(), dict)


def test_a_token_becomes_an_authorization_header_and_its_absence_does_not(
        cred_file, monkeypatch):
    from server import hf

    assert "Authorization" not in hf._headers()
    cred_file.write_text("HF_TOKEN=hf_example\n")
    assert hf._headers()["Authorization"] == "Bearer hf_example"
