// Small inline icon set — no icon library, keeps the bundle lean.
import type { JSX } from "react";

type P = { size?: number; className?: string };
const s = (n = 16) => ({ width: n, height: n, viewBox: "0 0 24 24", fill: "none" });
const stroke = { stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const IconLayers = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M12 3 3 8l9 5 9-5-9-5Z" {...stroke} /><path d="m3 13 9 5 9-5M3 18l9 5 9-5" {...stroke} /></svg>
);
export const IconUpload = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M12 16V4m0 0L7 9m5-5 5 5" {...stroke} /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" {...stroke} /></svg>
);
export const IconTable = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><rect x="3" y="4" width="18" height="16" rx="2" {...stroke} /><path d="M3 10h18M3 15h18M9 4v16M15 4v16" {...stroke} /></svg>
);
export const IconList = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" {...stroke} /></svg>
);
export const IconCode = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="m16 18 6-6-6-6M8 6l-6 6 6 6" {...stroke} /></svg>
);
export const IconCheck = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="m20 6-11 11-5-5" {...stroke} /></svg>
);
export const IconPlay = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M6 4v16l13-8L6 4Z" {...stroke} fill="currentColor" /></svg>
);
export const IconCopy = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><rect x="9" y="9" width="11" height="11" rx="2" {...stroke} /><path d="M5 15V5a2 2 0 0 1 2-2h10" {...stroke} /></svg>
);
export const IconDownload = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M12 4v12m0 0 4-4m-4 4-4-4" {...stroke} /><path d="M4 18v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1" {...stroke} /></svg>
);
export const IconReset = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" {...stroke} /><path d="M3 4v4h4" {...stroke} /></svg>
);
export const IconWarn = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M12 3 2 20h20L12 3Z" {...stroke} /><path d="M12 10v4m0 3h.01" {...stroke} /></svg>
);
export const IconGrid = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><rect x="3" y="3" width="7" height="7" rx="1.5" {...stroke} /><rect x="14" y="3" width="7" height="7" rx="1.5" {...stroke} /><rect x="3" y="14" width="7" height="7" rx="1.5" {...stroke} /><rect x="14" y="14" width="7" height="7" rx="1.5" {...stroke} /></svg>
);
export const IconEdit = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M12 20h9" {...stroke} /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" {...stroke} /></svg>
);
export const IconSave = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" {...stroke} /><path d="M17 21v-8H7v8M7 3v5h8" {...stroke} /></svg>
);
export const IconMaximize = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M3 16v3a2 2 0 0 0 2 2h3" {...stroke} /></svg>
);
export const IconInfo = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><circle cx="12" cy="12" r="9" {...stroke} /><path d="M12 11v6" {...stroke} /><circle cx="12" cy="7.5" r="1" fill="currentColor" stroke="none" /></svg>
);
export const IconMinimize = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><path d="M9 3v3a2 2 0 0 1-2 2H4M15 3v3a2 2 0 0 0 2 2h3M21 15h-3a2 2 0 0 0-2 2v3M3 15h3a2 2 0 0 1 2 2v3" {...stroke} /></svg>
);
export const IconSearch = ({ size, className }: P): JSX.Element => (
  <svg {...s(size)} className={className}><circle cx="11" cy="11" r="7" {...stroke} /><path d="m21 21-4.3-4.3" {...stroke} /></svg>
);
