# Introducing LanceScope: The LanceDB Workbench That Shows You What Your Queries Actually Cost

I've been spending a lot of time lately staring at LanceDB queries and wondering what they're actually costing me. The tools we have show us results, latency, row counts... but they don't show us what's really happening under the hood.

So I built something to fix that.

LanceScope is a workbench for understanding LanceDB databases. It shows you not just what your database contains, but what every operation actually costs in terms of I/O, bytes read, and system resources.

## The Problem: Cost is the Surprising Number

Here's something that surprised me when I really started looking at LanceDB... the bytes a query touches and the bytes a table holds can be radically different.

Take this real example from my demo corpus. I have a table holding 2.65 GB of video but only 20.1 MB of everything a search actually reads. That's a ratio of 132 to 1.

A semantic search over every row in that table reads zero video bytes. Not very little... literally zero. The video bytes aren't in the files a search opens. This works because of Lance's Blob V2 column type, which stores heavy data in side files with lazy loading handles.

Most database tools would show you the same result either way. LanceScope shows you the difference.

## What LanceScope Does

LanceScope is a workbench for understanding a LanceDB database... what's in it, why it behaves the way it does, and what every answer cost to get. I built it with four ways to interact with your data.

### Web Console
This is a browser-based interface for browsing databases, running queries, and seeing byte costs in real-time. You can connect to any Lance directory and explore schemas, versions, indices, fragments, and rows with the byte cost of each read shown as you go.

### CLI Tool
There's also a headless command-line interface for ingestion and scanning. You can survey directories of media files, check what your build can decode, and build Lance tables with progress tracking and capability detection.

### MCP Server
I added an MCP server so you can expose LanceDB's read surface to AI agents like Claude Code. This lets agents inspect your database directly with the same evidence the console uses, all through the Model Context Protocol.

### macOS Desktop App
I also built a self-contained macOS application that bundles the console and server. No Python, no Node, no Lance to install... just a 160 MB DMG that works out of the box.

## The Feature That Actually Matters: IO Cost Tracking

What makes LanceScope different is that every operation reports byte costs measured from Lance's own I/O counters. This isn't estimation or sampling... it's exactly what each request read and nothing else.

When you list tables, you see the cost. When you read a schema, you see the cost. When you run a search, you see the cost broken down by access path.

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

LanceScope doesn't just show costs... it works things out for you. I built in seven rules over metadata, each carrying the numbers it was derived from.

For example, it might tell you that your vector column has no index. Every similarity search over that vector scans all 1,114 rows and reads each 768-dimension vector to do it. That's fine at this size but stops being fine as the table grows.

Or it might warn you about small file counts that would mislead. One table I looked at had 2,651 fragments, which sounds terrible. But 2,644 of those were tombstone files from compaction... the real story was just 7 data fragments.

No model is involved in any of these. It's arithmetic over metadata Lance already reports, presented as actionable insights.

## Optional Intelligence, Fully Metered

When you need natural language queries, LanceScope has you covered with an optional intelligence layer. It supports Anthropic Claude for cloud-based intelligence, local Ollama models for free offline operation, and OpenAI-compatible endpoints for flexibility.

But here's the key... every response reports tokens and dollars spent beside the bytes read. A tool built to make read cost visible has no business hiding inference cost.

## The Demo That Started It All: Ctrl-F for Video

LanceScope actually grew out of a conference demo I built called Ctrl-F for Video. The demo makes a pretty compelling technical claim... the video and its index are the same table.

You type something like "a diagram with boxes and arrows" and get back actual frames from a corpus of conference talks. Click one, and the video plays at that exact second... while an instrument along the bottom shows how few bytes moved to make it happen.

On a 16-talk corpus with 1,114 moments and 2.65 GB of video, here's what I measured.

- semantic search over every moment: 3.45 MB index bytes, 0 video bytes
- full-text search over transcripts: 0.11 MB index bytes, 0 video bytes  
- the same search, filtered to one devroom: 3.45 MB index bytes, 0 video bytes
- open a blob handle: 2,722 video bytes
- start playback (cold segment): about 17 MB video bytes
- seek again inside it (warm): 262,144 video bytes, byte-exact

That's not a demo trick... that's LanceDB Blob V2 working as designed, and LanceScope is the tool that lets you see it happen.

## For Developers: Multi-Interface Architecture

What's interesting about LanceScope technically is that all four interfaces share the same core processing logic. The CLI and web interface call the same functions in ingest.core, and the MCP server wraps the same HTTP routes the console uses.

This means you get consistent behavior across all interfaces, a single source of truth for data processing, easier testing and maintenance, and no divergence between CLI and web behavior.

The architecture is built around a few key design patterns... catalog scoping so dataset handles don't pollute each other's I/O counters, capability detection so the system reports what it can and can't decode rather than failing silently, progressive disclosure with high-level overview for users and implementation details for developers, and a read-only console that intentionally prevents dataset modifications for safe exploration.

## Getting Started

The fastest way to understand LanceScope is to just use it.

```bash
git clone https://github.com/mrlynn/lancescope
cd lancescope
make setup     # python deps and web deps
make dev       # API on :8000, console on :3000
```

Open http://localhost:3000/console and you'll see if there's a database configured. If not, head to settings and point it at a directory containing .lance tables.

Don't have a database handy? You can build the demo corpus.

```bash
make ingest LIMIT=8    # downloads a handful of conference talks
```

This will give you two tables to explore... moments (keyframes with embeddings) and segments (playable video chunks in Blob V2 columns).

## The Philosophy: Evidence Before Advice

There are plenty of database tools that will tell you what to do. LanceScope is different... it shows you the evidence first and lets you decide.

An unindexed vector column isn't labeled bad... it's presented with the numbers like row count, dimensions, bytes per scan, and what that means at scale. A table with many fragments isn't called fragmented... it shows you the real count versus tombstone files and explains the difference.

This approach extends to the language layer too. When you ask what's wrong with your database, the response comes with the exact rules that ran, the evidence they used, and the cost of generating the answer.

## For the LanceDB Community

I built LanceScope because LanceDB enables data architectures that weren't possible before, but the tools to understand them haven't caught up. When your database can hold gigabytes of video alongside embeddings without affecting search performance, you need a tool that can show you that rather than just asserting it.

Whether you're building RAG applications and need to understand vector search costs, working with multimodal data and want to see how Blob V2 performs, optimizing production databases and need visibility into access patterns, or evaluating different indexing strategies and want to measure the difference... LanceScope gives you the evidence you need to make informed decisions.

## What's Next

LanceScope is open source and ready for production use. The console is stable and useful without any AI features, the CLI handles real ingestion workloads, and the MCP server lets you bring AI agents to your data safely.

I'm actively working on enhanced ingest capabilities for more media types, expanded intelligence features with more providers, additional deployment options and integrations, and performance optimizations for large-scale databases.

## Try It Out

The best way to understand LanceScope is to use it on your own data.

```bash
# Clone and run
git clone https://github.com/mrlynn/lancescope
cd lancescope
make setup && make dev

# Or try the demo
make ingest LIMIT=36
```

Or grab the macOS app for a self-contained experience that needs no installation.

LanceScope is a different kind of database tool... one that shows you what's actually happening rather than what you assume is happening. In a world where vector databases are becoming central to AI applications, that visibility matters more than ever.

See what your LanceDB databases are actually doing. The costs might surprise you.

---

LanceScope is open source and available at github.com/mrlynn/lancescope. It works with LanceDB 3.0+ and runs on macOS, Linux, and in containers.