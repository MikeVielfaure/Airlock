import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import type { ArtefactInfo, CellStatus, FieldType, ProcessResponse, RowsMutationResponse, TablePreview } from "../lib/types";
import { api, downloadBase64 } from "../lib/api";
import { IconMaximize, IconMinimize, IconPlay, IconReset, IconSave } from "../lib/icons";
import { InfoTip } from "./InfoTip";
import { ModelPicker, useModelRef } from "./EdiModelPicker";

interface Props {
  preview: TablePreview | null;
  result: ProcessResponse | null;
  fieldTypes: Record<string, FieldType>;
  onRun: () => void;
  running: boolean;
  canRun: boolean;
  sid: string | null;
  defaultName: string;
  originEncoding: string;
  originDelimiter: string;
  filters: Record<string, string>;            // per-column filters, keyed by displayed name
  setFilters: (f: Record<string, string>) => void;
  srcOf: Record<string, string>;              // displayed column -> SOURCE column (editable set)
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  onResetEdits: () => Promise<void> | void;   // discard all edits server-side
  onRowsChanged: (st?: RowsMutationResponse) => Promise<void> | void;  // reload after row ops
  deletedTotal: number;                       // logically removed rows, restorable
}

const ROW_H = 34;
const COL_W = 160;
const OVERSCAN = 8;

const ENCODINGS = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"];
const DELIMS: Record<string, string> = { ";": "semicolon ;", ",": "comma ,", "\t": "tab \\t", "|": "pipe |" };

type Sort = { col: number; dir: 1 | -1 } | null;

function compare(a: string, b: string, type: FieldType | undefined): number {
  const ea = a.trim() === "", eb = b.trim() === "";
  if (ea && eb) return 0;
  if (ea) return 1;
  if (eb) return -1;
  if (type === "integer" || type === "float") {
    const na = parseFloat(a.replace(",", ".").replace(/\s/g, ""));
    const nb = parseFloat(b.replace(",", ".").replace(/\s/g, ""));
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
  }
  if (type === "date") {
    const da = Date.parse(a), db = Date.parse(b);
    if (!isNaN(da) && !isNaN(db)) return da - db;
  }
  return a.localeCompare(b, undefined, { numeric: true });
}

// Filter operators, mirroring the backend _filter_mask (used in preview mode).
function matchFilter(value: string, expr: string): boolean {
  const s = (expr ?? "").trim();
  if (s === "") return true;
  const v = value ?? "";
  const low = v.toLowerCase();
  const sl = s.toLowerCase();
  if (["empty", "blank", "null", "isnull", "is null", "(empty)"].includes(sl)) return v.trim() === "";
  if (["!empty", "!blank", "!null", "notempty", "notblank", "notnull", "not null", "not empty", "not blank", "isnotnull"].includes(sl)) return v.trim() !== "";
  for (const op of [">=", "<=", ">", "<"]) {
    if (s.startsWith(op)) {
      const num = parseFloat(s.slice(op.length).trim().replace(",", "."));
      if (isNaN(num)) break;
      const cn = parseFloat(v.replace(",", ".").replace(/\s/g, ""));
      if (isNaN(cn)) return false;
      return op === ">=" ? cn >= num : op === "<=" ? cn <= num : op === ">" ? cn > num : cn < num;
    }
  }
  if (sl.startsWith("!in:")) { const it = s.slice(4).split(",").map((x) => x.trim().toLowerCase()).filter(Boolean); return !it.includes(low); }
  if (sl.startsWith("in:")) { const it = s.slice(3).split(",").map((x) => x.trim().toLowerCase()).filter(Boolean); return it.includes(low); }
  if (s.startsWith("!=")) return low !== s.slice(2).trim().toLowerCase();
  if (s.startsWith("=")) return low === s.slice(1).trim().toLowerCase();
  if (s.startsWith("!")) return !low.includes(s.slice(1).trim().toLowerCase());
  return low.includes(sl);
}

/** A conditional-formatting rule's token — either the full
 * "color:x;bold:1;italic:0" shape `STYLE()` produces, or a bare color name
 * (no `:`/`;`) a cross-source SQL rule can return without knowing the full
 * format. Never touches the background — that stays the validation status's,
 * so the two channels never fight over the same pixels. */
function parseStyleToken(token?: string): CSSProperties | undefined {
  if (!token) return undefined;
  const t = token.trim();
  if (!t) return undefined;
  if (!t.includes(":") && !t.includes(";")) {
    return { color: t };
  }
  const out: CSSProperties = {};
  for (const part of t.split(";")) {
    const [k, v] = part.split(":").map((s) => s.trim());
    if (k === "color" && v) out.color = v;
    if (k === "bold" && v === "1") out.fontWeight = "bold";
    if (k === "italic" && v === "1") out.fontStyle = "italic";
  }
  return Object.keys(out).length ? out : undefined;
}

/** Which delimiter a pasted block actually uses: whichever of tab / `;` / `,`
 * splits every non-empty line into the same (>1) number of fields, tab
 * winning ties — the standard shape a spreadsheet's own copy produces. */
