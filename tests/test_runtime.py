"""The reader report, and the two ways it is allowed to be wrong.

This is the module the version matrix rests on: CI fails a pylance row when
`runtime().degraded` is non-empty, and the container images refuse to publish on the
same signal. So it has to be right about a version it is not running on, which means
the detection has to be tested against something other than the installed library.
"""

from __future__ import annotations

import server.runtime as rt


def _fresh():
    """The report, recomputed. It is cached for the life of the process, and a test
    that changed what it detects would otherwise read the previous answer."""
    rt.runtime.cache_clear()
    return rt.runtime()


def test_it_names_the_reader_not_lancedb():
    """`lancedb` is deliberately absent.

    The server never imports it — `tests/test_write_quarantine.py` enforces that,
    because it is not in the packaged app's dependency group — and it is not the
    reader anyway. A version report that named it would send someone pinning the
    wrong package.
    """
    versions = _fresh().versions
    assert "lance" in versions
    assert "lancedb" not in versions
    assert versions["lance"] not in ("", "?")


def test_a_complete_reader_says_nothing():
    """The common case has to cost no words.

    `summary` is what the settings page renders; `None` is how it renders as
    nothing rather than as a reassuring paragraph nobody needs.
    """
    report = _fresh()
    assert report.degraded == []
    assert report.as_dict()["summary"] is None


def test_a_missing_feature_is_named_and_costed(monkeypatch):
    """The case this module exists for, forced.

    Detection is by attribute, so removing the attribute is the honest way to
    simulate an older Lance — closer to what actually happens on pylance 0.38 than
    stubbing the report would be.
    """
    import lance

    monkeypatch.delattr(lance.LanceDataset, "io_stats_incremental", raising=True)
    report = _fresh()

    missing = [f.name for f in report.degraded]
    assert missing == ["cost accounting"]

    entry = next(f for f in report.as_dict()["features"] if f["name"] == "cost accounting")
    assert entry["supported"] is False
    # `lost` says what stops working, for a reader looking at the gap it left.
    assert entry["lost"] and "byte" in entry["lost"]
    # `probe` says which symbol, for whoever has to fix it.
    assert "io_stats_incremental" in entry["probe"]
    assert "cost accounting" in report.as_dict()["summary"]


def test_index_statistics_accepts_either_spelling(monkeypatch):
    """The method moved to a `stats` accessor and the old name is deprecated but
    still present. Both answer the question, so both count — checking only one name
    would report half the supported range as degraded over a rename."""
    import lance

    monkeypatch.delattr(lance.LanceDataset, "index_statistics", raising=False)
    assert rt.supports("index statistics"), "the fallback spelling was not accepted"

    # And with neither, it is honestly reported as missing rather than assumed.
    monkeypatch.delattr(lance.LanceDataset, "stats", raising=False)
    rt.runtime.cache_clear()
    assert not rt.supports("index statistics")


def test_supports_is_the_same_answer():
    _fresh()
    assert rt.supports("cost accounting") is True
    assert rt.supports("no such feature") is False
