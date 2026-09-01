"""The token meter, and the ceiling that is meant to stop a bill.

None of this needs a model: what matters is the arithmetic, and that a cache hit and
an unpriced model are counted as the different things they are.
"""

from __future__ import annotations

import pytest

from server.intel import meter as intel_meter
from server.intel.providers import Usage


@pytest.fixture
def meter(monkeypatch):
    m = intel_meter.Meter()
    monkeypatch.setattr(intel_meter, "METER", m)
    return m


def test_a_fresh_meter_has_spent_nothing(meter):
    d = meter.as_dict()
    assert d["calls"] == 0 and d["cost_usd"] == 0.0 and d["input_tokens"] == 0


def test_a_call_moves_tokens_and_dollars(meter):
    meter.record(Usage(1000, 200, 0), 0.0075)
    d = meter.as_dict()
    assert d["input_tokens"] == 1000 and d["output_tokens"] == 200
    assert d["cost_usd"] == 0.0075 and d["calls"] == 1


def test_a_local_model_costs_zero_and_is_still_a_call(meter):
    meter.record(Usage(50, 20, 0), 0.0)
    d = meter.as_dict()
    assert d["calls"] == 1 and d["cost_usd"] == 0.0 and d["unpriced_calls"] == 0


def test_an_unpriced_model_is_counted_apart_from_a_free_one(meter):
    """A model we cannot price and a model that is free are different claims, and
    reporting both as $0.00 would make one of them a lie."""
    meter.record(Usage(50, 20, 0), None)
    d = meter.as_dict()
    assert d["calls"] == 1 and d["unpriced_calls"] == 1 and d["cost_usd"] == 0.0


def test_a_cache_hit_is_counted_as_a_call_that_did_not_happen(meter):
    meter.record_cache_hit()
    d = meter.as_dict()
    # Recording a served-from-disk answer as spend would make the cache look useless
    # in exactly the number that proves it works.
    assert d["cache_hits"] == 1 and d["calls"] == 0 and d["cost_usd"] == 0.0


def test_resetting_forgets_everything(meter):
    meter.record(Usage(10, 10, 0), 1.0)
    meter.record_cache_hit()
    meter.reset()
    d = meter.as_dict()
    assert d["calls"] == 0 and d["cache_hits"] == 0 and d["cost_usd"] == 0.0


def test_no_ceiling_means_no_refusal(meter, monkeypatch):
    monkeypatch.delenv("LANCESCOPE_SPEND_CEILING", raising=False)
    monkeypatch.setattr(intel_meter, "spend_ceiling", lambda: None)
    meter.record(Usage(1, 1, 0), 1_000.0)
    meter.check_ceiling()          # does not raise


def test_a_ceiling_refuses_before_the_next_call(meter, monkeypatch):
    monkeypatch.setattr(intel_meter, "spend_ceiling", lambda: 0.10)
    meter.record(Usage(1, 1, 0), 0.09)
    meter.check_ceiling()          # still under
    meter.record(Usage(1, 1, 0), 0.02)
    # Refusing after the money is gone is not a limit, it is a receipt. The check
    # happens before the call that would exceed it.
    with pytest.raises(intel_meter.SpendCeiling):
        meter.check_ceiling()


def test_the_ceiling_is_read_from_the_environment(meter, monkeypatch):
    monkeypatch.setenv("LANCESCOPE_SPEND_CEILING", "0.01")
    assert intel_meter.spend_ceiling() == 0.01


def test_a_nonsense_ceiling_is_ignored_rather_than_fatal(meter, monkeypatch, settings_file):
    monkeypatch.setenv("LANCESCOPE_SPEND_CEILING", "not-a-number")
    assert intel_meter.spend_ceiling() is None


def test_the_meter_endpoint_answers_and_resets(api_intel, meter):
    meter.record(Usage(100, 50, 0), 0.001)
    body = api_intel.get("/intel/meter").json()
    assert body["input_tokens"] == 100 and body["calls"] == 1
    after = api_intel.post("/intel/meter/reset").json()
    assert after["calls"] == 0


def test_a_summary_over_the_ceiling_is_refused_not_attempted(api_intel, meter,
                                                             settings_file, monkeypatch):
    from server.intel import config as intel_config

    monkeypatch.setattr(intel_config, "ollama_models", lambda *a, **k: ["qwen3:8b"])
    monkeypatch.setattr(intel_meter, "spend_ceiling", lambda: 0.01)
    meter.record(Usage(1, 1, 0), 0.02)

    r = api_intel.post("/intel/tables/ordinary/summary", json={})
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False and body.get("ceiling_reached") is True
    # And nothing was spent finding that out.
    assert meter.as_dict()["calls"] == 1