function detectDelimiter(text: string): string {
  const lines = text.split(/\r\n|\n/).filter((l) => l.length > 0).slice(0, 20);
  if (lines.length === 0) return "\t";
  let best = "\t", bestScore = 0;
  for (const d of ["\t", ";", ","]) {
    const counts = lines.map((l) => l.split(d).length);
    const consistent = counts.every((c) => c === counts[0]) && counts[0] > 1;
    const score = consistent ? counts[0] : 0;
    if (score > bestScore) { bestScore = score; best = d; }
  }
  return best;
}

/** Parse a clipboard table into rows of cells using the detected delimiter. */
function parsePastedTable(text: string): string[][] {
  const delim = detectDelimiter(text);
  return text.split(/\r\n|\n/).filter((l) => l.length > 0).map((l) => l.split(delim));
}

export function DataTable(props: Props) {
  const { preview, result, fieldTypes, onRun, running, canRun, sid, defaultName, originEncoding, originDelimiter, filters, setFilters, srcOf, notify, onResetEdits, onRowsChanged, deletedTotal } = props;
  const columns = result ? result.columns : preview?.columns ?? [];
  const data = result ? result.data : preview?.data ?? [];
  const computed = new Set(result?.computed ?? []);
  const stats = result?.stats;

  const [sort, setSort] = useState<Sort>(null);
  const [order, setOrder] = useState<number[]>([]);
  const [drag, setDrag] = useState<number | null>(null);
  const [dragRow, setDragRow] = useState<number | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(440);
  const scrollRef = useRef<HTMLDivElement>(null);

  // ── fullscreen mode ────────────────────────────────────────
  // An overlay, not the browser Fullscreen API: keeps the toolbar, filters
  // and edit mode all working exactly as-is, just given the whole viewport.
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  // export form
  const validEnc = ENCODINGS.includes(originEncoding) ? originEncoding : "utf-8";
  const validDelim = originDelimiter in DELIMS ? originDelimiter : ";";
  const [name, setName] = useState(defaultName);
  const [fmt, setFmt] = useState<"csv" | "xlsx" | "pivot" | "edifact">("csv");
  const [enc, setEnc] = useState(validEnc);
  const [delim, setDelim] = useState(validDelim);
  const [onlyFiltered, setOnlyFiltered] = useState(false);
  const [includeStyle, setIncludeStyle] = useState(false);
  useEffect(() => { setName(defaultName); setEnc(validEnc); setDelim(validDelim); }, [defaultName, validEnc, validDelim]);

  // ── EDIFACT: the trip back from an already-cleaned session ──
  const [ediModels, setEdiModels] = useState<ArtefactInfo[]>([]);
  const ediModel = useModelRef(ediModels);
  const [ediGroupBy, setEdiGroupBy] = useState("");
  const [ediSender, setEdiSender] = useState("");
  const [ediRecipient, setEdiRecipient] = useState("");
  const [ediRef, setEdiRef] = useState("");
  const [ediBusy, setEdiBusy] = useState(false);
  useEffect(() => {
    if (fmt !== "edifact" || ediModels.length) return;
    api.listArtefacts("edi_model").then(setEdiModels)
      .catch((e) => notify(e instanceof Error ? e.message : "Impossible de charger les modèles EDI.", "err"));
  }, [fmt, ediModels.length, notify]);

  const doGenerateEdi = async () => {
    if (!sid) return;
    const m = ediModel.ref();
    if (!m) { notify("Choisissez d'abord un modèle EDI (bibliothèque ou YAML inline).", "err"); return; }
    setEdiBusy(true);
    try {
      const r = await api.ediGenerateFromSession(sid, m, {
        group_by: ediGroupBy, sender: ediSender, recipient: ediRecipient, interchange_ref: ediRef,
      });
      downloadBase64(r.file);
      notify(`${r.messages} message(s), ${r.items} ligne(s) générée(s).`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setEdiBusy(false); }
  };

  // ── server-side pagination (after a run) ─────────────────
  const serverMode = !!result;
  const [showOps, setShowOps] = useState(false);

  // ── editable mode ─────────────────────────────────────────
  const [editMode, setEditMode] = useState(false);
  const [editing, setEditing] = useState<{ idx: number; ci: number; val: string } | null>(null);
  // Enter/Escape/Tab already decide whether to commit — but removing the
  // input from the DOM (setEditing(null)) fires a native blur on it too, and
  // that handler exists to save on click-away. Without this flag, Escape's
  // "cancel" still re-submitted the unchanged value through onBlur.
  const skipNextBlur = useRef(false);
  // Optimistic overlay of saved edits, keyed `${dfIndex}:${displayedCol}`.
  // Cleared when a new run or a new preview arrives (the server then owns the values).
  const [pending, setPending] = useState<Record<string, string>>({});
  useEffect(() => { setPending({}); setEditing(null); setFocus(null); }, [result, preview]);
  const pendingCount = Object.keys(pending).length;
  const editableCol = (name: string) => !computed.has(name) && srcOf[name] !== undefined;

  // ── keyboard grid navigation ───────────────────────────────
  // `r` indexes into the currently rendered `rows` array (post filter/sort/
  // page), `c` into `colIdx` (post drag-reorder) — both are display-order
  // positions, not dataframe indices, so Tab/arrows follow what's on screen.
  // (moveFocus/tabMove/onGridKeyDown are defined further down, once `rows`
  // and `colIdx` exist — they close over this state.)
  const [focus, setFocus] = useState<{ r: number; c: number } | null>(null);
  const clampN = (v: number, max: number) => Math.max(0, Math.min(max, v));

  const commitEdit = async (idx: number, ci: number, val: string) => {
    const col = columns[ci];
    setEditing(null);
    if (!sid || !editableCol(col)) return;
    const key = `${idx}:${col}`;
    const prev = pending[key];
    setPending((m) => ({ ...m, [key]: val }));
    try {
      const res = await api.editCells(sid, [{ index: idx, column: srcOf[col], value: val }]);
      if (res.applied !== 1) {
        setPending((m) => { const n = { ...m }; if (prev === undefined) delete n[key]; else n[key] = prev; return n; });
        notify(res.rejected[0] ? `Edit rejected: ${res.rejected[0].reason}` : "Edit rejected", "err");
      }
    } catch (e) {
      setPending((m) => { const n = { ...m }; if (prev === undefined) delete n[key]; else n[key] = prev; return n; });
      notify(e instanceof Error ? e.message : "Échec de la modification", "err");
    }
  };

  const discardEdits = async () => {
    try { await onResetEdits(); setPending({}); setEditing(null); }
    catch (e) { notify(e instanceof Error ? e.message : "Échec de la réinitialisation", "err"); }
  };
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(100);
  const [srvSort, setSrvSort] = useState<{ col: string; dir: "asc" | "desc" } | null>(null);
  const [srv, setSrv] = useState<{ rows: string[][]; status: CellStatus[][]; styles: string[][]; total: number; totalAll: number; index: number[] } | null>(null);
  const [srvLoading, setSrvLoading] = useState(false);

  // reset paging when the result, filters, sort or page size change
  useEffect(() => { setPage(0); }, [result, filters, srvSort, pageSize]);

  useEffect(() => {
    if (!result || !sid) { setSrv(null); return; }
    let cancelled = false;
    const handle = setTimeout(async () => {
      setSrvLoading(true);
      try {
        const named = Object.fromEntries(Object.entries(filters).filter(([, v]) => v.trim() !== ""));
        const res = await api.getRows(sid, {
          offset: page * pageSize, limit: pageSize,
          filters: Object.keys(named).length ? JSON.stringify(named) : "",
          sortCol: srvSort?.col ?? "", sortDir: srvSort?.dir ?? "asc",
        });
        if (!cancelled) setSrv({ rows: res.data, status: res.status, styles: res.styles ?? [], total: res.total, totalAll: res.total_all, index: res.index ?? [] });
      } catch { /* keep previous page on error */ }
      finally { if (!cancelled) setSrvLoading(false); }
    }, 250);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [result, sid, page, pageSize, filters, srvSort]);

  useEffect(() => { setOrder(columns.map((_, i) => i)); setSort(null); }, [columns.length, result]);

  useEffect(() => {
    const el = scrollRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setViewH(el.clientHeight));
    ro.observe(el); setViewH(el.clientHeight);
    return () => ro.disconnect();
  }, [columns.length]);

  const colIdx = useMemo(
    () => (order.length === columns.length ? order : columns.map((_, i) => i)),
    [order, columns],
  );

  // Active filters as [columnIndex, rawExpression], resolved from the named map.
  const activeFilters = useMemo(
    () => Object.entries(filters)
      .filter(([, v]) => v.trim() !== "")
      .map(([nameKey, v]) => [columns.indexOf(nameKey), v] as const)
      .filter(([ci]) => ci >= 0),
    [filters, columns],
  );
  const anyFilter = activeFilters.length > 0;

  // Preview mode (before a run): client-side filter + sort over the sample.
  const previewIdx = useMemo(() => {
    if (serverMode) return [];
    let base = data.map((_, i) => i);
    if (activeFilters.length) {
      // Split into ungrouped (AND) and `:id` groups (OR within, AND across).
      const ungrouped: (readonly [number, string])[] = [];
      const groups: Record<string, (readonly [number, string])[]> = {};
      for (const [ci, raw] of activeFilters) {
        const m = /^:([A-Za-z0-9]+)\s*(.*)$/.exec(raw);
        if (m) {
          const expr = m[2].trim();
          if (expr !== "") (groups[m[1]] ||= []).push([ci, expr] as const);
        } else {
          ungrouped.push([ci, raw] as const);
        }
      }
      base = base.filter((r) =>
        ungrouped.every(([ci, q]) => matchFilter(data[r][ci] ?? "", q)) &&
        Object.values(groups).every((g) => g.some(([ci, q]) => matchFilter(data[r][ci] ?? "", q))),
      );
    }
    if (sort) {
      const t = fieldTypes[columns[sort.col]];
      base = [...base].sort((ra, rb) => sort.dir * compare(data[ra][sort.col] ?? "", data[rb][sort.col] ?? "", t));
    }
    return base;
  }, [serverMode, data, activeFilters, sort, fieldTypes, columns]);

  // Unified list of rows to render (server page, or filtered preview).
  const rows = useMemo<{ cells: string[]; status?: CellStatus[]; styles?: string[]; num: number; idx: number }[]>(() => {
    if (serverMode) {
      const base = page * pageSize;
      return (srv?.rows ?? []).map((cells, i) => ({
        cells, status: srv?.status[i], styles: srv?.styles[i], num: base + i + 1, idx: srv?.index[i] ?? -1,
      }));
    }
    return previewIdx.map((oi) => ({ cells: data[oi], num: oi + 1, idx: preview?.index?.[oi] ?? oi }));
  }, [serverMode, srv, page, pageSize, previewIdx, data, preview]);

  const moveFocus = (dr: number, dc: number) => setFocus((f) => {
    if (!f) return f;
    return { r: clampN(f.r + dr, rows.length - 1), c: clampN(f.c + dc, colIdx.length - 1) };
  });
  const tabMove = (dir: 1 | -1) => setFocus((f) => {
    if (!f) return f;
    let { r, c } = f;
    c += dir;
    if (c >= colIdx.length) { c = 0; r = clampN(r + 1, rows.length - 1); }
    else if (c < 0) { c = colIdx.length - 1; r = clampN(r - 1, rows.length - 1); }
    return { r, c };
  });
  // Focus can point past the row/column count after a filter/sort shrinks
  // the grid — pull it back in rather than pointing at nothing.
  useEffect(() => {
    setFocus((f) => {
      if (!f) return f;
      if (!rows.length || !colIdx.length) return null;
      return { r: clampN(f.r, rows.length - 1), c: clampN(f.c, colIdx.length - 1) };
    });
  }, [rows.length, colIdx.length]);
  useEffect(() => {
    const el = scrollRef.current;
    if (!focus || !el) return;
    const rowTop = focus.r * ROW_H, rowBottom = rowTop + ROW_H;
    if (rowTop < el.scrollTop) el.scrollTop = rowTop;
    else if (rowBottom > el.scrollTop + viewH) el.scrollTop = rowBottom - viewH;
    const rownumW = 64;
    const colLeft = rownumW + focus.c * COL_W, colRight = colLeft + COL_W;
    if (colLeft < el.scrollLeft + rownumW) el.scrollLeft = colLeft - rownumW;
    else if (colRight > el.scrollLeft + el.clientWidth) el.scrollLeft = colRight - el.clientWidth;
  }, [focus, viewH]);
  const cellValueOf = (row: { idx: number; cells: string[] }, ci: number) => {
    const key = `${row.idx}:${columns[ci]}`;
    return key in pending ? pending[key] : row.cells[ci] ?? "";
  };
  const onGridKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (editing || !focus) return;
    const row = rows[focus.r]; const ci = colIdx[focus.c]; const name = ci !== undefined ? columns[ci] : undefined;
    if (e.key === "ArrowDown") { e.preventDefault(); moveFocus(1, 0); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveFocus(-1, 0); }
    else if (e.key === "ArrowRight") { e.preventDefault(); moveFocus(0, 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); moveFocus(0, -1); }
    else if (e.key === "Tab") { e.preventDefault(); tabMove(e.shiftKey ? -1 : 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (editMode && row && row.idx >= 0 && name && editableCol(name)) {
        setEditing({ idx: row.idx, ci, val: cellValueOf(row, ci) });
      } else moveFocus(1, 0);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (editMode && row && row.idx >= 0 && name && editableCol(name)) {
        e.preventDefault();
        setEditing({ idx: row.idx, ci, val: e.key });
      }
    }
  };

  const total = rows.length;
  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const end = Math.min(total, Math.ceil((scrollTop + viewH) / ROW_H) + OVERSCAN);
  const gridCols = `64px repeat(${colIdx.length}, ${COL_W}px)`;

  const filteredCount = serverMode ? (srv?.total ?? 0) : previewIdx.length;
  const totalAll = serverMode ? (srv?.totalAll ?? 0) : data.length;
  const pageCount = serverMode ? Math.max(1, Math.ceil((srv?.total ?? 0) / pageSize)) : 1;
  const sortedCol = serverMode ? srvSort?.col : (sort ? columns[sort.col] : undefined);
  const sortedDir = serverMode ? srvSort?.dir : (sort ? (sort.dir === 1 ? "asc" : "desc") : undefined);

  const toggleSort = (ci: number) => {
    if (serverMode) {
      const name = columns[ci];
      setSrvSort((s) => (s?.col === name ? (s.dir === "asc" ? { col: name, dir: "desc" } : null) : { col: name, dir: "asc" }));
    } else {
      setSort((s) => (s?.col === ci ? (s.dir === 1 ? { col: ci, dir: -1 } : null) : { col: ci, dir: 1 }));
    }
  };

  const onDrop = (target: number) => {
    if (drag === null || drag === target) return setDrag(null);
    setOrder((prev) => {
      const arr = prev.length === columns.length ? [...prev] : columns.map((_, i) => i);
      arr.splice(arr.indexOf(target), 0, arr.splice(arr.indexOf(drag), 1)[0]);
      return arr;
    });
    setDrag(null);
  };

  const onDropRow = async (target: number) => {
    const from = dragRow;
    setDragRow(null);
    if (from === null || from === target || !sid) return;
    try {
      await api.reorderRow(sid, from, target);
      await onRowsChanged();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec du réordonnancement", "err"); }
  };

  const duplicateRow = async (idx: number) => {
    if (!sid) return;
    try {
      const r = await api.addRows(sid, 1, idx);
      await onRowsChanged(r);
      notify("Ligne dupliquée.", "ok");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  // Paste always appends new rows at the end — never overwrites existing
  // ones — so pasting a large block never depends on knowing which row
  // visually follows which once sorting/filtering/pagination are involved.
  const pasteIntoTable = async (startCi: number, text: string) => {
    if (!sid) return;
    const grid = parsePastedTable(text);
    if (grid.length === 0 || (grid.length === 1 && grid[0].length <= 1)) return false;
    const targetCols = colIdx.slice(colIdx.indexOf(startCi))
      .slice(0, grid[0].length)
      .map((ci) => columns[ci]);
    if (targetCols.length === 0) return false;
    try {
      const added = await api.addRows(sid, grid.length);
      const edits: { index: number; column: string; value: string }[] = [];
      grid.forEach((row, ri) => {
        const idx = added.new_indices[ri];
        if (idx === undefined) return;
        targetCols.forEach((col, ci) => {
          if (row[ci] === undefined || !editableCol(col)) return;
          edits.push({ index: idx, column: srcOf[col], value: row[ci] });
        });
      });
      if (edits.length) await api.editCells(sid, edits);
      await onRowsChanged(added);
      notify(`${grid.length} ligne(s) collée(s).`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec du collage", "err"); }
    return true;
  };

  const doExport = () => {
    if (!sid) return;
    const qs = new URLSearchParams({ fmt, encoding: enc, delimiter: delim, filename: name || "export" });
    if (fmt === "xlsx" && includeStyle) qs.set("style", "1");
    if (onlyFiltered) {
      const active = Object.fromEntries(Object.entries(filters).filter(([, v]) => v.trim() !== ""));
      if (Object.keys(active).length) qs.append("filters", JSON.stringify(active));
    }
    api.downloadExport(sid, qs.toString(), `${name || "export"}.${fmt}`)
      .catch((e) => notify(e instanceof Error ? e.message : String(e), "err"));
  };

  return (
    <div className={fullscreen ? "table-fullscreen" : undefined}>
      <div className="sec-h">
        <h3>Aperçu</h3>
        <span className="sub">
          {result ? "Validé — les cellules colorées signalent les erreurs, nettoyages et valeurs calculées." : "Valeurs brutes. Lancez la validation pour appliquer vos règles."}
        </span>
        <InfoTip>
          <p><b>À quoi ça sert</b> — voir les données, filtrées et triées, et déclencher le contrôle qui applique les règles du Schéma et les calculs.</p>
          <p><b>Comment faire</b> — « Lancer la validation » applique tout ; ensuite, filtrez sous chaque colonne, triez en cliquant un nom, glissez les en-têtes pour réordonner. « Modifier les cellules » corrige une valeur à la main.</p>
          <p><b>Ce qu'il faut</b> — un fichier chargé. Avant la première validation, seul un échantillon brut est visible.</p>
        </InfoTip>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn sm" title={fullscreen ? "Quitter le plein écran (Échap)" : "Afficher le tableau en grand"}
            onClick={() => setFullscreen((v) => !v)}>
            {fullscreen ? <IconMinimize size={14} /> : <IconMaximize size={14} />}
          </button>
          <button className="btn primary" onClick={onRun} disabled={!canRun || running}>
            <IconPlay size={15} /> {running ? "En cours…" : "Lancer la validation"}
          </button>
        </span>
      </div>

      {stats && (
        <div className="statbar">
          <div className="stat"><div className="num">{stats.total_rows.toLocaleString()}</div><div className="lbl">Lignes</div></div>
          <div className="stat err"><div className="num">{stats.rows_err.toLocaleString()}</div><div className="lbl">Lignes en erreur</div></div>
          <div className="stat clean"><div className="num">{stats.rows_clean.toLocaleString()}</div><div className="lbl">Lignes nettoyées</div></div>
          <div className="stat"><div className="num">{columns.length}</div><div className="lbl">Colonnes affichées</div></div>
        </div>
      )}

      <div className="tabletools">
        {sortedCol && <button className="btn sm" onClick={() => (serverMode ? setSrvSort(null) : setSort(null))}><IconReset size={13} /> Réinitialiser le tri</button>}
        {anyFilter && <button className="btn sm" onClick={() => setFilters({})}><IconReset size={13} /> Réinitialiser les filtres ({activeFilters.length})</button>}
        <button className="btn sm" onClick={() => setShowOps((v) => !v)} title="Afficher les opérateurs de filtre">
          {showOps ? "Masquer les opérateurs" : "Opérateurs de filtre"}
        </button>
        <button className={`btn sm ${editMode ? "active" : ""}`} title="Cliquez sur une cellule pour corriger sa valeur, puis relancez la validation"
          onClick={() => { setEditMode((v) => !v); setEditing(null); }}>
          ✎ {editMode ? "Édition activée" : "Modifier les cellules"}
        </button>
        <button className="btn sm" disabled={!sid || !result}
          title="Transformer ce que vous venez de faire en flux reproductible"
          onClick={async () => {
            if (!sid) return;
            const name = window.prompt("Nom du flux ?", "flux-manuel");
            if (!name) return;
            try {
              const r = await api.sessionToFlow(sid, { name, save: true });
              notify(r.skipped.length
                ? `Flux enregistré (${r.steps} étape(s)). Non repris : ${r.skipped.join(" ; ")}`
                : `Flux « ${name} » enregistré — ${r.steps} étape(s).`, "ok");
            } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
          }}>Enregistrer comme flux</button>
        <button className="btn sm" disabled={!sid} title="Ajouter une ligne vide en fin de table"
          onClick={async () => {
            if (!sid) return;
            try { const r = await api.addRows(sid, 1); await onRowsChanged(r); notify("Ligne ajoutée.", "ok"); }
            catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
          }}>+ ligne</button>
        {anyFilter && (
          <button className="btn sm danger" disabled={!sid || !result}
            title="Supprime toutes les lignes correspondant aux filtres actifs — réversible"
            onClick={async () => {
              if (!sid) return;
              if (!window.confirm(`Supprimer les ${filteredCount} ligne(s) filtrées ? `
                                  + `C'est réversible tant que la table n'est pas rechargée.`)) return;
              try {
                const r = await api.deleteFilteredRows(sid, filters, []);
                await onRowsChanged(r);
                notify(`${r.deleted} ligne(s) supprimée(s).`, "ok");
              } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
            }}>Supprimer le lot filtré</button>
        )}
        {deletedTotal > 0 && (
          <button className="btn sm" disabled={!sid}
            onClick={async () => {
              if (!sid) return;
              try {
                const r = await api.restoreRows(sid);
                await onRowsChanged(r);
                notify(`${r.restored} ligne(s) restaurée(s).`, "ok");
              } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
            }}><IconReset size={13} /> Restaurer {deletedTotal} supprimée(s)</button>
        )}
        <span className="toolnote">
          {anyFilter
            ? `${filteredCount.toLocaleString()} ligne(s) sur ${totalAll.toLocaleString()} correspondent${serverMode ? " (fichier entier)" : " (échantillon)"} · `
            : ""}
          {srvLoading ? "chargement… · " : ""}glissez les en-têtes pour réordonner · cliquez un nom pour trier · saisissez sous une colonne pour filtrer
        </span>
      </div>
      {(anyFilter || showOps) && (
        <div className="filterlegend">
          <div>
            <strong>Opérateurs :</strong> <code>texte</code> contient · <code>=x</code> égal à · <code>!x</code> ne contient pas ·
            <code>!=x</code> différent de · <code>in:a,b</code> · <code>!in:a,b</code> · <code>&gt;n</code> <code>&lt;n</code> <code>&gt;=n</code> <code>&lt;=n</code> · <code>null</code> / <code>notnull</code>
          </div>
          <div>
            <strong>Combiner :</strong> les colonnes différentes sont en <em>ET</em> par défaut. Préfixez avec <code>:1</code> (n'importe quel identifiant) pour mettre
            des colonnes dans le même groupe <em>OU</em> — ex. <code>:1!empty</code> sur deux colonnes correspond aux lignes où <em>l'une ou l'autre</em> est non vide.
          </div>
        </div>
      )}

      {(pendingCount > 0 || editMode) && (
        <div className="editbar">
          {pendingCount > 0 ? (
            <span><strong>{pendingCount}</strong> cellule{pendingCount > 1 ? "s" : ""} modifiée{pendingCount > 1 ? "s" : ""} — les couleurs ci-dessous sont périmées tant que vous ne relancez pas la validation.</span>
          ) : (
            <span>Mode édition : cliquez une cellule (ou déplacez-vous avec les flèches / <kbd>Tab</kbd>), tapez pour remplacer, <kbd>Entrée</kbd> pour enregistrer et descendre · <kbd>Échap</kbd> pour annuler. Les colonnes calculées sont en lecture seule.</span>
          )}
          {pendingCount > 0 && (
            <span className="editbar-actions">
              <button className="btn sm primary" onClick={onRun} disabled={running}>Relancer la validation</button>
              <button className="btn sm" onClick={discardEdits} title="Restaure le fichier tel que chargé (toutes les modifications manuelles, y compris les précédentes)">Annuler toutes les modifications</button>
            </span>
          )}
        </div>
      )}

      {result && (
        <div className="legend">
          <span><i className="swatch err" /> Erreur</span>
          <span><i className="swatch clean" /> Nettoyée / mappée</span>
          <span><i className="swatch warn" /> Sans TCO</span>
          <span><i className="swatch comp" /> Calculée</span>
          {(editMode || pendingCount > 0) && <span><i className="swatch edited" /> Modifiée (à revalider)</span>}
          <span style={{ color: "var(--ink-faint)" }}><code style={{ fontFamily: "var(--mono)" }}>null</code> = vide</span>
        </div>
      )}

      {columns.length === 0 ? (
        <div className="banner"><span>Aucune colonne à afficher. Activez des colonnes dans l'onglet Schéma.</span></div>
      ) : (
        <div className="vtable" ref={scrollRef} tabIndex={0} onKeyDown={onGridKeyDown}
          onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}>
          <div className="vthead" style={{ gridTemplateColumns: gridCols }}>
            <div className="vth rownum">
              {anyFilter && <button className="hclear" title="Réinitialiser les filtres" onClick={() => setFilters({})}>×</button>}
            </div>
            {colIdx.map((ci) => (
              <div key={ci} className={`vth ${computed.has(columns[ci]) ? "comp" : ""} ${drag === ci ? "dragging" : ""}`}>
                <div className="vth-top" draggable onDragStart={() => setDrag(ci)}
                  onDragOver={(e) => e.preventDefault()} onDrop={() => onDrop(ci)}
                  onClick={() => toggleSort(ci)} title={columns[ci]}>
                  <span className="vth-name">{columns[ci]}</span>
                  {computed.has(columns[ci]) && <span className="fx">fx</span>}
                  {sortedCol === columns[ci] && <span className="sortarrow">{sortedDir === "asc" ? "▲" : "▼"}</span>}
                </div>
                <input className="vth-filter mono-input" placeholder="filter…"
                  value={filters[columns[ci]] ?? ""} onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setFilters({ ...filters, [columns[ci]]: e.target.value })} />
              </div>
            ))}
          </div>

          <div style={{ height: start * ROW_H }} />
          {rows.slice(start, end).map((row, k) => (
            <div key={start + k} className="vtr" style={{ gridTemplateColumns: gridCols, height: ROW_H }}>
              <div className={`vtd rownum ${dragRow === row.idx ? "dragging" : ""}`}
                draggable={editMode && row.idx >= 0}
                onDragStart={() => setDragRow(row.idx)}
                onDragOver={(e) => { if (editMode) e.preventDefault(); }}
                onDrop={() => onDropRow(row.idx)}>
                {editMode && row.idx >= 0 ? (
                  <span className="rowops">
                    <button className="rowdup" title="Dupliquer cette ligne" onClick={() => duplicateRow(row.idx)}>⧉</button>
                    <button className="rowdel" title="Supprimer cette ligne (réversible)"
                      onClick={async () => {
                        if (!sid) return;
                        try {
                          const st = await api.deleteRows(sid, [row.idx]);
                          await onRowsChanged(st);
                        } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                      }}>✕</button>
                  </span>
                ) : row.num}
              </div>
              {colIdx.map((ci, c) => {
                const name = columns[ci];
                const key = `${row.idx}:${name}`;
                const edited = key in pending;
                const cell = edited ? pending[key] : row.cells[ci] ?? "";
                const st: CellStatus | undefined = edited ? "EDITED" : row.status?.[ci];
                const sty = edited ? undefined : row.styles?.[ci];
                const canEdit = editMode && row.idx >= 0 && editableCol(name);
                const r = start + k;
                const isFocused = focus?.r === r && focus?.c === c;
                const isEditing = editing && editing.idx === row.idx && editing.ci === ci;
                if (isEditing) {
                  return (
                    <div key={ci} className="vtd cell-editing">
                      <input className="celledit" autoFocus value={editing.val}
                        onChange={(e) => setEditing({ ...editing, val: e.target.value })}
                        onKeyDown={(e) => {
                          // Refocus the grid container after a keyboard-driven exit (not on
                          // blur, which fires because focus is already moving elsewhere) so
                          // arrow/Tab navigation keeps working on the next keypress — the
                          // input's autoFocus otherwise leaves nothing focused once it unmounts.
                          if (e.key === "Enter") { skipNextBlur.current = true; commitEdit(editing.idx, ci, editing.val); moveFocus(1, 0); scrollRef.current?.focus(); }
                          else if (e.key === "Escape") { skipNextBlur.current = true; setEditing(null); scrollRef.current?.focus(); }
                          else if (e.key === "Tab") { e.preventDefault(); skipNextBlur.current = true; commitEdit(editing.idx, ci, editing.val); tabMove(e.shiftKey ? -1 : 1); scrollRef.current?.focus(); }
                        }}
                        onPaste={(e) => {
                          const text = e.clipboardData.getData("text");
                          const grid = parsePastedTable(text);
                          if (grid.length > 1 || grid[0]?.length > 1) {
                            e.preventDefault();
                            skipNextBlur.current = true;
                            setEditing(null);
                            pasteIntoTable(ci, text);
                          }
                        }}
                        onBlur={() => {
                          if (skipNextBlur.current) { skipNextBlur.current = false; return; }
                          commitEdit(editing.idx, ci, editing.val);
                        }} />
                    </div>
                  );
                }
                return (
                  <div key={ci} className={`vtd ${st ? `cell-${st}` : ""} ${canEdit ? "editable" : ""} ${isFocused ? "focused" : ""}`}
                    title={canEdit ? `${cell || "(vide)"} — cliquer pour modifier` : cell}
                    style={parseStyleToken(sty)}
                    onClick={() => { setFocus({ r, c }); scrollRef.current?.focus(); if (canEdit) setEditing({ idx: row.idx, ci, val: cell }); }}>
                    {cell === "" ? <span className="nullv">null</span> : cell}
                  </div>
                );
              })}
            </div>
          ))}
          <div style={{ height: (total - end) * ROW_H }} />
        </div>
      )}

      {serverMode ? (
        <div className="pager">
          <span className="toolnote">
            {filteredCount === 0 ? "Aucune ligne" :
              `Lignes ${(page * pageSize + 1).toLocaleString()}–${(page * pageSize + rows.length).toLocaleString()} sur ${filteredCount.toLocaleString()}`}
            {anyFilter ? ` (filtré depuis ${totalAll.toLocaleString()})` : ""}
          </span>
          <span className="pager-ctrls">
            <label className="csub">Lignes par page
              <select className="mono-input" style={{ marginLeft: 6 }} value={pageSize}
                onChange={(e) => setPageSize(parseInt(e.target.value))}>
                {[50, 100, 200, 500, 1000].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <button className="btn sm" disabled={page <= 0} onClick={() => setPage(0)}>« Début</button>
            <button className="btn sm" disabled={page <= 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>‹ Préc.</button>
            <span className="csub">Page {page + 1} / {pageCount}</span>
            <button className="btn sm" disabled={page + 1 >= pageCount} onClick={() => setPage((p) => p + 1)}>Suiv. ›</button>
            <button className="btn sm" disabled={page + 1 >= pageCount} onClick={() => setPage(pageCount - 1)}>Fin »</button>
          </span>
        </div>
      ) : data.length > 0 ? (
        <div className="toolnote" style={{ marginTop: 8 }}>
          Échantillon de {data.length.toLocaleString()} ligne(s). Lancez la validation pour parcourir et filtrer le fichier entier.
        </div>
      ) : null}

      {/* export */}
      {columns.length > 0 && (
        <div className="export">
          <div className="sec-h" style={{ marginBottom: 12 }}>
            <h3>Exporter la table</h3>
            <span className="sub">{result ? "Exporte le dernier résultat validé (nettoyé + calculé)." : "Lancez la validation d'abord pour exporter les valeurs nettoyées — sinon la table de travail est exportée telle quelle."}</span>
          </div>
          <div className="export-form">
            <div className="frow"><label>Nom du fichier</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="export" /></div>
            <div className="frow"><label>Format</label>
              <select value={fmt} onChange={(e) => setFmt(e.target.value as "csv" | "xlsx" | "pivot" | "edifact")}>
                <option value="csv">CSV</option>
                <option value="xlsx">Excel (.xlsx)</option>
                <option value="pivot">Pivot (.json)</option>
                <option value="edifact">EDIFACT</option>
              </select></div>

            {fmt === "edifact" ? (
              <>
                <ModelPicker label="Modèle EDI cible" models={ediModels} ctl={ediModel} />
                <div className="frow"><label>Regrouper les lignes en messages par</label>
                  <input value={ediGroupBy} onChange={(e) => setEdiGroupBy(e.target.value)}
                         placeholder="nom de colonne — par défaut : message_no, sinon un seul message" /></div>
                <div className="frow"><label>Interchange</label>
                  <div className="edi-row">
                    <input value={ediSender} onChange={(e) => setEdiSender(e.target.value)} placeholder="expéditeur (GLN:14)" />
                    <input value={ediRecipient} onChange={(e) => setEdiRecipient(e.target.value)} placeholder="destinataire" />
                    <input value={ediRef} onChange={(e) => setEdiRef(e.target.value)} placeholder="référence" />
                  </div></div>
                <button className="btn primary" onClick={doGenerateEdi} disabled={!sid || ediBusy}>
                  <IconSave size={15} /> Générer EDI
                </button>
              </>
            ) : (
              <>
                <div className="frow"><label>Encodage</label>
                  <select value={enc} onChange={(e) => setEnc(e.target.value)} disabled={fmt !== "csv"}>
                    {ENCODINGS.map((x) => <option key={x} value={x}>{x}</option>)}
                  </select></div>
                <div className="frow"><label>Délimiteur</label>
                  <select value={delim} onChange={(e) => setDelim(e.target.value)} disabled={fmt !== "csv"}>
                    {Object.entries(DELIMS).map(([v, lbl]) => <option key={v} value={v}>{lbl}</option>)}
                  </select></div>
                <label className="check" style={{ alignSelf: "end", opacity: anyFilter ? 1 : 0.5 }}>
                  <input type="checkbox" checked={onlyFiltered} disabled={!anyFilter}
                    onChange={(e) => setOnlyFiltered(e.target.checked)} />
                  <span className="ctxt">Uniquement les lignes filtrées
                    <div className="csub">{anyFilter
                      ? "Applique vos filtres de colonne à tout le fichier, pas seulement à l'échantillon."
                      : "Définissez un filtre de colonne ci-dessus pour activer cette option."}</div>
                  </span>
                </label>
                <label className="check" style={{ alignSelf: "end", opacity: fmt === "xlsx" ? 1 : 0.5 }}>
                  <input type="checkbox" checked={includeStyle} disabled={fmt !== "xlsx"}
                    onChange={(e) => setIncludeStyle(e.target.checked)} />
                  <span className="ctxt">Inclure la mise en forme
                    <div className="csub">{fmt === "xlsx"
                      ? "Applique les règles de « Mise en forme » comme un vrai style de cellule Excel."
                      : "Disponible seulement pour l'export Excel (.xlsx)."}</div>
                  </span>
                </label>
                <button className="btn primary" onClick={doExport} disabled={!sid}>
                  <IconSave size={15} /> Exporter .{fmt}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
