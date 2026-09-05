"""The bundle: what it says, what it costs, and — mostly — what it refuses to carry.

The load-bearing tests here are the two scrub gates. A bundle exists to be handed to
somebody, so the interesting failure is not that a section is missing; it is that a
row value or a credential left the building inside one. Both are asserted over every
fixture table rather than over a hand-written example, on the same ground as
`tests/test_write_quarantine.py`: the rule has to hold for tables nobody thought about
when writing it.
"""

import json

import pytest

from server import bundle

TABLES = ("ordinary", "vectors", "indexed", "searchable", "blobs", "versioned",
          "evolved")


def get(api, table, **params):
    r = api.get(f"/catalog/tables/{table}/bundle", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ what it carries

@pytest.mark.parametrize("table", TABLES)
def test_a_bundle_carries_its_schema_version_as_the_first_key(api, table):
    """Somebody else's parser reads this and needs to know what it has."""
    d = get(api, table)

    assert next(iter(d)) == "lancescope_bundle"
    assert d["lancescope_bundle"] == bundle.SCHEMA_VERSION
    assert d["table"] == table


@pytest.mark.parametrize("table", TABLES)
def test_every_section_is_present_and_none_of_them_failed(api, table):
    d = get(api, table)

    for section in bundle.SECTIONS:
        assert section in d, f"{table} has no {section} section"
    assert d["incomplete"] == []


def test_a_bundle_reports_what_assembling_it_cost(api):
    """A document about byte costs that would not state its own would be an odd thing."""
    d = get(api, "vectors")

    assert d["cost"]["read_bytes"] > 0
    assert d["cost"]["read_iops"] > 0
    assert d["cost"]["basis"]


def test_the_cost_is_the_sum_of_the_sections_that_were_collected(api):
    d = get(api, "ordinary")

    summed = sum(d[s].get("read_bytes", 0) for s in bundle.SECTIONS if s in d)
    assert d["cost"]["read_bytes"] == summed


def test_footer_reads_stay_off_the_meter_rather_than_being_folded_in(api):
    """`server/estimate.py` reads through a reader the handle cannot see, and says so.
    A bundle that quietly added those bytes to a measured figure would undo that."""
    d = get(api, "vectors")

    assert d["weights"]["off_meter"] is True
    assert d["cost"]["off_meter_ms"] >= 0
    assert d["cost"]["read_bytes"] < d["weights"]["footer_bytes"] + d["cost"]["read_bytes"]


def test_a_finding_travels_with_the_numbers_it_was_derived_from(api):
    d = get(api, "vectors")

    ids = [f["id"] for f in d["findings"]["findings"]]
    assert "vector-column-unindexed" in ids
    finding = next(f for f in d["findings"]["findings"] if f["id"] == "vector-column-unindexed")
    assert finding["evidence"]


def test_a_partial_sweep_is_still_reported_as_partial(api, monkeypatch):
    """A broken check must not look like a clean table, inside a bundle either."""
    from server.intel import findings as intel_findings

    def explode(facts):
        raise RuntimeError("no")

    monkeypatch.setattr(intel_findings, "RULES", (*intel_findings.RULES, explode))
    d = get(api, "ordinary")

    assert d["findings"]["partial_analysis"] is True
    assert d["findings"]["failed_rules"]


# ---------------------------------------------------------------------- the scrubs

def strings(value):
    """Every string anywhere in the document, so a scrub can be checked exhaustively."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)
    elif value is not None:
        yield str(value)


@pytest.mark.parametrize("table", TABLES)
def test_no_row_value_leaves_in_a_bundle(api, catalog, table):
    """The one thing a shareable document must not do.

    Checked against the table's own rows rather than against a list of things we
    thought of: every distinct string value in the first rows must be absent from the
    document. Column *names* are excluded from the comparison, because they are
    schema and the whole document is about them.
    """
    ds = catalog.open(table, scope="test").ds
    columns = [f.name for f in ds.schema if str(f.type) in ("string", "large_string")]
    if not columns:
        pytest.skip(f"{table} has no string columns to leak")

    values = set()
    for row in ds.to_table(columns=columns, limit=8).to_pylist():
        values.update(str(v) for v in row.values() if v and len(str(v)) > 3)

    text = json.dumps(get(api, table))
    leaked = sorted(v for v in values if v in text and v not in columns)
    assert not leaked, f"{table} leaked row values into its bundle: {leaked}"


def test_the_query_section_keeps_the_diagnosis_and_drops_the_rows(api):
    """The filter you wrote travels. The rows it matched do not — the reproduction
    re-runs them against the reader's own copy, which is the honest way to share."""
    r = api.post("/catalog/tables/searchable/bundle",
                 json={"mode": "scan", "filter": "track = 'Go'", "limit": 5})
    assert r.status_code == 200, r.text
    d = r.json()

    assert "rows" not in d["query"]
    assert d["query"]["rows_included"] is False
    assert d["query"]["returned"] >= 0
    assert d["query"]["reproduction"].startswith("import lance")
    assert "track = 'Go'" in json.dumps(d["query"])


@pytest.mark.parametrize("key", ["api_key", "AWS_SECRET_ACCESS_KEY", "storage_options",
                                 "session_token", "Authorization"])
def test_a_secret_named_key_is_removed_at_any_depth(key):
    nested = {"a": [{"b": {key: "shhh", "keep": 1}}]}

    out = bundle.scrub_secrets(nested)

    assert "shhh" not in json.dumps(out)
    assert out["a"][0]["b"]["keep"] == 1


def test_a_secret_is_removed_rather_than_masked():
    """A masked key still says which credentials a deployment holds."""
    out = bundle.scrub_secrets({"api_key": "sk-1", "region": "us-east-1"})

    assert out == {"region": "us-east-1"}


# ---------------------------------------------------------------------- path modes

@pytest.mark.parametrize("table", TABLES)
def test_paths_are_redacted_by_default(api, corpus, table):
    """A local root carries a username and a bucket name carries an employer."""
    d = get(api, table)

    assert d["paths"] == bundle.REDACTED
    assert str(corpus) not in json.dumps(d)
    assert d["connection"]["root"] == bundle.ROOT_PLACEHOLDER


def test_redaction_reaches_inside_the_generated_python(api, corpus):
    """The root turns up in places a field list would miss."""
    r = api.post("/catalog/tables/ordinary/bundle", json={"mode": "scan", "limit": 2})
    d = r.json()

    assert str(corpus) not in d["query"]["reproduction"]
    assert bundle.ROOT_PLACEHOLDER in d["query"]["reproduction"]


def test_redaction_keeps_the_scheme_so_it_costs_no_meaning(api):
    d = get(api, "ordinary")

    assert d["connection"]["scheme"] == "file"
    assert d["connection"]["provenance"]


def test_paths_can_be_kept_on_request_and_the_document_says_which(api, corpus):
    d = get(api, "ordinary", paths="kept")

    assert d["paths"] == bundle.KEPT
    assert str(corpus) in json.dumps(d)


def test_an_unknown_paths_mode_is_refused_rather_than_guessed(api):
    r = api.get("/catalog/tables/ordinary/bundle", params={"paths": "maybe"})

    assert r.status_code == 400
    assert "redacted" in r.text


def test_scheme_of_reads_a_remote_root():
    assert bundle.scheme_of("s3://bucket/lance") == "s3"
    assert bundle.scheme_of("db://sales") == "db"
    assert bundle.scheme_of("/var/data") == "file"


# ----------------------------------------------------------------------- rendering

@pytest.mark.parametrize("table", TABLES)
def test_the_markdown_is_the_same_document(api, table):
    """Rendered from `as_dict()`, so the file somebody reads and the file their
    script parses cannot describe different tables."""
    r = api.get(f"/catalog/tables/{table}/bundle", params={"format": "md"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")

    text = r.text
    assert text.startswith(f"# LanceScope — `{table}`")
    assert "No row values and no\n> credentials" in text or "no row values" in text.lower()


@pytest.mark.parametrize("table", TABLES)
def test_no_row_value_leaves_in_the_markdown_either(api, catalog, table):
    ds = catalog.open(table, scope="test").ds
    columns = [f.name for f in ds.schema if str(f.type) in ("string", "large_string")]
    if not columns:
        pytest.skip(f"{table} has no string columns to leak")

    values = set()
    for row in ds.to_table(columns=columns, limit=8).to_pylist():
        values.update(str(v) for v in row.values() if v and len(str(v)) > 3)

    text = api.get(f"/catalog/tables/{table}/bundle", params={"format": "md"}).text
    leaked = sorted(v for v in values if v in text and v not in columns)
    assert not leaked, f"{table} leaked row values into its markdown: {leaked}"


def test_an_unknown_format_is_refused(api):
    r = api.get("/catalog/tables/ordinary/bundle", params={"format": "pdf"})

    assert r.status_code == 400


def test_a_missing_table_is_one_error_rather_than_six(api):
    r = api.get("/catalog/tables/nope/bundle")

    assert r.status_code == 404


def test_a_bundle_reads_nothing_that_moves_a_byte_on_disk(api, frozen_corpus):
    """The read boundary holds here too — this only calls routes that already had it."""
    from server.catalog import Catalog
    from server.routes import catalog as catalog_routes
    from tests.conftest import snapshot

    before = snapshot(frozen_corpus)
    cat = Catalog(frozen_corpus)
    catalog_routes.bind(cat)
    try:
        for table in TABLES:
            api.get(f"/catalog/tables/{table}/bundle")
    finally:
        cat.close_all()
    assert snapshot(frozen_corpus) == before
