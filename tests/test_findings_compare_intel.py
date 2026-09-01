"""Findings, compare mode, and provider resolution — the parts with no corpus in them.

These three share a property worth testing together: none of them should need the
demo's data, a model, a key, or a network. If any of them stops working without those
things, the console has quietly become an application that requires them.
"""

from __future__ import annotations

import pytest

from server.intel import findings as intel_findings

# ------------------------------------------------------------------------ findings

def test_an_unindexed_vector_column_is_a_finding(api):
    ids = {f["id"] for f in api.get("/catalog/tables/vectors/findings").json()["findings"]}
    assert "vector-column-unindexed" in ids


def test_an_indexed_vector_column_is_not_a_finding(api):
    ids = {f["id"] for f in api.get("/catalog/tables/indexed/findings").json()["findings"]}
    assert "vector-column-unindexed" not in ids


def test_a_blob_table_reports_its_split_from_measured_bytes(api):
    body = api.get("/catalog/tables/blobs/findings").json()
    split = next((f for f in body["findings"] if f["id"] == "blob-heavy-table"), None)
    assert split is not None
    assert split["evidence"]["blob_bytes"] > split["evidence"]["meta_bytes"]


def test_an_ordinary_table_has_nothing_alarming_to_say(api):
    body = api.get("/catalog/tables/ordinary/findings").json()
    assert body["summary"]["warn"] == 0
    assert body["partial_analysis"] is False


def test_every_finding_carries_evidence_and_a_panel(api):
    for table in ("ordinary", "vectors", "blobs", "versioned"):
        for f in api.get(f"/catalog/tables/{table}/findings").json()["findings"]:
            assert f["evidence"], f"{f['id']} has no evidence"
            assert f["panel"] in intel_findings.PANELS


def test_a_rule_that_raises_is_reported_not_swallowed(api, monkeypatch):
    def explodes(_facts):
        raise ZeroDivisionError("deliberately broken rule")

    monkeypatch.setattr(intel_findings, "RULES", (*intel_findings.RULES, explodes))
    monkeypatch.setattr(intel_findings.log, "exception", lambda *a, **k: None)

    body = api.get("/catalog/tables/ordinary/findings").json()
    assert body["partial_analysis"] is True
    assert any(f["error"] == "ZeroDivisionError" for f in body["failed_rules"])


def test_a_broken_rule_does_not_take_the_working_ones_down(api, monkeypatch):
    before = len(api.get("/catalog/tables/blobs/findings").json()["findings"])

    def explodes(_facts):
        raise RuntimeError("nope")

    monkeypatch.setattr(intel_findings, "RULES", (*intel_findings.RULES, explodes))
    monkeypatch.setattr(intel_findings.log, "exception", lambda *a, **k: None)

    after = api.get("/catalog/tables/blobs/findings").json()
    assert len(after["findings"]) == before
    assert after["partial_analysis"] is True


# ------------------------------------------------------------------------- compare

def test_two_versions_can_be_compared(api):
    body = api.get("/catalog/tables/versioned/compare",
                   params={"a": 1, "b": 2}).json()
    assert body["a"]["version"] == 1 and body["b"]["version"] == 2
    assert body["diff"]["rows"] > 0            # v2 appended


def test_an_index_build_shows_up_as_an_index_build(api):
    body = api.get("/catalog/tables/versioned/compare",
                   params={"a": 2, "b": 3}).json()
    assert body["diff"]["indices"]["added"], body["diff"]["indices"]
    assert body["diff"]["rows"] == 0           # an index moves no rows


def test_a_version_that_is_not_there_is_a_400(api):
    assert api.get("/catalog/tables/versioned/compare",
                   params={"a": 1, "b": 99}).status_code == 400


def test_a_query_one_version_cannot_answer_is_a_result(api):
    """Full text before the inverted index existed. The most useful before/after
    there is, and one that would be thrown away by treating a refusal as an error."""
    body = api.post("/catalog/tables/versioned/compare/query",
                    json={"a": 1, "b": 3, "mode": "fts", "text": "kubernetes",
                          "limit": 5}).json()
    assert body["a"] is None and body["b"] is not None
    assert body["ran_both"] is False
    assert "cannot answer" in body["verdict"]


def test_a_query_both_versions_answer_is_compared_by_bytes(api):
    body = api.post("/catalog/tables/versioned/compare/query",
                    json={"a": 1, "b": 2, "mode": "scan", "limit": 5}).json()
    assert body["ran_both"] is True
    assert "bytes_delta" in body


# ---------------------------------------------------------------------- providers

@pytest.fixture
def no_ollama(monkeypatch):
    from server.intel import config as intel_config

    monkeypatch.setattr(intel_config, "ollama_models", lambda *a, **k: None)


@pytest.fixture
def local_ollama(monkeypatch):
    from server.intel import config as intel_config

    monkeypatch.setattr(intel_config, "ollama_models",
                        lambda *a, **k: ["qwen3:8b", "llama3.2:3b"])


def resolve(**fields):
    from server import settings as cfg
    from server.intel import config as intel_config

    s = cfg.load()
    for k, v in fields.items():
        setattr(s.intelligence, k, v)
    return intel_config.resolve(s)


def test_nothing_configured_resolves_to_nothing_with_a_hint(settings_file, no_ollama):
    r = resolve()
    assert r.provider == "none" and not r.available and r.setup_hint


def test_a_key_alone_brings_up_the_claude_path(settings_file, no_ollama, monkeypatch):
    from server.intel import registry

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    r = resolve()
    assert r.provider == "anthropic"
    assert r.models["deep"] == registry.ANTHROPIC_DEFAULT


def test_a_local_runtime_alone_brings_up_the_language_layer(settings_file, local_ollama):
    r = resolve()
    assert r.provider == "ollama" and r.available
    assert r.models["deep"] == "qwen3:8b"          # the measured known-good one


def test_with_both_the_key_wins_and_a_pin_beats_them_both(settings_file, local_ollama,
                                                          monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    assert resolve().provider == "anthropic"
    assert resolve(provider="ollama").provider == "ollama"


def test_a_stored_key_is_scoped_to_the_provider_it_was_stored_for(settings_file):
    from server import settings as cfg

    intel = cfg.Intelligence(provider="ollama", api_key="not-an-anthropic-key")
    key, source = cfg.api_key_for(intel, "anthropic")
    assert key is None and source is None


def test_an_unknown_model_is_usable_and_priced_at_nothing_known(settings_file):
    from server.intel import registry

    m = registry.lookup("something-nobody-has-heard-of", "openai-compat")
    assert m.input_usd_per_mtok is None
    assert registry.cost_usd(m, 1000, 1000) is None
    assert m.tools is False        # the cautious assumption, not the convenient one


def test_a_local_model_costs_zero_rather_than_unknown(settings_file):
    from server.intel import registry

    m = registry.lookup("qwen3:8b", "ollama")
    assert registry.cost_usd(m, 1_000_000, 1_000_000) == 0.0
