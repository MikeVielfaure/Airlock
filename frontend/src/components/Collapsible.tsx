import { useState, type ReactNode } from "react";

interface Props {
  /** Same content a `.side-head`/`h4` title would hold — a numbered badge,
   * an icon, plain text. This just makes that header clickable. */
  title: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * A section that can be tucked away instead of always taking its full
 * height — the sidebar's "import a config" block, a dense admin table,
 * anything that's useful but not what most sessions need open by default.
 * No animation library: React mounts/unmounts the body, which is enough
 * for a sidebar-scale disclosure and keeps this dependency-free.
 */
export function Collapsible({ title, defaultOpen = true, children, className = "" }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapsible ${className}`}>
      <button type="button" className="collapsible-head" onClick={() => setOpen((o) => !o)}
              aria-expanded={open}>
        <span className={`collapsible-chevron ${open ? "open" : ""}`}>▸</span>
        {title}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}
