"use client";

/** The dialog, and two phases: the tour, then the doors out of it.
 *
 *  There used to be a third — a diagnosis game where five bills arrived and you said why.
 *  It went, because it asked the player to do the job the console's own findings already do
 *  ("anything a rule can determine, a rule determines"), it changed genre halfway through a
 *  lesson that had no score, and it followed the tour's own payoff with a quiz. What sits
 *  there now is the only ending the setting actually affords: the player has just learned
 *  the vocabulary, and is standing inside the application where all of it is on screen.
 *
 *  `<dialog>` for the same reasons KioskIntro and Diagrams use one: the top layer, the
 *  backdrop, Escape and returning focus where it came from.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Tour, { CloseButton, Doors, Rail } from "./Tour";
import { CHAPTERS, type Term } from "./engine";

const TOURED_KEY = "lancescope-egg-toured";

// Private windows and blocked site data. The egg works fine without any of this.
const hasToured = () => {
  try { return localStorage.getItem(TOURED_KEY) === "1"; } catch { return false; }
};
const markToured = () => {
  try { localStorage.setItem(TOURED_KEY, "1"); } catch { /* not remembered */ }
};

export default function Egg({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  // Somebody who has already walked the seven chapters gets the doors directly. Being made
  // to sit through a tutorial twice is how a joke becomes a chore.
  const [done, setDone] = useState(() => hasToured());
  const [learned, setLearned] = useState<Term[]>([]);

  useEffect(() => {
    const d = dialogRef.current;
    if (d && !d.open) d.showModal();
  }, []);

  // Escape belongs to the dialog and a showModal() dialog closes on it by itself; handled
  // here as well because the only way out of a full-screen takeover should not depend on
  // the UA's close-watcher reaching us.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const finish = useCallback((words: Term[]) => {
    markToured(); setLearned(words); setDone(true);
  }, []);

  return (
    <dialog ref={dialogRef} className="egg-dialog"
            aria-label="Lancelot and the Roll of the Realm" onClose={onClose}>
      <div className="egg-stage">
        <CloseButton onClose={onClose} />
        <Rail>
          {done ? <span>the tour is over</span>
                : <span>the squire&rsquo;s tour &middot; {CHAPTERS.length} chapters</span>}
          {done ? (
            <button type="button" className="egg-retour"
                    onClick={() => { setLearned([]); setDone(false); }}>
              walk it again
            </button>
          ) : null}
        </Rail>

        {done ? <Doors learned={learned} onClose={onClose} />
              : <Tour onFinish={finish} />}
      </div>
    </dialog>
  );
}
