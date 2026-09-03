"""The run config: what it pins, what it refuses to invent, and that the YAML is real.

The load-bearing test here is the round trip. `to_yaml` is hand-rolled because pyyaml
is not a declared dependency, and a hand-rolled emitter is only safe while something
parses its output with a real library and compares. That is
`test_the_yaml_and_the_object_are_the_same_thing`; if it goes, the emitter goes with
it.
"""

import lance
import pyarrow as pa
import pytest
import yaml

from server.intel import findings as intel_findings
from server.intel import runconfig
from tests.conftest import snapshot


def build(catalog, table, **kw):
    return runconfig.build(catalog.open(table, scope="test"), **kw)


def test_a_run_config_pins_the_version_that_was_actually_read(catalog):
    """A checkpoint that cannot name its version is one nobody can reproduce."""
    cfg = build(catalog, "versioned")

    d = cfg.as_dict()
    assert d["dataset"]["version"] == d["dataset"]["latest_version"]
    assert d["dataset"]["name"] == "versioned"
    assert d["dataset"]["uri"].endswith("versioned.lance")


def test_a_run_config_carries_its_schema_version_as_the_first_key(catalog):
    """This file gets committed elsewhere and parsed by code that is not ours."""
    d = build(catalog, "ordinary").as_dict()

    assert next(iter(d)) == "lancescope_run_config"
    assert d["lancescope_run_config"] == runconfig.SCHEMA_VERSION


def test_the_columns_a_run_reads_decide_the_bytes_it_pins(catalog):
    """The whole point of naming columns: the number moves, and says why it moved."""
    everything = build(catalog, "thumbnails").as_dict()["read"]
    one = build(catalog, "thumbnails", columns=["item_id"]).as_dict()["read"]

    assert one["column_weight_bytes"] < everything["column_weight_bytes"]
    assert one["basis"] == "file-statistics"


def test_a_column_a_table_does_not_have_is_refused_rather_than_quietly_dropped(catalog):
    """Falling back to the whole table would describe a projection nobody asked for."""
    with pytest.raises(KeyError):
        build(catalog, "thumbnails", columns=["nope"])


def test_a_weighed_figure_is_never_labelled_as_a_measured_one(catalog):
    """`basis` is what stops one number meaning two things on different days."""
    for columns in (None, ["id"]):
        read = build(catalog, "ordinary", columns=columns).as_dict()["read"]
        assert read["basis"] in runconfig.BASES
        assert read["basis"] != "unavailable"


def test_a_blob_tables_side_files_are_reported_beside_the_columns_and_not_inside_them(catalog):
    """Adding them into one total is the mistake the Training tab already refuses."""
    read = build(catalog, "blobs").as_dict()["read"]

    assert read["blob_columns"], "the blobs fixture should have a blob column"
    assert read["blob_bytes"] and read["blob_bytes"] > read["column_weight_bytes"]


def test_the_findings_in_a_run_config_are_the_ones_a_run_pays_for(catalog):
    """Default facet is training, and the artifact says so rather than implying it."""
    h = catalog.open("vectors", scope="test")
    cfg = runconfig.build(h)

    d = cfg.as_dict()["findings"]
    assert d["facet"] == "training"
    expected = {f.id for f in intel_findings.findings_for(h, facet="training")}
    assert {f["id"] for f in d["outstanding"]} == expected


def test_a_run_config_records_findings_without_refusing_to_generate_over_them(catalog):
    """Provenance, not a gate. `lancescope findings --fail-on` is what blocks."""
    d = build(catalog, "vectors").as_dict()["findings"]

    assert any(f["severity"] == "warn" for f in d["outstanding"])
    assert d["analysis_complete"] is True


def test_an_incomplete_sweep_is_recorded_rather_than_hidden(catalog, monkeypatch):
    """"Clean" and "could not be checked" must not arrive looking the same."""
    def explodes(_facts):
        raise RuntimeError("nope")

    monkeypatch.setattr(intel_findings, "RULES", (*intel_findings.RULES, explodes))
    monkeypatch.setattr(intel_findings.log, "exception", lambda *a, **k: None)

    d = build(catalog, "ordinary").as_dict()["findings"]

    assert d["analysis_complete"] is False
    assert d["failed_rules"] and d["failed_rules"][0]["rule"] == "explodes"


