import type { SpriteId } from "./pixels";

/** The Squire's Tour — seven chapters, in the order this repo already teaches in.
 *
 *  Every previous version of this game was an assessment. This one is the lesson, and it
 *  assumes nothing: not what a column is, not what an index is, not what a vector is. Each
 *  chapter introduces exactly one idea, shows it, lets you do it once, and states the fact
 *  it earned. Nothing is asked before it has been shown.
 *
 *  The beats are DEMO_SCRIPT.md's, the numbers are the docs' own, and every figure carries
 *  the file it came from so a check can prove the game has not drifted away from the product
 *  it is teaching. If a number here stops matching the repo, that check fails rather than
 *  the tour quietly teaching something untrue.
 */

/** A word the tour has defined, added to the glossary panel as it goes. This project has no
 *  glossary anywhere — the docs open at "you already work with Lance" — so building one is
 *  half of what the tour is for, and watching it fill is how progress reads. */
/** The way out of the story.
 *
 *  An allegory teaches intuition and leaves the reader unable to map it back. So every
 *  chapter carries its own translation: the phrases in the fiction against the things they
 *  actually are, the truth said plainly, and the pages in this project's own guide where it
 *  is written down properly. Reachable from any chapter, at any point in it.
 */
export type Plain = {
  glosses: { fiction: string; real: string }[];
  says: string;
  /** Slugs under docs/guide/. Checked against the directory, not trusted. */
  links: { slug: string; label: string }[];
};

export type Term = {
  word: string;
  means: string;
  /** The console tab this word is visible on, and what to look at when you get there.
   *  The egg is hidden inside LanceScope, so the last thing it can usefully do is put
   *  someone in the tab where what they just learned is showing, on their own table. */
  door: { tab: string; label: string; look: string };
};

/** The establishing shot for a chapter. Chapter II's whole argument is four buildings
 *  against one, so the band has to be able to say "versus" rather than just line things up. */
export type Art =
  | { kind: "row"; sprites: SpriteId[]; note?: string }
  | { kind: "versus"; left: SpriteId[]; right: SpriteId[]; note?: string };

export type Step = {
  label: string;
  /** What the scale moves by. Zero for anything that costs nothing to ask. */
  cost: number;
  says: string;
  /** A step that teaches by failing: it reveals the others rather than moving on. */
  reveals?: boolean;
  /** Shields are drawn when the arms column is paid for — the one place the heraldry earns
   *  its keep, because 120 bytes a knight is what buys you a picture of the arms. */
  showsArms?: boolean;
};

export type Chapter = {
  n: string;
  title: string;
  art: Art;
  plain: Plain;
  /** Two or three sentences that ARE the explanation. Not flavour on top of one. */
  scene: string[];
  /** How many steps must be taken before the chapter will end. Usually one. */
  needs: number;
  steps: Step[];
  fact: string;
  term: Term;
  /** Figures quoted from the repo, with where they live. Checked, not trusted. */
  cites: { figure: string; where: string }[];
};

const KB = 1024;
const MB = 1024 * 1024;
const KNIGHTS = 1114;      // the moments table
const CRATES = 162;        // the segments table

