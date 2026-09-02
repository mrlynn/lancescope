"""The write half of LanceScope.

`server/` reads Lance datasets and never writes one. Everything that creates a table
lives here, and that split is enforced by `tests/test_write_quarantine.py` rather
than by convention.

Two things share this package and should not be confused. The modules directly under
it — `download.py`, `prepare.py`, `embed.py`, `build_lance.py` — are the FOSDEM
conference demo's pipeline, hardcoded to that corpus and its two schemas.
`ingest.core` is the general one: it turns a directory of someone's own media into a
Lance table, and it is what the console and the `lancescope` CLI both call.
"""
