"""The read commands: `lancescope findings` and `lancescope run-config`.

These are the first CLI subcommands to read a table — `ingest.core.capability` has
imported `server.catalog` all along — and the first place this project has an exit
code anyone will script against. So the tests here are
mostly about the codes and about which stream things land on — a gate whose stdout
carries commentary is a gate nobody can redirect, and a gate that returns the same
number for "this table has a warning" and "we could not check this table" is one
nobody should trust.
"""

import json

import pytest
import yaml

from ingest.cli import main as cli_main
from server.intel import findings as intel_findings


@pytest.fixture
def rooted(corpus, monkeypatch, tmp_path):
    """The CLI pointed at the fixture corpus the way a deployment would be.

    `LANCE_ROOT` plus an isolated config, rather than injecting a catalog: root
    resolution happens per call, and a test that bypassed it would not be exercising
    the ladder the command actually climbs.
    """
    from server import headless

    monkeypatch.setenv("LANCESCOPE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LANCE_ROOT", str(corpus))
    headless.reset()
    yield corpus
    headless.reset()


def test_findings_reports_without_being_asked_to_judge(rooted, capsys):
    """No `--fail-on` is exit 0 whatever it finds.

    A subcommand that broke somebody's build the first time they ran it is a
    subcommand they delete from the pipeline.
    """
    assert cli_main(["findings", "vectors"]) == 0
    assert "vector-column-unindexed" in capsys.readouterr().out


def test_fail_on_warn_exits_non_zero_only_when_a_warning_is_outstanding(rooted, capsys):
    assert cli_main(["findings", "vectors", "--fail-on", "warn"]) == 1
    capsys.readouterr()
    assert cli_main(["findings", "ordinary", "--fail-on", "warn"]) == 0


def test_fail_on_note_is_stricter_than_fail_on_warn(rooted, capsys):
    """`note` means "anything at all", which is a different gate, not a louder one."""
    warn = cli_main(["findings", "blobs", "--fail-on", "warn"])
    capsys.readouterr()
    note = cli_main(["findings", "blobs", "--fail-on", "note"])
    capsys.readouterr()

    assert note >= warn


def test_an_incomplete_sweep_fails_the_gate_with_its_own_code(rooted, capsys, monkeypatch):
    """The test this gate exists for.

    A rule that crashed leaves a table neither clean nor condemned, and a CI job has
    to be able to tell that apart from both. Exit 3 is how, and stderr names the rule
    so somebody can go and fix it.
    """
    def explodes(_facts):
        raise RuntimeError("nope")

    monkeypatch.setattr(intel_findings, "RULES", (*intel_findings.RULES, explodes))
    monkeypatch.setattr(intel_findings.log, "exception", lambda *a, **k: None)

    assert cli_main(["findings", "ordinary", "--fail-on", "warn"]) == 3
    err = capsys.readouterr().err
    assert "incomplete" in err
    assert "explodes" in err


def test_an_incomplete_sweep_is_still_only_reported_when_nothing_was_asked(rooted, capsys,
                                                                          monkeypatch):
    """Without `--fail-on` the command is a report, and a report does not fail."""
    def explodes(_facts):
        raise RuntimeError("nope")

    monkeypatch.setattr(intel_findings, "RULES", (*intel_findings.RULES, explodes))
    monkeypatch.setattr(intel_findings.log, "exception", lambda *a, **k: None)

    assert cli_main(["findings", "ordinary"]) == 0
    assert "incomplete" in capsys.readouterr().err


def test_a_table_that_is_not_there_is_a_usage_error_not_a_failed_gate(rooted, capsys):
    """A renamed table and a table with a warning must not exit the same way."""
    assert cli_main(["findings", "nope", "--fail-on", "warn"]) == 2
    err = capsys.readouterr().err
    assert "no table named 'nope'" in err
    assert "ordinary" in err, "a short root should say what it does hold"


def test_a_facet_nobody_defined_is_refused_before_anything_is_read(rooted, capsys):
    assert cli_main(["findings", "ordinary", "--facet", "trainng"]) == 2
    assert "training" in capsys.readouterr().err


def test_no_configured_root_is_a_usage_error_that_says_how_to_fix_it(
        settings_file, monkeypatch, capsys):
    """`settings_file` deletes LANCE_ROOT, which is exactly the state under test."""
    from server import headless

    headless.reset()
    monkeypatch.setattr(headless.cfg, "demo_root", lambda: None)

    assert cli_main(["findings", "ordinary"]) == 2
    err = capsys.readouterr().err
    assert "LANCE_ROOT" in err, "the refusal has to say how to fix itself"


