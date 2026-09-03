import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import Icon from "@/app/components/Icon";
import Diagrams from "@/app/components/docs/Diagrams";
import { OnThisPage } from "@/app/components/docs/OnThisPage";
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

  return (
    <>
      <article className="flex-1 min-w-0 max-w-[72ch]">
        <div className="flex items-center gap-3 mb-4 flex-wrap">
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
        </div>

        <div className="prose" dangerouslySetInnerHTML={{ __html: doc.html }} />

        {/* Only where there is something to draw. Mermaid is the heaviest thing the
            interface can load, and most of the guide is prose. */}
        {doc.diagrams && <Diagrams />}

        <nav className="flex flex-wrap gap-3 justify-between mt-14 pt-6"
             style={{ borderTop: "1px solid var(--hairline)" }}>
          {prev ? (
            <Link href={`/docs/${prev.slug}`} className="group max-w-[46%]">
              <div className="eyebrow mb-1">Previous</div>
              <div className="text-[13px] text-[var(--body)] group-hover:text-[var(--bright)]">
                {prev.title}
              </div>
            </Link>
          ) : <span />}
          {next && (
            <Link href={`/docs/${next.slug}`} className="group max-w-[46%] text-right">
              <div className="eyebrow mb-1">Next</div>
              <div className="text-[13px] text-[var(--body)] group-hover:text-[var(--bright)]">
                {next.title}
              </div>
            </Link>
          )}
        </nav>
      </article>

      <OnThisPage headings={doc.headings} />
    </>
  );
}
