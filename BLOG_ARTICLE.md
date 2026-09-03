# Introducing LanceScope: The LanceDB Workbench That Shows You What Your Queries Actually Cost

In the world of vector databases and modern data infrastructure, we've become accustomed to tools that show us results but hide the real story. We see query latency, row counts, and result sets, but we rarely see what those queries actually cost in terms of I/O, bytes read, and system resources.

Today I'm excited to introduce **LanceScope**, a new kind of database workbench designed specifically for LanceDB that makes the invisible visible: a tool that shows you not just what your database contains, but what every operation actually costs.

## The Problem: Cost is the Surprising Number

If you're working with LanceDB, you already know it's different from traditional databases. But here's something that might surprise you: in Lance, the bytes a query touches and the bytes a table holds can be radically different.

Consider this real example from our demo corpus: a table holding **2.65 GB of video** against just **20.1 MB of everything a search actually reads**. That's a ratio of 132 to 1.

A semantic search over every row in that table reads **zero video bytes**. Not "very little" — literally zero, because the video bytes aren't in the files a search opens. This is possible thanks to Lance's Blob V2 column type, which stores heavy data in side files with lazy loading handles.

Most database tools would show you the same result either way. LanceScope shows you the difference.

## What LanceScope Does

LanceScope is a workbench for understanding a LanceDB database — what's in it, why it behaves the way it does, and what every answer cost to get. It provides four ways to interact with your data:

### 🖥️ Web Console
A browser-based interface for browsing databases, running queries, and seeing byte costs in real-time. Connect to any Lance directory and explore schemas, versions, indices, fragments, and rows with the byte cost of each read shown as you go.

### 💻 CLI Tool
A headless command-line interface for ingestion and scanning. Survey directories of media files, check what your build can decode, and build Lance tables with progress tracking and capability detection.

### 🤖 MCP Server
Expose LanceDB's read surface to AI agents like Claude Code. Let agents inspect your database directly with the same evidence the console uses, all through the Model Context Protocol.

### 🍎 macOS Desktop App
A self-contained application that bundles the console and server. No Python, no Node, no Lance to install — just a 160 MB DMG that works out of the box.

## The Killer Feature: IO Cost Tracking

What makes LanceScope different is that every operation reports byte costs measured from Lance's own I/O counters. This isn't estimation or sampling — it's exactly what each request read and nothing else.

When you list tables, you see the cost. When you read a schema, you see the cost. When you run a search, you see the cost broken down by access path:

```
RETURNED 10   TIME 1 ms   READ 3.5 MB   IOS 21
brute-force vector scan — Every row's vector is read and compared.
```

Switch to full-text search on the same table:

```
RETURNED 25   TIME 2 ms   READ 100 KB
inverted index — Full-text search used the inverted index rather than reading the column.
```

Thirty-five times less, because one column has an index and the other doesn't. That's not just a number — that's an insight that changes how you build your data pipeline.

## Beyond Cost: Intelligent Analysis

LanceScope doesn't just show costs — it works things out for you. Seven rules over metadata, each carrying the numbers it was derived from:

> **vector has no vector index**
> Every similarity search over vector scans all 1,114 rows and reads each 768-dimension vector to do it. That is fine at this size and stops being fine as the table grows.

> **small file count would mislead**
> This table has 2,651 fragments. A file count of 2,651 would suggest a badly fragmented table, but 2,644 of those are tombstone files from compaction — the real story is 7 data fragments.

No model is involved in any of these. It's arithmetic over metadata Lance already reports, presented as actionable insights.

## Optional Intelligence, Fully Metered

When you need natural language queries, LanceScope has you covered with an optional intelligence layer that supports:

- **Anthropic Claude** for cloud-based intelligence
- **Local Ollama models** for free, offline operation
- **OpenAI-compatible endpoints** for flexibility

But here's the key: every response reports tokens and dollars spent beside the bytes read. A tool built to make read cost visible has no business hiding inference cost.

## The Demo That Started It All: Ctrl-F for Video

LanceScope grew out of a conference demo called "Ctrl-F for Video" that makes a compelling technical claim:

> **The video and its index are the same table.**

