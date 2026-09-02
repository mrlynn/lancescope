"""Turning a directory of media into a Lance table.

**The rule this package exists to keep: nothing under `ingest.core` imports torch,
av, or lancedb at module scope.** The packaged desktop app ships pylance and no ML
stack, and an import that fires at module load would turn "this build cannot decode
video" into "this build will not start". Heavy backends are resolved through
registries and imported inside the function that needs them, so a missing dependency
becomes a capability the plan can report rather than a traceback.

`lancedb` is absent for a different reason: it is not needed. FINDINGS.md records the
measurement — pylance builds the inverted, IVF_PQ and BTREE indices ingest wants, and
the read path recognises all three.

The order of operations is deliberate. Everything that can be known is established
before a byte is written: which files are here, which of them this build can decode,
whether the embedder answers and what dimension it really returns. A run that is
going to fail should fail in the plan, where it costs nothing.
"""
