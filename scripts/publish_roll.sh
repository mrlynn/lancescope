#!/usr/bin/env bash
#
# Publish the Roll of the Realm to a HuggingFace dataset repository.
#
# Three uploads in a fixed order, then a check that what landed is readable. The order
# is not a style choice and neither is the split — see docs/guide/howto-roll.md, and
# the comments below, for what each one is avoiding. Every failure this guards against
# was hit for real, and two of them cost a full 600 MB upload before saying so.
#
#   scripts/publish_roll.sh mlynn/roll-of-the-realm-lance
#   scripts/publish_roll.sh mlynn/roll-of-the-realm-lance --yes      # no prompt
#
# The table is expected at data/roll/, as `make roll` writes it. Point DIR elsewhere
# for a differently-sized build.

set -euo pipefail

REPO="${1:-}"
YES="${2:-}"
DIR="${DIR:-data/roll}"
PY="${PY:-.venv/bin/python}"

# uvx rather than an installed `hf`: `pip install huggingface_hub` puts the script
# beside the interpreter that ran the pip, which for a framework Python is not on
# PATH — an install that reports success and leaves `hf` missing.
HF=(uvx --from huggingface_hub hf)

die() { printf '\n  %s\n\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[ -n "$REPO" ] || die "usage: scripts/publish_roll.sh <org>/<repo> [--yes]"
case "$REPO" in */*) ;; *) die "expected <org>/<repo>, got: $REPO" ;; esac

# ---------------------------------------------------------------- what we are sending

[ -d "$DIR" ] || die "no such directory: $DIR — run: make roll"
TABLE=$(find "$DIR" -maxdepth 1 -name '*.lance' -type d 2>/dev/null | head -1)
[ -n "$TABLE" ] || die "no *.lance table in $DIR/ — run: make roll"
NAME=$(basename "$TABLE")

[ -f "$DIR/.gitattributes" ] || die "$DIR/.gitattributes is missing — rebuild with: make roll"
[ -f "$DIR/README.md" ]      || die "$DIR/README.md is missing — rebuild with: make roll"

# A `.gitattributes` inside the table is the mistake that looks like it works: it
# uploads fine and is then ignored, because only the root file is consulted.
[ ! -f "$TABLE/.gitattributes" ] || die \
  "$TABLE/.gitattributes should not exist — only the root one is read. Delete it."

SIZE=$(du -sh "$TABLE" | cut -f1)
FILES=$(find "$TABLE" -type f | wc -l | tr -d ' ')

step "About to publish"
cat <<EOF
  from    $TABLE  ($SIZE, $FILES files)
  to      https://huggingface.co/datasets/$REPO
  as      data/$NAME

  This REPLACES data/$NAME in that repository. Anything there and not here is deleted.
EOF

if [ "$YES" != "--yes" ] && [ "$YES" != "-y" ]; then
  printf '\n  Publish? [y/N] '
  read -r reply
  case "$reply" in [yY]*) ;; *) die "nothing was uploaded." ;; esac
fi

# ------------------------------------------------------------------------- uploading

# 1. The rules, alone and first.
#
# Lance writes five binary file types and the Hub's defaults name none of them, so an
# untracked binary is refused outright — after the whole upload has gone over the wire.
# It cannot ride along in the data commit either: huggingface_hub asks the server which
# files need LFS *before* it commits, so rules arriving in that commit are read one
# commit too late. Its own commit costs a second and makes the next answer correct.
step "1/3  .gitattributes (so the Hub will accept the binaries)"
"${HF[@]}" upload "$REPO" "$DIR/.gitattributes" .gitattributes --repo-type=dataset

# 2. The table.
#
# `--delete "*"` because every rebuild names its data files with fresh UUIDs: without
# it the old fragments stay for ever beside the new manifest, paid for and unreachable.
# Patterns are relative to the destination, so this cannot reach outside data/$NAME —
# and it never removes a .gitattributes, so step 1 survives it.
step "2/3  the table"
"${HF[@]}" upload "$REPO" "$TABLE" "data/$NAME" --repo-type=dataset --delete "*"

# 3. The card. Last, because it carries `viewer: false` and the measured numbers, and
#    because a card describing a table that failed to upload is worse than no card.
step "3/3  the dataset card"
"${HF[@]}" upload "$REPO" "$DIR/README.md" README.md --repo-type=dataset

# ------------------------------------------------------------------------- verifying
#
# Not optional. Every problem this dataset has had — no portraits, a table the console
# could not list, images no browser could draw — was invisible from the upload log and
# obvious from thirty seconds of reading it back.

step "Reading it back over hf://"
REPO="$REPO" NAME="$NAME" "$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, ".")
import lance
from server.hf import list_tables
from server.query import sniff_media_type

repo, name = os.environ["REPO"], os.environ["NAME"]
root = f"hf://datasets/{repo}"
uri = f"{root}/data/{name}"
ok = True

def check(label, good, detail=""):
    global ok
    ok = ok and good
    print(f"  {'ok  ' if good else 'FAIL'}  {label}{'  ' + detail if detail else ''}")

tables = list_tables(root)
check("the console lists it", tables != [], f"{tables}")

ds = lance.dataset(uri)
rows = ds.count_rows()
check("it opens and has rows", rows > 0, f"{rows:,} rows · v{ds.version}")

blobs = [f.name for f in ds.schema if "blob" in str(f.type)]
check("a blob column survived the trip", bool(blobs), f"{blobs}")

# What a row browse costs, which is the whole claim. Fresh handle: the counter is a
# destructive delta, so anything read before this point would be billed to it.
scalars = [f.name for f in ds.schema
           if f.name not in blobs and not str(f.type).startswith("fixed_size_list")]
d2 = lance.dataset(uri); d2.io_stats_incremental()
d2.to_table(columns=scalars)
cost = d2.io_stats_incremental().read_bytes
check("every scalar row is cheap", cost < 5 * 1024 ** 2,
      f"{cost / 1024:,.0f} KB for all {rows:,} rows")

for col in blobs:
    b = ds.take_blobs(col, indices=[0])[0]
    if b is None:
        check(f"{col}: row 0 has one", False, "null — is the column sparse at the front?")
        continue
    head = b.read(64)
    mime = sniff_media_type(head)
    check(f"{col}: row 0 is {b.size() / 1024 ** 2:.1f} MB", True)
    check(f"{col}: a browser can draw it", mime is not None,
          mime or f"unrecognised: {head[:8]!r}")

print(f"\n  https://huggingface.co/datasets/{repo}")
sys.exit(0 if ok else 1)
PYEOF

step "Published"
