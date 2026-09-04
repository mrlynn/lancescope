"use client";

/** Seven chapters, a scale that carries, and a glossary that builds itself.
 *
 *  The shape of every chapter is the same and never varies: a scene, one thing to do, the
 *  scale moving, and the fact it earned. Nothing is asked before it has been shown, there is
 *  no score and no failure state — the only wrong-looking move in the whole tour is the
 *  first button of chapter I, and it is wrong on purpose and free.
 */

import { useCallback, useState } from "react";
import Icon from "@/app/components/Icon";
import Shield from "./Shield";
import Pixel from "./Pixel";
import { blazonOf, deck, type Arms } from "./arms";
import { GLOSSARY } from "./chapters";
import {
  CHAPTERS, armsShown, chapter, format, offered, onward, startTour, take,
  type Chapter, type Term, type Tour as TourState,
} from "./engine";

export default function Tour({ onFinish }: { onFinish: (learned: Term[]) => void }) {
  const [t, setT] = useState<TourState>(startTour);
  // The engine mutates in place; React is told to look again rather than handed a copy.
  const bump = useCallback(() => setT((x) => ({ ...x })), []);
  // A stable value, so useState rather than a ref read during render — the arms never
  // change and nothing needs to be told when they don't.
  const [arms] = useState<Arms[]>(() => deck(8));

  const c = chapter(t);
  const can = offered(t);
  // Collapsed on every new chapter: stepping out of the story should be a decision, not a
  // state you drift into and then read the whole tour from outside.
  const [plainAt, setPlainAt] = useState<number | null>(null);
  const plain = plainAt === t.at;

  return (
    <div className="egg-body">
      <aside className="egg-side">
        <div className="egg-scale">
          <div className="eyebrow mb-1.5">the scale</div>
          <div className="egg-scale-v">{format(t.scale)}</div>
          <p className="egg-scale-note">
            Everything Lancelot has carried so far, weighed on the way out.
          </p>
        </div>

        <div className="egg-glossary">
          <div className="eyebrow mb-2">
            what you know now · {t.learned.length} of {CHAPTERS.length}
          </div>
          {t.learned.length === 0 ? (
            <p className="egg-glossary-empty">
              Nothing yet. Each chapter leaves one word behind.
            </p>
          ) : (
            <dl>
              {t.learned.map((x) => (
                <div key={x.word}>
                  <dt>{x.word}</dt>
                  <dd>{x.means}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </aside>

      <section className="egg-main">
        <div className="egg-chapter-head">
          <span className="egg-chapter-n">Chapter {c.n}</span>
          <h2 className="egg-chapter-t">{c.title}</h2>
        </div>
        <Band art={c.art} />

        <ol className="egg-progress" aria-label={`Chapter ${t.at + 1} of ${CHAPTERS.length}`}>
          {CHAPTERS.map((x, i) => (
            <li key={x.n} className={i < t.at ? "done" : i === t.at ? "here" : ""} />
          ))}
        </ol>

        {c.scene.map((p, i) => <p key={i} className="egg-scene">{p}</p>)}

        {armsShown(t) ? (
          <div className="egg-arms-row">
            {arms.map((a, i) => (
              <Shield key={i} arms={a} size={26} title={blazonOf(a)} />
            ))}
            <span className="egg-arms-note">
              {arms.length} of the 1,114, drawn from what you just read.
            </span>
          </div>
        ) : null}

        {/* The way out of the fiction. Present in every chapter, at any point in it. */}
        <div className="egg-plainbar">
          <button type="button" className="egg-plain-toggle" aria-expanded={plain}
                  onClick={() => setPlainAt(plain ? null : t.at)}>
            {plain ? "back to the story" : "wait \u2014 what is this really?"}
          </button>
        </div>
        {plain ? <PlainTerms c={c} /> : null}

        {!t.done ? (
          <div className="egg-options">
            {can.map((i) => (
              <button key={i} type="button" className="egg-option"
                      onClick={() => { take(t, i); bump(); }}>
                <span>{c.steps[i].label}</span>
                {c.steps[i].cost > 0
                  ? <span className="egg-option-cost">{format(c.steps[i].cost)}</span>
                  : null}
              </button>
            ))}
          </div>
        ) : null}

        {t.said ? <p className="egg-said">{t.said}</p> : null}

        {t.done ? (
          <div className="egg-fact">
            <div className="eyebrow mb-2">so</div>
            <p>{c.fact}</p>
            <div className="egg-fact-term">
              <b>{c.term.word}</b> — {c.term.means}
            </div>
            <button type="button" className="btn btn-accent" autoFocus
                    onClick={() => {
                      if (t.at + 1 >= CHAPTERS.length) { onFinish(t.learned); return; }
                      onward(t); bump();
                    }}>
              {t.at + 1 >= CHAPTERS.length ? "Now you try" : `Chapter ${CHAPTERS[t.at + 1].n}`}
            </button>
          </div>
        ) : null}

        {!t.done && t.at === 0 && !t.revealed ? (
          <p className="egg-hint">
            There is one obvious thing to try. It will not work, and that is the lesson.
          </p>
        ) : null}
      </section>
    </div>
  );
}

/** The translation out of the allegory: every phrase against the thing it stands for, the
 *  truth in the project's own words, and the guide pages where it is written down properly.
 *
 *  A story that cannot be cashed out is a story someone has to take on faith, and this is a
 *  console whose entire claim is that its numbers can be checked. */
function PlainTerms({ c }: { c: Chapter }) {
  return (
    <div className="egg-plain">
      <div className="eyebrow mb-2">out of the story &middot; chapter {c.n}</div>
      <dl className="egg-plain-glosses">
        {c.plain.glosses.map((g) => (
          <div key={g.fiction}>
            <dt>&ldquo;{g.fiction}&rdquo;</dt>
            <dd>{g.real}</dd>
          </div>
        ))}
      </dl>
      <p className="egg-plain-says">{c.plain.says}</p>
      <div className="egg-plain-links">
        {c.plain.links.map((l) => (
          <a key={l.slug} className="egg-plain-link" href={`/docs/${l.slug}`}>
            {l.label} &rarr;
          </a>
        ))}
      </div>
    </div>
  );
}

/** The establishing shot. You should know where you are before you start reading. */
function Band({ art }: { art: Chapter["art"] }) {
  return (
    <div className="egg-band">
      {art.note ? <span className="egg-band-note">{art.note}</span> : null}
      {art.kind === "row"
        ? art.sprites.map((id, i) => (
            <Pixel key={i} id={id} scale={4}
                   className={id === "knight" ? "egg-band-knight" : undefined} />
          ))
        : (
          <>
            <span className="egg-band-huts">
              {art.left.map((id, i) => (
                <Pixel key={i} id={id} scale={3} />
              ))}
            </span>
            <span className="egg-band-vs">or</span>
            {art.right.map((id, i) => (
              <Pixel key={i} id={id} scale={4} />
            ))}
          </>
        )}
    </div>
  );
}

/** The last card: seven words, and the tab each one lives on.
 *
 *  The egg is hidden inside the console, which makes this the only ending that is actually
 *  useful — the player has just learned the vocabulary and is standing in the application
 *  where all of it is on screen, about their own table. A quiz used to go here and it asked
 *  them to do the job the findings already do.
 */
export function Doors({ learned, onClose }: { learned: Term[]; onClose: () => void }) {
  const words = learned.length ? learned : GLOSSARY;
  return (
    <div className="egg-card">
      <div className="egg-sheet">
        <div className="egg-sheet-body">
          <div className="eyebrow mb-2">the tour is over</div>
          <h2 className="text-[22px] font-extrabold tracking-tight text-[var(--bright)] mb-3">
            You know seven words. Each one has a tab.
          </h2>
          <p className="text-[13.5px] leading-relaxed text-[var(--body)] mb-4">
            All of it is already on screen behind this, about your own table rather than a
            realm somebody invented. Here is where each word is showing.
          </p>

          <ul className="egg-doors">
            {words.map((t) => (
              <li key={t.word}>
                <a className="egg-door" href={`/console?tab=${t.door.tab}`}>
                  <span className="egg-door-word">{t.word}</span>
                  <span className="egg-door-tab">{t.door.label} &rarr;</span>
                  <span className="egg-door-look">{t.door.look}</span>
                </a>
              </li>
            ))}
          </ul>

          <p className="text-[12.5px] leading-relaxed text-[var(--haze)] mt-4">
            If nothing is connected yet, the console will say so and offer you Settings. The
            tour needed no database and the console needs one — that is the only difference
            between the two halves of this.
          </p>
        </div>
        <div className="egg-actions">
          <a className="btn btn-accent" href="/console?tab=insights">Open the console</a>
          <button type="button" className="btn" onClick={onClose}>Back to work</button>
        </div>
      </div>
    </div>
  );
}

/** The rail. */
export function Rail({ children }: { children: React.ReactNode }) {
  return (
    <div className="egg-rail">
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" style={{ flexShrink: 0 }}>
        <path d="M4 6a4 4 0 0 1 8 0v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" fill="currentColor" opacity=".9" />
        <rect x="5.2" y="6.6" width="5.6" height="1.5" fill="var(--ink)" />
        <path d="M8 2c2-2 4-1 3.4 1.2" stroke="var(--video)" strokeWidth="1.4" fill="none" strokeLinecap="round" />
      </svg>
      <span className="egg-brand">lancelot</span>
      <span className="egg-sep">/</span>
      {children}
    </div>
  );
}

export function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button type="button" className="iconbtn egg-close" onClick={onClose} aria-label="Close">
      <Icon name="close" size={14} />
    </button>
  );
}
