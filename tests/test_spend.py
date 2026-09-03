"""The spend ledger, and the panel's arithmetic over it.

The meter answers "this process, since it started". The ledger answers the question
somebody holding a provider key actually has — where the money went — and it has to
survive a restart to do that. None of this needs a model: what matters is that a
line is written for every real call, that a cache hit is recorded as a saving rather
than a cost, and that the rollups keep those two apart.
"""

from __future__ import annotations

import json
import time

import pytest

from server.intel import ledger
from server.intel import meter as intel_meter
from server.intel.providers import Usage


@pytest.fixture
def meter(monkeypatch):
    m = intel_meter.Meter()
    monkeypatch.setattr(intel_meter, "METER", m)
    return m


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_nothing_is_written_when_the_log_is_off(meter, settings_file, monkeypatch):
    monkeypatch.setenv("LANCESCOPE_SPEND_LOG", "off")
    meter.record(Usage(10, 5, 0), 0.01, task="filter", model="claude-opus-5")
    assert not ledger.path().exists()
    # And the in-process meter still counted it: the ledger is a record, not the
    # accounting.
    assert meter.as_dict()["calls"] == 1


def test_a_call_writes_one_line_with_what_it_cost(meter, spend_ledger):
    meter.record(Usage(1000, 200, 50), 0.0075, task="filter",
                 provider="anthropic", model="claude-opus-5", ms=1400)
    (row,) = lines(spend_ledger)
    assert row["task"] == "filter" and row["model"] == "claude-opus-5"
    assert row["input_tokens"] == 1000 and row["output_tokens"] == 200
    assert row["cost_usd"] == 0.0075 and row["ms"] == 1400
    assert row["cached"] is False


def test_the_ledger_records_no_content(meter, spend_ledger):
    """Counts, never the question. A spend log that accumulated what people asked
    their database would be a worse privacy story than the feature is worth."""
    meter.record(Usage(1, 1, 0), 0.01, task="filter", model="m")
    (row,) = lines(spend_ledger)
    assert set(row) == {"ts", "task", "provider", "model", "input_tokens",
                        "output_tokens", "cache_read_tokens", "cost_usd", "ms",
                        "cached", "avoided_usd"}


def test_an_unpriced_model_is_written_as_unknown_not_as_zero(meter, spend_ledger):
    meter.record(Usage(10, 10, 0), None, task="summary", model="mystery-7b")
    (row,) = lines(spend_ledger)
    assert row["cost_usd"] is None


def test_a_cache_hit_is_a_saving_and_never_a_cost(meter, spend_ledger):
    meter.record_cache_hit(task="summary", model="claude-opus-5", avoided_usd=0.02)
    (row,) = lines(spend_ledger)
    assert row["cached"] is True and row["cost_usd"] == 0.0
    assert row["avoided_usd"] == 0.02


def test_a_corrupt_line_is_skipped_rather_than_fatal(meter, spend_ledger):
    meter.record(Usage(1, 1, 0), 0.01, task="filter", model="m")
    with spend_ledger.open("a") as fh:
        fh.write('{"ts": 1, "trunc\n')          # an append that lost its tail
    meter.record(Usage(2, 2, 0), 0.02, task="filter", model="m")
    assert len(ledger.read()) == 2


def test_reading_a_missing_ledger_is_empty_not_an_error(settings_file):
    assert ledger.read() == []


def test_clearing_forgets_everything(meter, spend_ledger):
    meter.record(Usage(1, 1, 0), 0.01, task="filter", model="m")
    ledger.clear()
    assert ledger.read() == []


def test_a_window_excludes_what_is_older_than_it(spend_ledger):
    ledger.record(task="filter", provider="anthropic", model="m", cost_usd=1.0)
    (row,) = lines(spend_ledger)
    row["ts"] = time.time() - 10 * 86400
    spend_ledger.write_text(json.dumps(row) + "\n")
    assert ledger.read(since=time.time() - 86400) == []
    assert len(ledger.read(since=time.time() - 30 * 86400)) == 1


# ------------------------------------------------------------------ the endpoint

def test_the_spend_endpoint_is_empty_and_honest_before_anything_is_spent(
        api_intel, spend_ledger):
    body = api_intel.get("/intel/spend").json()
    assert body["totals"]["calls"] == 0 and body["totals"]["cost_usd"] == 0.0
    assert body["by_task"] == [] and body["recent"] == []
    # Every day in the window is still plotted, so a quiet week reads as a quiet
    # week rather than as no week.
    assert len(body["daily"]) == body["window_days"] == 30


def test_the_endpoint_rolls_up_by_day_task_and_model(api_intel, meter, spend_ledger):
    meter.record(Usage(1000, 100, 0), 0.01, task="filter",
                 provider="anthropic", model="claude-haiku-4-5", ms=800)
    meter.record(Usage(4000, 600, 0), 0.05, task="summary",
                 provider="anthropic", model="claude-opus-5", ms=6000)
    meter.record_cache_hit(task="summary", provider="anthropic",
                           model="claude-opus-5", avoided_usd=0.05)

    body = api_intel.get("/intel/spend").json()
    t = body["totals"]
    assert t["calls"] == 2 and t["cache_hits"] == 1
    assert t["cost_usd"] == pytest.approx(0.06)
    # The saving is reported beside the spend and never inside it.
    assert t["avoided_usd"] == pytest.approx(0.05)
    assert t["input_tokens"] == 5000

    tasks = {b["task"]: b for b in body["by_task"]}
    assert tasks["filter"]["calls"] == 1 and tasks["filter"]["avg_ms"] == 800
    assert tasks["summary"]["cache_hits"] == 1
    models = {b["model"]: b for b in body["by_model"]}
    assert models["claude-opus-5"]["cost_usd"] == pytest.approx(0.05)

    today = body["daily"][-1]
    assert today["calls"] == 2 and today["cost_usd"] == pytest.approx(0.06)
    # And a day says which tasks made it up, so a bar can be read as the mix of
    # things this tool does rather than as one number.
    assert today["tasks"]["summary"]["cost_usd"] == pytest.approx(0.05)
    assert today["tasks"]["filter"]["calls"] == 1


def test_an_unpriced_call_is_counted_apart_rather_than_as_free(
        api_intel, meter, spend_ledger):
    meter.record(Usage(10, 10, 0), None, task="filter", provider="ollama",
                 model="qwen3:8b")
    body = api_intel.get("/intel/spend").json()
    assert body["totals"]["unpriced_calls"] == 1
    assert body["totals"]["cost_usd"] == 0.0


def test_the_endpoint_carries_the_rates_the_figures_came_from(api_intel, spend_ledger):
    body = api_intel.get("/intel/spend").json()
    ids = {m["id"] for m in body["rates"]["models"]}
    assert "claude-opus-5" in ids and body["rates"]["priced_on"]


def test_clearing_through_the_endpoint_empties_the_history(api_intel, meter,
                                                           spend_ledger):
    meter.record(Usage(1, 1, 0), 0.01, task="filter", model="m")
    api_intel.delete("/intel/spend")
    assert api_intel.get("/intel/spend").json()["totals"]["calls"] == 0