def test_a_run_config_invents_no_training_parameters(catalog):
    """This server has never watched a training run and must not imply that it has."""
    text = runconfig.to_yaml(build(catalog, "ordinary"))

    for invented in ("batch_size", "learning_rate", "epochs:", "device", "optimizer"):
        assert invented not in text


def test_the_analysis_is_swept_once_when_the_caller_already_has_one(catalog):
    """A panel and the artifact beside it must not disagree about the same table."""
    h = catalog.open("vectors", scope="test")
    analysis = intel_findings.analyse(h, facet="training")

    calls = []
    original = intel_findings.analyse

    def counted(*a, **k):
        calls.append(1)
        return original(*a, **k)

    cfg = runconfig.build(h, analysis=analysis)

    assert not calls
    assert len(cfg.as_dict()["findings"]["outstanding"]) == len(analysis.findings)


def test_the_loader_ceiling_is_the_fragment_count(catalog):
    """The number the Training tab reports, in the file a run actually keeps."""
    h = catalog.open("ordinary", scope="test")
    cfg = runconfig.build(h)

    assert cfg.as_dict()["read"]["loader_workers_max"] == len(h.ds.get_fragments())


@pytest.mark.parametrize("table", ["ordinary", "vectors", "blobs", "versioned", "evolved"])
def test_the_yaml_and_the_object_are_the_same_thing(catalog, table):
    """The one that makes a hand-rolled emitter safe to have."""
    cfg = build(catalog, table)

    assert yaml.safe_load(runconfig.to_yaml(cfg)) == cfg.as_dict()


def test_the_yaml_survives_a_table_name_that_needs_quoting(tmp_path):
    """A colon in a value is where a naive emitter produces a file nobody can parse."""
    from server.catalog import Catalog

    uri = str(tmp_path / "odd: name.lance")
    lance.write_dataset(pa.table({"id": pa.array(range(8))}), uri)

    cfg = runconfig.build(Catalog(tmp_path).open("odd: name", scope="test"))

    assert yaml.safe_load(runconfig.to_yaml(cfg)) == cfg.as_dict()


def test_generating_a_run_config_moves_nothing_on_disk(frozen_corpus):
    """New file-opening code, on a server whose first promise is that it only reads."""
    from server.catalog import Catalog

    before = snapshot(frozen_corpus)
    cat = Catalog(frozen_corpus)
    for table in ("ordinary", "blobs", "vectors"):
        runconfig.to_yaml(runconfig.build(cat.open(table, scope="test")))
    cat.close_all()

    assert snapshot(frozen_corpus) == before


# ---------------------------------------------------------------------- the route

def test_the_run_config_route_carries_the_same_findings_as_the_findings_route(api):
    """One sweep, two consumers. A panel and the artifact beside it must agree."""
    rc = api.get("/catalog/tables/vectors/run-config").json()
    f = api.get("/catalog/tables/vectors/findings?facet=training").json()

    assert [x["id"] for x in rc["findings"]] == [x["id"] for x in f["findings"]]
    assert rc["summary"] == f["summary"]
    assert rc["run_config"]["findings"]["facet"] == "training"


def test_the_route_yaml_and_the_route_object_are_the_same_thing(api):
    body = api.get("/catalog/tables/ordinary/run-config").json()

    assert yaml.safe_load(body["run_config_yaml"]) == body["run_config"]


def test_asking_for_a_column_that_is_not_there_is_refused_rather_than_dropped(api):
    r = api.get("/catalog/tables/ordinary/run-config?columns=nope")

    assert r.status_code == 400
    assert "nope" in r.json()["detail"]


def test_naming_columns_weighs_the_projection_rather_than_the_table(api):
    whole = api.get("/catalog/tables/thumbnails/run-config").json()
    part = api.get("/catalog/tables/thumbnails/run-config?columns=item_id").json()

    assert (part["run_config"]["read"]["column_weight_bytes"]
            < whole["run_config"]["read"]["column_weight_bytes"])
    assert part["columns"] == ["item_id"]


def test_a_facet_nobody_defined_is_a_400_rather_than_an_empty_artifact(api):
    r = api.get("/catalog/tables/ordinary/run-config?facet=trainng")

    assert r.status_code == 400
    assert "training" in r.json()["detail"]


def test_the_run_config_route_is_not_swallowed_by_the_catch_all_table_route(api):
    """`/tables/{name:path}` would happily match `ordinary/run-config` as a name."""
    body = api.get("/catalog/tables/ordinary/run-config").json()

    assert body["name"] == "ordinary"
    assert "run_config" in body
