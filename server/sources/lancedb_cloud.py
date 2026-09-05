"""LanceDB Cloud and Enterprise, over `db://`.

Thin on purpose. Everything about listing, opening, capabilities and error wording
lives in `NamespaceSource`; what is here is how to build the client — an endpoint
from the database name and region, and an API key. LanceDB Cloud is LanceDB's
commercial product, and this reads it the way it reads a directory: the operator
brings their own database and their own key.

**`RestNamespace`, not hand-rolled HTTP.** `server/hf.py` is hand-rolled because the
alternative was carrying `huggingface_hub` for one endpoint. The ratio is inverted
here: the client already ships inside `pylance`, it is Rust-backed, and it is the
same code path `lance.dataset(namespace_client=…)` uses to resolve and open — so a
table that lists is a table that opens. Writing our own would be a second, divergent
implementation of a fifty-operation specification, to avoid a dependency that is
already installed.

**No `lancedb` import.** `tests/test_write_quarantine.py` forbids one under
`server/`, because that package is absent from the packaged desktop app's dependency
group. `lance_namespace` is a different package and is present wherever `pylance` is,
so the quarantine stands unchanged rather than being negotiated with.

**Known gap, not yet fixable here.** A namespace may report `managed_versioning`,
meaning version history lives in the service rather than in Lance's `_versions/`.
`GET /catalog/tables/{name}/versions` reads `ds.versions()` and would then show a
short history that looks complete. `ListTableVersions` is the operation that answers
properly — it works, and it is exercised in the tests — but whether LanceDB Cloud
actually sets that flag cannot be checked from here without an account, and building
a fallback for a condition nobody has observed is the kind of guess this console does
not make. It is named here and in the spec so the first person with credentials knows
exactly what to look at.
"""

from __future__ import annotations

from server.sources.namespace import NamespaceSource, NamespaceUnavailable

PREFIX = "db://"

# Where LanceDB Cloud lives. `LANCEDB_HOST_OVERRIDE` replaces the whole endpoint,
# which is how LanceDB Enterprise and a local test service are reached.
ENDPOINT = "https://{db}.{region}.api.lancedb.com"
DEFAULT_REGION = "us-east-1"

API_KEY = "LANCEDB_API_KEY"
REGION = "LANCEDB_REGION"
HOST_OVERRIDE = "LANCEDB_HOST_OVERRIDE"

# Deliberately resolved rather than exported. `server/credentials.py::EXPORTED` is
# for names a Rust library reads out of the environment on its own; these are read
# here, and putting them in the environment would widen their reach for nothing.
NO_KEY = (
    f"No {API_KEY} is set, so this database cannot be listed. Add it to the "
    f"environment or to `.cred`. {REGION} sets the region if the database is not in "
    f"{DEFAULT_REGION}, and {HOST_OVERRIDE} replaces the endpoint entirely for "
    f"LanceDB Enterprise."
)


class CloudSource(NamespaceSource):
    scheme = "db"

    def label(self) -> str:
        return "LanceDB service"

    def endpoint(self, root: str) -> tuple[str, str, str]:
        """The endpoint, the key and the database name for this root."""
        from server import credentials

        database = str(root)[len(PREFIX):].strip("/").split("/")[0]
        if not database:
            raise NamespaceUnavailable(
                f"`{root}` names no database. A LanceDB root looks like "
                f"`db://my-database`.")
        key, _ = credentials.resolve(API_KEY)
        if not key:
            raise NamespaceUnavailable(NO_KEY)
        host, _ = credentials.resolve(HOST_OVERRIDE)
        region, _ = credentials.resolve(REGION)
        endpoint = host or ENDPOINT.format(db=database,
                                           region=region or DEFAULT_REGION)
        return endpoint, key, database

    def namespace(self, root: str):
        try:
            from lance.namespace import RestNamespace
        except ImportError as e:  # pragma: no cover - pylance always brings it
            raise NamespaceUnavailable(
                f"this build cannot reach a LanceDB service: {e}") from e

        endpoint, key, database = self.endpoint(root)
        try:
            # `header.*` properties become HTTP headers on every request.
            return RestNamespace(uri=endpoint, **{
                "header.x-api-key": key,
                "header.x-lancedb-database": database,
            })
        except Exception as e:  # noqa: BLE001
            raise NamespaceUnavailable(
                f"could not reach the LanceDB service at {endpoint}: {e}") from e