def test_the_cli_and_the_route_agree_about_findings(rooted, api, capsys):
    """The contract `tests/test_ingest_api.py` set for scan, kept for findings."""
    assert cli_main(["findings", "vectors", "--json"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    from_http = api.get("/catalog/tables/vectors/findings").json()

    assert from_cli["findings"] == from_http["findings"]
    assert from_cli["summary"] == from_http["summary"]


def test_run_config_writes_only_the_artifact_to_stdout(rooted, capsys):
    """`lancescope run-config t > dataset.yaml` has to produce a parseable file.

    Everything a person would want to know about the artifact goes to stderr for
    exactly this reason: stdout is the file.
    """
    assert cli_main(["run-config", "vectors"]) == 0
    out = capsys.readouterr()

    parsed = yaml.safe_load(out.out)
    assert parsed["lancescope_run_config"] == 1
    assert parsed["dataset"]["name"] == "vectors"
    assert "warning" in out.err, "an outstanding warn should be mentioned, off stdout"


def test_run_config_names_the_columns_it_was_asked_for(rooted, capsys):
    assert cli_main(["run-config", "thumbnails", "--columns", "item_id,kind"]) == 0
    parsed = yaml.safe_load(capsys.readouterr().out)

    assert parsed["columns"] == ["item_id", "kind"]
    assert parsed["read"]["basis"] == "file-statistics"


def test_run_config_refuses_a_column_the_table_does_not_have(rooted, capsys):
    """Quietly dropping it would emit a byte figure for a projection nobody asked for."""
    assert cli_main(["run-config", "ordinary", "--columns", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_the_cli_and_the_route_agree_about_the_run_config(rooted, api, capsys):
    assert cli_main(["run-config", "ordinary", "--json"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    from_http = api.get("/catalog/tables/ordinary/run-config").json()

    # `generated_at` is a timestamp and the two calls are not simultaneous.
    for body in (from_cli, from_http):
        body["run_config"].pop("generated_at")
        body.pop("run_config_yaml")
    assert from_cli["run_config"] == from_http["run_config"]


def test_building_the_parser_does_not_load_the_read_surface(rooted):
    """The deferred-import rule, checked on what these commands actually added.

    `lancescope --help` already pays for lance, because `ingest.core.plan` imports it
    and `ingest.core.capability` imports `server.catalog` — both from long before
    these subcommands existed. What must not join that list is everything `findings`
    and `run-config` reach for: fastapi, and the modules that weigh a table.
    """
    import subprocess
    import sys

    deferred = ("fastapi", "server.headless", "server.estimate",
                "server.intel.runconfig", "server.routes.catalog")
    code = (
        "import sys; from ingest.cli import build_parser; build_parser();"
        f"loaded=[m for m in {deferred!r} if m in sys.modules];"
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert done.returncode == 0, f"loaded eagerly: {done.stdout.strip()}"


def test_cost_lists_the_columns_heaviest_first(rooted, capsys):
    """The shape is the answer: one column is usually most of a pass."""
    assert cli_main(["cost", "thumbnails"]) == 0
    out = capsys.readouterr().out

    assert "thumb" in out
    assert "a full pass" in out
    assert "on the meter above" in out, "the off-meter footer cost has to be stated"


def test_cost_quotes_the_floor_when_the_floor_is_what_a_pass_costs(rooted, capsys):
    """On small files, reporting the column sum alone would understate it hugely."""
    assert cli_main(["cost", "blobs"]) == 0
    # Collapsed, because the caveats are wrapped for a terminal and a phrase can
    # straddle two lines.
    out = " ".join(capsys.readouterr().out.split())

    assert "per-file overhead" in out or "a full pass over these" in out
    assert "reads a small file whole" in out


def test_cost_refuses_a_column_the_table_does_not_have(rooted, capsys):
    assert cli_main(["cost", "ordinary", "--columns", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_cost_and_the_route_agree(rooted, api, capsys):
    assert cli_main(["cost", "ordinary", "--json"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    from_http = api.get("/catalog/tables/ordinary/estimate").json()

    assert from_cli["columns"] == from_http["columns"]
    assert from_cli["bytes"] == from_http["bytes"]


# ------------------------------------------------------------------------- open

def test_open_resolves_a_dot_lance_path_to_the_directory_above_it(corpus):
    """`LANCE_ROOT` wants the parent, and tab completion gives you the table."""
    from ingest.cli import resolve_open_target

    t = resolve_open_target(str(corpus / "ordinary.lance"))

    assert t.root == corpus
    assert t.table == "ordinary"
    assert not t.error


def test_open_resolves_a_path_inside_a_table_to_the_table_containing_it(corpus):
    """`.../ordinary.lance/data` is the other thing completion hands you."""
    from ingest.cli import resolve_open_target

    t = resolve_open_target(str(corpus / "ordinary.lance" / "data"))

    assert t.root == corpus
    assert t.table == "ordinary"


def test_open_takes_a_directory_of_tables_as_the_root(corpus):
    from ingest.cli import resolve_open_target

    t = resolve_open_target(str(corpus))

    assert t.root == corpus
    assert t.table is None


def test_open_refuses_a_directory_with_no_tables_under_it(tmp_path):
    from ingest.cli import resolve_open_target

    t = resolve_open_target(str(tmp_path))

    assert t.error and "no .lance tables" in t.error


def test_open_refuses_a_path_that_is_not_there(tmp_path):
    from ingest.cli import resolve_open_target

    assert "does not exist" in resolve_open_target(str(tmp_path / "nope")).error


def test_open_with_no_path_leaves_the_root_alone(corpus):
    """Pointed at whatever the console is already pointed at."""
    from ingest.cli import resolve_open_target

    t = resolve_open_target(None)

    assert t.root is None and t.table is None and not t.error
