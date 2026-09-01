/** Reading the guide off disk, at build time.
 *
 *  The pages live in `docs/guide/` at the repository root rather than inside the web
 *  app, for two reasons. They are versioned with the code they describe, so a change
 *  and its documentation land in the same commit. And six of them are generated from
 *  the code by `scripts/gen_docs.py`, which has no business writing into a Next.js
 *  tree.
 *
 *  All of this runs in a server component during the build, so the markdown parser
 *  and the syntax highlighter never reach a browser.
 */

import fs from "node:fs";
import path from "node:path";
import hljs from "highlight.js";
import { Marked } from "marked";

const GUIDE = path.join(process.cwd(), "..", "docs", "guide");

/** The four kinds of page, in reading order. Diátaxis, more or less: something to
 *  follow, something to do, something to look up, and something to understand. */
export const SECTIONS = [
  "Start here",
  "How to",
  "Reference",
  "Why it works this way",
] as const;

export type Heading = { id: string; text: string; level: number };

export type Doc = {
  slug: string;
  title: string;
  section: string;
  order: number;
  summary: string;
  generated: boolean;
  html: string;
  headings: Heading[];
  /** Words of prose, for nothing more than telling a reader how long a page is. */
  words: number;
};

/** An id a heading can be linked to, stable enough to survive rewording elsewhere
 *  on the page. */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/`/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/** Deliberately small: the front matter is ours, written by hand or by the
 *  generator, and a YAML parser would be a dependency for four keys. */
function parseFrontMatter(raw: string): [Record<string, string>, string] {
  if (!raw.startsWith("---")) return [{}, raw];
  const end = raw.indexOf("\n---", 3);
  if (end === -1) return [{}, raw];
  const meta: Record<string, string> = {};
  for (const line of raw.slice(4, end).split("\n")) {
    const at = line.indexOf(":");
    if (at > 0) meta[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return [meta, raw.slice(end + 4)];
}

function renderer(headings: Heading[]) {
  const marked = new Marked({ gfm: true });
  marked.use({
    renderer: {
      // Headings carry an anchor, so a section of a reference page can be linked to
      // directly — which is most of what makes reference documentation usable.
      heading({ tokens, depth }) {
        const text = this.parser.parseInline(tokens);
        const plain = text.replace(/<[^>]+>/g, "");
        const id = slugify(plain);
        if (depth > 1 && depth <= 3) headings.push({ id, text: plain, level: depth });
        return `<h${depth} id="${id}"><a class="anchor" href="#${id}">${text}</a></h${depth}>\n`;
      },
      code({ text, lang }) {
        const language = lang && hljs.getLanguage(lang) ? lang : null;
        const body = language
          ? hljs.highlight(text, { language }).value
          : text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
        // The language is shown rather than inferred silently: a reader deciding
        // whether to paste something wants to know what it is.
        return `<figure class="code"><figcaption>${lang ?? ""}</figcaption>`
          + `<pre><code class="hljs">${body}</code></pre></figure>\n`;
      },
    },
  });
  return marked;
}

let cache: Doc[] | null = null;

export function allDocs(): Doc[] {
  if (cache) return cache;

  const files = fs.existsSync(GUIDE)
    ? fs.readdirSync(GUIDE).filter((f) => f.endsWith(".md"))
    : [];

  const docs = files.map((file) => {
    const raw = fs.readFileSync(path.join(GUIDE, file), "utf8");
    const [meta, body] = parseFrontMatter(raw);
    const headings: Heading[] = [];
    const html = renderer(headings).parse(body) as string;
    const slug = file.replace(/\.md$/, "");
    return {
      slug,
      title: meta.title ?? slug,
      section: meta.section ?? "Reference",
      order: Number(meta.order ?? 99),
      summary: meta.summary ?? "",
      generated: meta.generated === "true",
      html,
      headings,
      words: body.split(/\s+/).filter(Boolean).length,
    };
  });

  cache = docs.sort((a, b) => {
    const s = SECTIONS.indexOf(a.section as never) - SECTIONS.indexOf(b.section as never);
    return s !== 0 ? s : a.order - b.order;
  });
  return cache;
}

export function getDoc(slug: string): Doc | undefined {
  return allDocs().find((d) => d.slug === slug);
}

/** Ordered as the sidebar shows them, so "next" means what a reader expects. */
export function neighbours(slug: string): { prev?: Doc; next?: Doc } {
  const docs = allDocs();
  const i = docs.findIndex((d) => d.slug === slug);
  return { prev: docs[i - 1], next: docs[i + 1] };
}

/** What the sidebar and the search box need — everything except the rendered HTML,
 *  which is far too big to hand to a client component. */
export type DocIndexEntry = Pick<Doc, "slug" | "title" | "section" | "summary"> & {
  headings: string[];
};

export function docIndex(): DocIndexEntry[] {
  return allDocs().map((d) => ({
    slug: d.slug,
    title: d.title,
    section: d.section,
    summary: d.summary,
    headings: d.headings.map((h) => h.text),
  }));
}