export const CHAPTERS: Chapter[] = [
  {
    n: "I",
    title: "The vault",
    art: { kind: "row", sprites: ["castle", "scroll", "scroll", "scroll"], note: "one vault, many ledgers" },
    plain: {
      glosses: [
        { fiction: "the Crown", real: "Lance — the storage format underneath LanceDB" },
        { fiction: "the vault", real: "a table, which on disk is a directory of files" },
        { fiction: "a ledger of every knight's name", real: "a column, stored as one contiguous run of values" },
        { fiction: "Sir Aldric", real: "a row — which is not a unit of storage here, only of meaning" },
        { fiction: "Ask it for a column", real: "projection: a scan opens only the columns it was asked for" },
      ],
      says: "A Lance table is a directory. Inside it are data files holding the columns, a "
          + "manifest for each version, and — where a column holds large values — side files. "
          + "Reads are columnar, so asking for two of twelve columns reads roughly two "
          + "twelfths of the width no matter how many rows there are. Everything else in this "
          + "tour follows from that one fact.",
      links: [
        { slug: "architecture", label: "Architecture" },
        { slug: "path-run-production", label: "Run it in production" },
      ],
    },
    scene: [
      "The Crown does not keep knights. It keeps columns.",
      "There is no shelf with Sir Aldric on it. There is one long ledger of every knight's "
      + "name, a second of every knight's renown, a third of every coat of arms — and, down "
      + "a locked stair, a cellar of painted portraits.",
      "Ask the vault for a knight and nobody knows what you mean. Ask it for a column and "
      + "they will tell you to the byte what it weighs.",
    ],
    needs: 1,
    steps: [
      {
        label: "Fetch Sir Aldric",
        cost: 0,
        reveals: true,
        says: "He comes back empty-handed, and confused. There is no Sir Aldric to fetch — "
            + "no shelf he sits on, no drawer with his things in it. His name is in one "
            + "ledger, his renown in another, his face in a crate downstairs. Ask again, by "
            + "column.",
      },
      {
        label: "the names — 8 bytes a knight",
        cost: 8 * KNIGHTS,
        says: "One ledger, read end to end. Eight bytes for each of the 1,114 knights, and "
            + "not one byte of anything else.",
      },
      {
        label: "the renown — 4 bytes a knight",
        cost: 4 * KNIGHTS,
        says: "Cheaper still. A rating is a single number, and a column of them is 1,114 "
            + "single numbers in a row.",
      },
      {
        label: "the arms — 120 bytes a knight",
        cost: 120 * KNIGHTS,
        showsArms: true,
        says: "Thirty times the width of the renown, for the same 1,114 knights — and now "
            + "you can see what they bear. Which column you ask for matters far more than "
            + "how many knights you ask about.",
      },
    ],
    fact: "You never fetch a knight. You fetch a column, for as many knights as you asked "
        + "about. That is why a table can be enormous and a question about it can be small.",
    term: {
      word: "column",
      means: "A table is stored as one long run per field, not one lump per row. A read "
           + "opens only the runs it asked for and leaves the rest on disk.",
          door: { tab: "schema", label: "Schema", look: "the type and width of every column, and what a page of rows just cost" },
    },
    cites: [{ figure: "1,114", where: "DEMO_SCRIPT.md" }],
  },

  {
    n: "II",
    title: "One vault, not four",
    art: { kind: "versus", left: ["hut", "runner", "hut", "hut", "hut"], right: ["castle"], note: "four buildings, or one" },
    plain: {
      glosses: [
        { fiction: "most realms", real: "the usual architecture for search over documents or media" },
        { fiction: "a ledger house for the facts", real: "a relational database holding the metadata — Postgres, typically" },
        { fiction: "a gallery for the paintings", real: "a blob store holding the files — S3 and its relatives" },
        { fiction: "a wise woman who says which faces are alike", real: "a vector database holding the embeddings" },
        { fiction: "a clerk who wrote down every word", real: "a search index for the text — Elasticsearch, or an inverted index" },
        { fiction: "the runner between them", real: "the pipeline keeping four systems in step, and the seams where the bugs live" },
        { fiction: "one vault", real: "one Lance table holding all four" },
        { fiction: "sealed together whenever anything changes", real: "every write is a version, committed across all columns at once" },
      ],
      says: "In the guide's own words: \u201cThe usual shape of that is four systems \u2014 a blob "
          + "store for the files, a vector database for the embeddings, a relational database "
          + "for the metadata, and a search index for the text \u2014 and most of your bugs live in "
          + "the seams between them. The pitch for Lance is that those are one table.\u201d The "
          + "video and its index are the same table; so are the captions and the metadata.",
      links: [
        { slug: "path-build-on-it", label: "Build on it" },
        { slug: "index", label: "What LanceScope is" },
      ],
    },
    scene: [
      "Most realms keep four buildings. A ledger house for the facts. A gallery for the "
      + "paintings. A wise woman across the valley who can say which two faces are alike. "
      + "A clerk who has written down every word anyone ever said, so a phrase can be found "
      + "again. And a runner going between all four for ever, carrying messages that are out "
      + "of date before they arrive.",
      "This realm keeps one vault. The names, the likenesses and the paintings are shelves "
      + "in the same room, written in the same hand, sealed together every time anything "
      + "changes.",
      "That is the whole pitch. Everything after this is a consequence of it.",
    ],
    needs: 1,
    steps: [
      {
        label: "Walk the vault",
        cost: 0,
        says: "Six shelves, one room. The facts a clerk would keep, the numbers that let "
            + "two faces be compared, and the paintings themselves — and no runner between "
            + "them, because there is nowhere for him to run.",
      },
    ],
    fact: "One table holds the facts, the vectors and the media. The four systems you would "
        + "otherwise wire together — and the bugs that live in the seams between them — are "
        + "one thing.",
    term: {
      word: "table",
      means: "In Lance, a directory of column files plus a manifest saying which version "
           + "you are looking at. Not a service, not a server — a directory.",
          door: { tab: "versions", label: "Versions", look: "every write this table has taken, each one a version you can pin" },
    },
    cites: [],
  },

  {
    n: "III",
    title: "The cellar",
    art: { kind: "row", sprites: ["knight", "crate", "crate"], note: "the cellar" },
    plain: {
      glosses: [
        { fiction: "the cellar", real: "Blob V2 side files, which sit beside the table rather than in it" },
        { fiction: "the tag", real: "a lazy blob handle: which file, what offset, how many bytes" },
        { fiction: "the steward", real: "the manifest, and the total_files_size it reports" },
        { fiction: "he counts the shelves he is responsible for", real: "the manifest describes the files Lance manages, and side files are not among them" },
      ],
      says: "\u201cLance can store a large value \u2014 a video, a model, an archive \u2014 in a side file, "
          + "with the table holding a lazy handle to it. Search and filter cannot touch those "
          + "bytes.\u201d On the reference corpus, total_files_size reports 43,424 bytes for a "
          + "table holding 2.65 GB. Both numbers are correct; they answer different questions, "
          + "and this console never merges them.",
      links: [{ slug: "explain-blobs", label: "Blob V2, and what it hides" }],
    },
    scene: [
      "The portraits are not in the ledger. They never were.",
      "What the portrait column holds is a tag: which crate, how far in, how heavy. Reading "
      + "the tag does not open the crate. It does not go near the crate.",
      "The steward is stranger still. Ask him what the vault weighs and he will say "
      + "forty-three thousand four hundred and twenty-four bytes. He is not lying. He counts "
      + "the shelves he is responsible for, and the cellar is not one of them.",
    ],
    needs: 2,
    steps: [
      {
        label: "Read the portrait column",
        cost: 43 * KB,
        says: `${CRATES} tags, forty-three kilobytes. Every one of them describes a painting `
            + "you have not looked at. One of the tags reads 16.7 MB — and printing that "
            + "number cost nothing, because a tag is not a painting.",
      },
      {
        label: "Ask the steward what the vault weighs",
        cost: 0,
        says: "43,424 bytes, he says, for a vault holding 2.65 GB. Both numbers are true. "
            + "They answer different questions, and this console never merges them.",
      },
    ],
    fact: "Heavy values live in side files, and the table holds a handle to them. Search and "
        + "filter cannot touch those bytes — which is what makes a vault of paintings "
        + "searchable at the cost of a vault of text.",
    term: {
      word: "side file",
      means: "Where Lance puts a large value — a video, a model, an archive — with the row "
           + "keeping only a lazy handle. The manifest does not count them, so the size it "
           + "reports is the small half.",
          door: { tab: "schema", label: "Schema", look: "the blob split — what sits in side files against what the manifest can see" },
    },
    cites: [
      { figure: "43,424", where: "docs/guide/explain-blobs.md" },
      { figure: "2.65 GB", where: "docs/guide/explain-blobs.md" },
      { figure: "16.7 MB", where: "docs/guide/explain-blobs.md" },
    ],
  },

  {
    n: "IV",
    title: "The scale",
    art: { kind: "row", sprites: ["knight", "scales"], note: "weighed on the way out" },
    plain: {
      glosses: [
        { fiction: "the scale", real: "io_stats_incremental() on the dataset handle" },
        { fiction: "weighed on the way out", real: "counters drained after each call \u2014 measured, not modelled" },
        { fiction: "say what you think it is", real: "an estimate, which this console avoids anywhere it can show a count instead" },
      ],
      says: "Every byte figure in the console is drained from Lance's own IO counters after "
          + "the operation, not sampled or predicted. It is a drain, which has a consequence "
          + "worth knowing: two callers sharing one dataset object silently steal each "
          + "other's numbers. A claim about cost is an argument; a counter is not.",
      links: [{ slug: "explain-cost", label: "Why cost is the unit" }],
    },
    scene: [
      "Everything Lancelot carries is weighed on the way out. Not guessed at, not worked out "
      + "from the size of the thing he was sent for — weighed, on the way out, every time.",
      "Before you look at the number, say what you think it is. Reading all 1,114 names, "
      + "eight bytes apiece:",
    ],
    needs: 1,
    steps: [
      {
        label: "About a megabyte, surely",
        cost: 0,
        says: "Not close. It is 8,912 bytes — under nine kilobytes for every name in the "
            + "realm. Intuition about cost is nearly always wrong by an order of magnitude, "
            + "in one direction or the other, which is the entire reason for the scale.",
      },
      {
        label: "Ten kilobytes or so",
        cost: 0,
        says: "8,912 bytes — close. You are one of the few. Most people guess a megabyte, "
            + "which is the entire reason for the scale.",
      },
      {
        label: "I would rather weigh it than guess",
        cost: 0,
        says: "8,912 bytes. Which is the correct instinct: a claim about cost is an "
            + "argument, and a counter is not.",
      },
    ],
    fact: "Every figure in this tour is drained from Lance's own IO counters after the fact. "
        + "Nothing here is estimated or sampled.",
    term: {
      word: "cost",
      means: "Bytes read and IO operations, measured per action rather than modelled. It is "
           + "the unit this console reports in, because with Lance the cost is the "
           + "surprising part.",
          door: { tab: "rows", label: "Rows", look: "the byte and IO counter beside the panel, updating as you browse" },
    },
    cites: [{ figure: "8,912", where: "computed: 8 B × 1,114" }],
  },

  {
    n: "V",
    title: "Described, not carried",
    art: { kind: "row", sprites: ["crate", "knight", "painting"], note: "a tag, and the thing itself" },
    plain: {
      glosses: [
        { fiction: "the handle", real: "a blob handle \u2014 2,722 bytes to open one on the reference corpus" },
        { fiction: "Bring the painting up the stairs", real: "materialising the blob; range requests as it plays" },
        { fiction: "Nothing does this on your behalf", real: "heavy columns are described from the schema and never returned in a result" },
      ],
      says: "From the demo script: \u201cThe video column is not read \u2014 it is described from the "
          + "schema: size, position, and \u2018not materialised\u2019. \u2026 That is the whole design \u2014 "
          + "spending the bytes is a decision someone makes, and the app says what it cost.\u201d "
          + "A row browser can truthfully print 16.7 MB in a cell it never opened.",
      links: [
        { slug: "explain-blobs", label: "Blob V2, and what it hides" },
        { slug: "howto-demo", label: "Run the Ctrl-F for Video demo" },
      ],
    },
    scene: [
      "You have read every tag in the cellar. You have not seen a single painting.",
      "To see one you must say so, and the saying is the expensive part. Opening the handle "
      + "is two thousand seven hundred and twenty-two bytes — you are holding a way to reach "
      + "the crate, not the crate.",
      "Nothing does this on your behalf. A heavy column never comes back in an answer unless "
      + "somebody named it.",
    ],
    needs: 2,
    steps: [
      {
        label: "Open the handle",
        cost: 2_722,
        says: "2,722 bytes. You now have a way to reach the painting. You still have not "
            + "seen it, and the scale has barely moved.",
      },
      {
        label: "Bring the painting up the stairs",
        cost: 17 * MB,
        says: "Seventeen megabytes, one crate. That is the first time the needle has moved "
            + "in a way you can feel — and it moved because you decided it should.",
      },
    ],
    fact: "Spending the bytes is a decision someone makes, and the console says what it "
        + "cost. Heavy columns are described from the schema, never returned in a result.",
    term: {
      word: "projection",
      means: "Choosing which columns come back. Ask for four and you pay for four; the "
           + "hundred-megabyte one beside them costs nothing until it is named.",
          door: { tab: "query", label: "Query", look: "heavy columns held out of the result, and the plan that proves it" },
    },
    cites: [
      { figure: "2,722", where: "README.md" },
      { figure: "17 MB", where: "README.md" },
    ],
  },

  {
    n: "VI",
    title: "Two ways to find a face",
    art: { kind: "versus", left: ["painting", "painting", "painting", "painting"], right: ["oracle"], note: "every one, or the oracle" },
    plain: {
      glosses: [
        { fiction: "a likeness", real: "an embedding \u2014 a fixed_size_list<float32, 1536> beside the row" },
        { fiction: "Compare every likeness by hand", real: "a brute-force vector scan; the query plan names it KNNVectorDistance" },
        { fiction: "the oracle", real: "an ANN index, usually IVF_PQ; the plan names it ANNSubIndex or ANNIvfPartition" },
        { fiction: "remembers roughly where each sits", real: "approximate \u2014 an index trades a little recall for a great deal of reading" },
      ],
      says: "\u201cEvery row's vector is read and compared. Exact, and linear in table size\u201d is "
          + "what the console says about a scan; \u201can approximate index narrowed the candidates "
          + "before distances were computed\u201d is what it says about the other. Which one you "
          + "got is on the Query tab, and it is the answer most of the time \u2014 a search that "
          + "reads megabytes on a small table almost always found no index to use.",
      links: [
        { slug: "howto-diagnose", label: "Diagnose a slow query" },
        { slug: "reference-query", label: "Query modes" },
      ],
    },
    scene: [
      "You are handed a face and asked which knight it belongs to. There are two ways, and "
      + "the vault will do either.",
      "Compare it against every likeness in the vault, one after another. That is exact, and "
      + "it reads every one of them: about three and a half megabytes a question. Ask it a "
      + "thousand times for an evaluation and you have moved three and a half gigabytes to "
      + "answer questions.",
      "Or ask the oracle, who was shown all of them once and remembers roughly where each "
      + "sits. Far cheaper, and approximate — it can be wrong, and mostly it is not.",
    ],
    needs: 1,
    steps: [
      {
        label: "Compare every likeness by hand",
        cost: 3_586_355,
        says: "3.42 MB, and exactly the right knight. Every likeness in the vault was read "
            + "to be sure of it. Do this a thousand times and you have moved 3.4 GB.",
      },
      {
        label: "Ask the oracle",
        cost: 100 * KB,
        says: "100 KB against 3.5 MB — thirty-five times less, because one of those has an "
            + "index behind it and the other does not. The oracle narrowed the field before "
            + "anything was compared.",
      },
    ],
    fact: "A search that reads megabytes on a small table is almost always a search that "
        + "found no index to use. What it read is the evidence; the access path is the name "
        + "for what it did.",
    term: {
      word: "index",
      means: "A structure that narrows the candidates before the expensive comparison. An "
           + "approximate one trades a little accuracy for a great deal of reading.",
          door: { tab: "indices", label: "Indices", look: "what is indexed, and in Query, which access path your search got" },
    },
    cites: [
      { figure: "3.42 MB", where: "docs/guide/path-build-models.md" },
      { figure: "3.4 GB", where: "docs/guide/path-build-models.md" },
      { figure: "100 KB", where: "docs/guide/start-here.md" },
      { figure: "3.5 MB", where: "docs/guide/start-here.md" },
    ],
  },

  {
    n: "VII",
    title: "The close",
    art: { kind: "row", sprites: ["castle", "painting", "knight"], note: "2.65 GB, and the needle at zero" },
    plain: {
      glosses: [
        { fiction: "Search the whole vault", real: "a semantic search over every row of the table" },
        { fiction: "Portrait bytes read: zero", real: "zero bytes read from the blob column" },
        { fiction: "2.65 GB against 69.8 KB", real: "what the table holds, against what a search opens" },
      ],
      says: "\u201cA table can hold 2.65 GB of video against 20.1 MB of everything a search reads "
          + "\u2014 a ratio of 132 to 1 \u2014 and a semantic search over every row in it reads zero "
          + "video bytes. Not \u2018very little\u2019. Zero.\u201d That is hard to believe from a "
          + "description and trivial to believe from a counter, which is why the counter is on "
          + "screen.",
      links: [
        { slug: "explain-cost", label: "Why cost is the unit" },
        { slug: "index", label: "What LanceScope is" },
      ],
    },
    scene: [
      "The cellar holds two and a half gigabytes of painted portraits. Everything you have "
      + "learned is about to be worth something.",
      "Search all of them — not the tags, not the names, the paintings themselves — for a "
      + "diagram with boxes and arrows.",
    ],
    needs: 2,
    steps: [
      {
        label: "Search the whole vault",
        cost: 3_617_587,
        says: "3.45 MB of index read. Portrait bytes read: zero. Not 'very little'. Zero — "
            + "because the paintings are not in the files a search opens.",
      },
      {
        label: "Now open one result",
        cost: 17 * MB,
        says: "And there the needle goes. Seventeen megabytes, for one crate, because you "
            + "asked for one crate.",
      },
    ],
    fact: "The bytes a search touches and the bytes a table holds live in different files. "
        + "2.65 GB of paintings against 69.8 KB of ordinary shelves — 37,978 to one. That is "
        + "the whole of it, and every number you were shown getting here was measured.",
    term: {
      word: "the ratio",
      means: "What a table holds, over what a question about it reads. On this corpus it is "
           + "37,978 to 1, and it is the reason any of the rest matters.",
          door: { tab: "insights", label: "Insights", look: "every finding, each carrying the numbers it was computed from" },
    },
    cites: [
      { figure: "3.45 MB", where: "README.md" },
      { figure: "2.65 GB", where: "README.md" },
      { figure: "69.8 KB", where: "docs/guide/path-build-models.md" },
      { figure: "37,978", where: "README.md" },
    ],
  },
];

export const GLOSSARY = CHAPTERS.map((c) => c.term);
