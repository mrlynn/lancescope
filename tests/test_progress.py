"""Startup stages, and the fact that nobody else has to hear them.

The desktop shell mirrors these into a window while the server is still importing
Lance. The contract is small and worth pinning: the shape of the line, that it is
silent unless asked, and that saying something can never be the reason a server
fails to start.
"""

from __future__ import annotations

from server import progress


def test_nothing_is_said_unless_something_asked(capsys, monkeypatch):
    """Every dev run, every test and every container imports this. A line meant for
    a parent process that is not there is noise in all three."""
    monkeypatch.delenv(progress.ENABLED, raising=False)
    progress.stage("loading", "Loading Lance")
    assert capsys.readouterr().out == ""
    assert progress.enabled() is False


def test_arming_turns_it_on_and_the_line_has_both_halves(capsys, monkeypatch):
    """`<id>|<text>`: the id is for a reader that wants to act on a particular
    stage, the text is what it shows. A shell that does not recognise the id can
    still print the sentence, which is what keeps an old shell working against a
    new server."""
    monkeypatch.delenv(progress.ENABLED, raising=False)
    progress.arm()
    progress.stage("catalog", "Opening the database")
    assert capsys.readouterr().out == "LANCESCOPE_STAGE=catalog|Opening the database\n"


def test_a_pipe_nobody_is_reading_is_not_a_failure_to_boot(monkeypatch):
    """This is decoration on a startup path. A parent that exited between the spawn
    and the first stage must not take the server down with it."""
    monkeypatch.setenv(progress.ENABLED, "1")

    def closed(*_args, **_kwargs):
        raise OSError(32, "Broken pipe")

    monkeypatch.setattr("builtins.print", closed)
    progress.stage("loading", "Loading Lance")  # does not raise


def test_text_carrying_a_pipe_still_leaves_the_id_readable(capsys, monkeypatch):
    """Split on the first separator, so a sentence may contain one. Asserted because
    the shell splits it back and would otherwise show half a sentence."""
    monkeypatch.setenv(progress.ENABLED, "1")
    progress.stage("catalog", "Opening a|b")
    line = capsys.readouterr().out.strip()
    ident, _, text = line.removeprefix("LANCESCOPE_STAGE=").partition("|")
    assert (ident, text) == ("catalog", "Opening a|b")