You type *"a diagram with boxes and arrows"* and get back actual frames from a corpus of conference talks. Click one, and the video plays at that exact second — while an instrument along the bottom shows how few bytes moved to make it happen.

On a 16-talk corpus with 1,114 moments and 2.65 GB of video:

| operation | index bytes | video bytes |
|---|---|---|
| semantic search over every moment | 3.45 MB | **0** |
| full-text search over transcripts | 0.11 MB | **0** |
| the same search, filtered to one devroom | 3.45 MB | **0** |
| open a blob handle | — | 2,722 |
| start playback (cold segment) | — | ~17 MB |
| seek again inside it (warm) | — | 262,144 — byte-exact |

That's not a demo trick — that's LanceDB Blob V2 working as designed, and LanceScope is the tool that lets you see it happen.

## For Developers: Multi-Interface Architecture

What's interesting about LanceScope technically is that all four interfaces share the same core processing logic. The CLI and web interface call the same functions in `ingest.core`, and the MCP server wraps the same HTTP routes the console uses.

This means:
- Consistent behavior across all interfaces
- Single source of truth for data processing
- Easier testing and maintenance
- No divergence between CLI and web behavior

The architecture is built around a few key design patterns:

- **Catalog Scoping**: Dataset handles are scoped to prevent I/O counter pollution between operations
- **Capability Detection**: System reports what it can/cannot decode rather than failing silently
- **Progressive Disclosure**: High-level overview for users, implementation details for developers
- **Read-Only Console**: Intentionally prevents dataset modifications for safe exploration

## Getting Started

The fastest way to understand LanceScope is to use it:

```bash
git clone https://github.com/your-org/lancescope
cd lancescope
make setup     # python deps and web deps
make dev       # API on :8000, console on :3000
```

Open http://localhost:3000/console and you'll see if there's a database configured. If not, head to settings and point it at a directory containing `.lance` tables.

No database to hand? Build the demo corpus:

```bash
make ingest LIMIT=8    # downloads a handful of conference talks
```

This will give you two tables to explore: `moments` (keyframes with embeddings) and `segments` (playable video chunks in Blob V2 columns).

## The Philosophy: Evidence Before Advice

There are plenty of database tools that will tell you what to do. LanceScope is different — it shows you the evidence first and lets you decide.

An unindexed vector column isn't labeled "bad" — it's presented with the numbers: row count, dimensions, bytes per scan, and what that means at scale. A table with many fragments isn't called "fragmented" — it shows you the real count versus tombstone files and explains the difference.

This approach extends to the language layer. When you ask "what's wrong with this database?", the response comes with the exact rules that ran, the evidence they used, and the cost of generating the answer.

## For the LanceDB Community

LanceScope exists because LanceDB enables data architectures that weren't possible before, but the tools to understand them haven't caught up. When your database can hold gigabytes of video alongside embeddings without affecting search performance, you need a tool that can show you that rather than just asserting it.

Whether you're:
- **Building RAG applications** and need to understand vector search costs
- **Working with multimodal data** and want to see how Blob V2 performs
- **Optimizing production databases** and need visibility into access patterns
- **Evaluating different indexing strategies** and want to measure the difference

LanceScope gives you the evidence you need to make informed decisions.

## What's Next

LanceScope is open source and ready for production use. The console is stable and useful without any AI features, the CLI handles real ingestion workloads, and the MCP server lets you bring AI agents to your data safely.

We're actively developing:
- Enhanced ingest capabilities for more media types
- Expanded intelligence features with more providers
- Additional deployment options and integrations
- Performance optimizations for large-scale databases

## Try It Out

The best way to understand LanceScope is to use it on your own data:

```bash
# Clone and run
git clone https://github.com/your-org/lancescope
cd lancescope
make setup && make dev

# Or try the demo
make ingest LIMIT=36
```

Or grab the macOS app for a self-contained experience that needs no installation.

LanceScope is a different kind of database tool — one that shows you what's actually happening rather than what you assume is happening. In a world where vector databases are becoming central to AI applications, that visibility matters more than ever.

**See what your LanceDB databases are actually doing.** The costs might surprise you.

---

*LanceScope is open source and available at [github.com/your-org/lancescope]. It works with LanceDB 3.0+ and runs on macOS, Linux, and in containers.*