---
title: Blob V2, and what it hides
section: Why it works this way
order: 2
summary: Side files, a manifest that cannot see them, and a small-file count that would mislead you.
---

# Blob V2, and what it hides

Lance can store a large value — a video, a model, an archive — in a side file, with
the table holding a lazy handle to it. Search and filter cannot touch those bytes.
This is the thing that makes a table of video searchable at the cost of a table of
text, and it produces three effects the console has to handle carefully.

## The manifest cannot see side files

`tracked_files()` lists no `.blob` paths, and `total_files_size` reports 43,424 bytes
for a table holding 2.65 GB. The manifest is not wrong; it describes the files Lance
manages, and the side files are not among them.

So the cheap figure and the true figure are different numbers answering different
questions, and every panel that shows one says which. The true split comes from
walking the directory, cached per table version because it is O(files).

## A blob column is safe to project

The first version of the row browser assumed the opposite. Selecting a Blob V2 column
yields a **descriptor** — position and size — not bytes. All 162 descriptors in the
reference corpus cost 43 KB while describing 2.65 GB, which is why a row browser can
truthfully print `16.7 MB` in a cell it never read.

The columns that actually cost something are the ordinary ones. `thumb_jpeg` takes a
page of rows from 34 KB to 383 KB.

## The small-file count would mislead you

`num_small_files` flags all 16 fragments of the reference `segments` table, and by
Lance's own measure it is right — the data files are 2.7 KB each. They also hold about
195 MB of video apiece.

A compaction button wired to that number would rewrite the small half of a table that
needs nothing done to it. So the rule that reports the count is the same rule that
says so:

> This table keeps its bytes in Blob V2 side files, which the manifest cannot see. Its
> data files are small because that is where the data isn't — compacting them would
> rewrite 2.65 GB of side files to tidy up 0.1 MB of metadata.

The caveat is not decoration. A test fails if it goes missing.

## There is a size threshold

Below roughly 8 MB, Lance packs a blob into the data file rather than giving the row
its own extent. Eight blobs of 4 KB produce no side files at all. Measured, not
assumed — a test fixture built to exercise the side-file path was not exercising it
until the threshold was found.
