/** Reading orders through the guide, one per kind of reader.
 *
 *  The guide is arranged by Diátaxis — something to follow, something to do,
 *  something to look up, something to understand — which is the right way to
 *  *organise* documentation and a poor way to *start* reading it. Someone whose job
 *  is training runs and someone whose job is keeping a service up need the same
 *  twenty-one pages in two different orders, and neither of them wants to begin at
 *  the top of a section called Reference.
 *
 *  So this is a second axis over the same pages. Nothing here duplicates a page:
 *  a path is an order plus a sentence per step saying why *you* are being sent
 *  there. Where a path needs something the guide does not have, the answer is a new
 *  page in `docs/guide/`, not a paragraph hidden in this file.
 *
 *  Why a manifest rather than front matter: a page belongs to several paths at
 *  different positions and for different reasons, and `paths: build:3, run:1` in a
 *  YAML header is a data structure pretending to be a string. `tests/test_paths.py`
 *  fails if any slug here does not exist, so this cannot rot quietly.
 */

export type Step = {
  /** A page in `docs/guide/`, without the `.md`. */
  slug: string;
  /** Why this reader is being sent to this page. Not a summary of the page — the
   *  page has one of those. This is the sentence that makes it worth the detour. */
  why: string;
};

export type Path = {
  id: string;
  /** What you do, not what you are called. Job titles are contested and overlap;
   *  "I am here to get a model trained" is a thing someone can recognise about
   *  themselves in one read. */
  label: string;
  /** The roles this path was written for, said plainly so nobody has to guess
   *  whether it means them. */
  roles: string;
  /** One line on the picker, under the label. */
  blurb: string;
  steps: Step[];
};

export const PATHS: Path[] = [
  {
    id: "build-models",
    label: "Build better models",
    roles: "AI & ML engineers · ML and data scientists",
    blurb:
      "What a training run costs before you start it, and which properties of a "
      + "table decide how long an epoch takes.",
    steps: [
      {
        slug: "path-build-models",
        why: "What Lance actually is, and the four properties of a table that a "
          + "training run pays for.",
      },
      {
        slug: "explain-cost",
        why: "Every screen in this console reports bytes rather than milliseconds. "
          + "This is the argument for why, and it is the argument your epoch times "
          + "are made of.",
      },
      {
        slug: "explain-blobs",
        why: "The reason a table of video can be described for kilobytes. Training "
          + "on the frames and training on the features are two different reads of "
          + "the same table, and they differ by four orders of magnitude.",
      },
      {
        slug: "howto-connect",
        why: "Point it at your own tables — or open one of LanceDB's published "
          + "datasets over hf:// without downloading it, which is the cheapest way "
          + "to see whether any of this is true.",
      },
      {
        slug: "reference-findings",
        why: "The rules that read your layout. Three of them are about training "
          + "specifically: how few workers your fragments can feed, what a straggler "
          + "fragment costs an epoch, and what share of the table is embeddings.",
      },
      {
        slug: "howto-diagnose",
        why: "A retrieval eval that scans every vector is a slow eval with no error "
          + "message. This is how to see the access path before you spend an hour on "
          + "it.",
      },
      {
        slug: "explain-evidence",
        why: "Why nothing here tells you to compact a table. The findings carry the "
          + "numbers they were computed from, and some of them are reasons not to act.",
      },
    ],
  },
  {
    id: "run-production",
    label: "Run it in production",
    roles: "MLOps · data engineers · infrastructure architects",
    blurb:
      "Which reader opens your data, what a deployment reads, and how to keep a "
      + "dataset legible to the people who did not write it.",
    steps: [
      {
        slug: "path-run-production",
        why: "The operational shape of Lance: what a version is, what a fragment "
          + "costs, and which of it you are on the hook for.",
      },
      {
        slug: "reference-versions",
        why: "A Lance reader is not universal. This is the measured matrix of which "
          + "versions open what, and the floor it establishes.",
      },
      {
        slug: "howto-container",
        why: "One image per reader, because the tag has to name the version that can "
          + "open your data. Read-only against a bind-mounted dataset.",
      },
      {
        slug: "reference-configuration",
        why: "Every knob, and the order in which they win. Four things can set the "
          + "root and exactly one of them does.",
      },
      {
        slug: "explain-cost",
        why: "The unit a capacity plan is made of. Bytes and IOs are the numbers "
          + "that stay true when the hardware changes underneath them.",
      },
      {
        slug: "reference-findings",
        why: "Tombstone debt, small-file counts, version churn — the layout problems "
          + "that show up as a slow scan months after the write that caused them.",
      },
      {
        slug: "howto-desktop",
        why: "The other way to hand this to somebody: a signed app that carries its "
          + "own server and needs nothing installed.",
      },
    ],
  },
  {
    id: "build-on-it",
    label: "Build on it",
    roles: "Full-stack AI & LLM application developers",
    blurb:
      "Query a multimodal table from your own code, and give an agent a read "
      + "surface that cannot pull a gigabyte into its context.",
    steps: [
      {
        slug: "path-build-on-it",
        why: "One table holding text, vectors and the media itself — and what that "
          + "means for the code you write against it.",
      },
      {
        slug: "reference-query",
        why: "Scan, full-text, vector and hybrid, with what each one needs and what "
          + "each one costs. A mode a table cannot answer says so.",
      },
      {
        slug: "reference-http-api",
        why: "Every route, and the rule that no parameter of any of them can "
          + "materialise a blob column.",
      },
      {
        slug: "explain-blobs",
        why: "Why a row of video is cheap to list and expensive to open, and how to "
          + "stay on the cheap side of that until you mean not to.",
      },
      {
        slug: "howto-agents",
        why: "The same read surface as MCP tools. An agent gets the catalog and "
          + "cannot accidentally read 2.65 GB of video into its context window.",
      },
      {
        slug: "howto-intelligence",
        why: "The optional language layer: plain-English filters and table "
          + "summaries, priced per call, running against Claude or a local model.",
      },
      {
        slug: "explain-caching",
        why: "What that layer costs and what it caches, because a feature that "
          + "spends money should say how much.",
      },
    ],
  },
];

export const pathById = (id: string | null | undefined): Path | undefined =>
  PATHS.find((p) => p.id === id);

/** Where a slug sits in a path, 1-based, or 0 if it is not on it. */
export function positionIn(path: Path, slug: string): number {
  return path.steps.findIndex((s) => s.slug === slug) + 1;
}
