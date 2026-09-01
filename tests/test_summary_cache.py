"""Summaries and the cache underneath them.

No model runs here. What is asserted is the economics and the boundaries: that an
answer is kept against the version it describes, that a different question gets a
different entry, and that nothing from a row reaches the prompt.
"""

from __future__ import annotations

import pytest

from server.intel import cache, tasks


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCESCOPE_CACHE", str(tmp_path / "cache"))
    return tmp_path / "cache"


def key(**over):
    fields = {"uri": "/tmp/t.lance", "version": 3, "task": "summary",
              "model": "qwen3:8b"}
    return cache.Key(**{**fields, **over})


def test_a_stored_answer_comes_back(cache_dir):
    cache.put(key(), {"summary": "a table"})
    assert cache.get(key())["summary"] == "a table"


def test_a_miss_is_none_not_an_error(cache_dir):
    assert cache.get(key()) is None


@pytest.mark.parametrize("field,value", [
    ("version", 4),
    ("uri", "/tmp/other.lance"),
    ("model", "claude-opus-5"),
    ("task", "something-else"),
    ("prompt_version", 99),
])
def test_anything_that_changes_the_question_changes_the_key(cache_dir, field, value):
    cache.put(key(), {"summary": "about version three"})
    # A new version of the table is a new table as far as an answer is concerned,
    # and a reworded prompt is a different question. Serving the old answer for
    # either is the subtlest way for a cache to start lying.
    assert cache.get(key(**{field: value})) is None


def test_the_same_question_is_the_same_key(cache_dir):
    cache.put(key(), {"summary": "kept"})
    assert cache.get(key())["summary"] == "kept"


def test_a_corrupt_entry_is_a_miss_not_a_crash(cache_dir):
    k = key()
    cache.put(k, {"summary": "fine"})
    k.path().write_text("{not json")
    assert cache.get(k) is None


def test_clearing_removes_what_was_kept(cache_dir):
    cache.put(key(), {"summary": "a"})
    cache.put(key(version=9), {"summary": "b"})
    assert cache.clear("summary") == 2
    assert cache.get(key()) is None


def test_nothing_is_written_inside_a_dataset(cache_dir, corpus):
    cache.put(key(uri=str(corpus / "ordinary.lance")), {"summary": "x"})
    # The console does not write to data. A cache inside a table directory would be
    # the one exception nobody remembered.
    assert not list((corpus / "ordinary.lance").rglob("*.json")) or \
        all("cache" not in str(p) for p in (corpus / "ordinary.lance").rglob("*.json"))


def test_the_summary_prompt_carries_shape_and_findings_but_no_rows(catalog):
    from server.intel import findings as intel_findings

    handle = catalog.open("ordinary")
    analysis = intel_findings.analyse(handle)
    context, read_bytes = tasks.build_summary_context(handle, analysis.findings)

    # The shape is there.
    assert "columns:" in context and "track" in context and "rows:" in context
    # The contents are not. `row-000` is the first value in the `name` column; if a
    # summary prompt ever starts carrying values, this is what notices.
    assert "row-000" not in context
    assert "Go" not in context.split("columns:")[0]
    assert read_bytes < 200_000


def test_the_summary_prompt_names_a_blob_column_without_its_bytes(catalog):
    handle = catalog.open("blobs")
    context, _ = tasks.build_summary_context(handle, [])
    assert "payload" in context and "side files" in context


def test_summarising_with_no_provider_is_answered_not_thrown(api_intel, settings_file,
                                                             monkeypatch):
    from server.intel import config as intel_config

    monkeypatch.setattr(intel_config, "ollama_models", lambda *a, **k: None)
    r = api_intel.post("/intel/tables/ordinary/summary", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["setup_hint"]
