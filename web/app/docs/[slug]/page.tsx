import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Icon from "@/app/components/Icon";
import Diagrams from "@/app/components/docs/Diagrams";
import { OnThisPage } from "@/app/components/docs/OnThisPage";
import { PathPicker } from "@/app/components/docs/PathPicker";
import { DocFooter, PathMarker } from "@/app/components/docs/PathProgress";
import { allDocs, getDoc, neighbours } from "@/app/lib/docs";

export function generateStaticParams() {
  return allDocs().map((d) => ({ slug: d.slug }));
}

export async function generateMetadata(
  { params }: { params: Promise<{ slug: string }> },
): Promise<Metadata> {
  const doc = getDoc((await params).slug);
  return doc
    ? { title: `${doc.title} · LanceScope`, description: doc.summary }
    : {};
}

export default async function DocPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const doc = getDoc(slug);
  if (!doc) notFound();

  const { prev, next } = neighbours(slug);
  const minutes = Math.max(1, Math.round(doc.words / 220));
  // Slug → title for every page, so the client components can name a step without
  // being handed the whole guide. Twenty-one short strings.
  const titles = Object.fromEntries(allDocs().map((d) => [d.slug, d.title]));

  return (
    <>
      <article className="flex-1 min-w-0">
        <div className="flex items-center gap-3 mb-4 flex-wrap max-w-[72ch]">
          <span className="eyebrow">{doc.section}</span>
          <span className="mono text-[10px] text-[var(--dim)]">
            {minutes} min read
          </span>
          {/* Said on the page rather than only in the file, because a reader who
              wants to fix something needs to know that editing it would be undone. */}
          {doc.generated && (
            <span className="mono text-[10px] flex items-center gap-1.5"
                  style={{ color: "var(--index)" }}>
              <Icon name="refresh" size={11} />
              generated from the code
            </span>
          )}
          <PathMarker slug={slug} />
        </div>

        <div className="prose" dangerouslySetInnerHTML={{ __html: doc.html }} />

        {/* Only where there is something to draw. Mermaid is the heaviest thing the
            interface can load, and most of the guide is prose. */}
        {doc.diagrams && <Diagrams />}

        {/* The guide opens on `index`, so the doors go there rather than on a
            landing page of their own that everyone would have to pass through. */}
        {slug === "index" && <PathPicker titles={titles} />}

        <DocFooter
          slug={slug}
          titles={titles}
          prev={prev ? { slug: prev.slug, title: prev.title } : null}
          next={next ? { slug: next.slug, title: next.title } : null}
        />

      </article>

      <OnThisPage headings={doc.headings} />
    </>
  );
}
