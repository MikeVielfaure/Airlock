import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { IconInfo } from "../lib/icons";

/** A small "ⓘ" next to a title or a control — click to reveal what it's for,
 * how to use it, and what it needs, without permanently taking up space the
 * way an inline paragraph would. Closes on an outside click, not on a timer —
 * reading takes as long as it takes. */
export function InfoTip({ children, align = "left" }: { children: ReactNode; align?: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <span className="infotip" ref={ref}>
      <button type="button" className="infotip-btn" onClick={() => setOpen((v) => !v)}
        aria-label="Aide" title="Aide">
        <IconInfo size={13} />
      </button>
      {open && <div className={`infotip-pop ${align === "right" ? "right" : ""}`}>{children}</div>}
    </span>
  );
}
