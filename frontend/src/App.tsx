import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, setEnvironment, setToken } from "./lib/api";
import { canOpenTab } from "./lib/tabs";
import {
  defaultField, defaultHeader,
  type ComputedColumn, type StyleRule,
  type FieldConfig, type FieldType, type HeaderConfig, type Presets,
  type EnvProfile,
  type FileResponse, type RowsMutationResponse,
  type ProcessResponse, type ReportRow, type TablePreview, type TcoResponse,
} from "./lib/types";
import { Sidebar } from "./components/Sidebar";
import { SchemaPanel } from "./components/SchemaPanel";
import { DataTable } from "./components/DataTable";
import { ReportPanel } from "./components/ReportPanel";
import { YamlPanel } from "./components/YamlPanel";
import { ComputedPanel } from "./components/ComputedPanel";
import { FlowsPanel } from "./components/FlowsPanel";
import { EdiPanel } from "./components/EdiPanel";
import { DatasetPanel } from "./components/DatasetPanel";
import { MappingPanel } from "./components/MappingPanel";
import { FlowCanvas } from "./components/FlowCanvas";
import { FunctionsPanel } from "./components/FunctionsPanel";
import { OpsPanel } from "./components/OpsPanel";
import { TcoFixPanel } from "./components/TcoFixPanel";
import { LoginGate } from "./components/LoginGate";
import { Home } from "./components/Home";
import { AdminPanel } from "./components/AdminPanel";
import { SourceSelector } from "./components/SourceSelector";
import { IconGrid, IconTable, IconList, IconCode, IconLayers, IconPlay } from "./lib/icons";

/** Modules that never touch a file, and so deserve the whole width. */
const FULL_WIDTH = new Set(["home", "admin", "ops", "canvas", "flows", "functions"]);

type Tab = "schema" | "computed" | "data" | "report" | "yaml" | "flows" | "edi" | "datasets" | "mapping" | "canvas" | "functions" | "ops" | "tco" | "admin" | "home";
type Toast = { id: number; msg: string; kind: "ok" | "err" | "info" };

export default function App() {
  const [presets, setPresets] = useState<Presets | null>(null);

  // source options (applied at next upload)
  const [fileType, setFileType] = useState("CSV");
  const [encoding, setEncoding] = useState("AUTO");
  const [delimiterKey, setDelimiterKey] = useState("semicolon (;)");

  // session
  const [sid, setSid] = useState<string | null>(null);
  const [loadedName, setLoadedName] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ encoding: string; delimiter: string } | null>(null);
  const [sheet, setSheet] = useState<string | null>(null);     // chosen Excel sheet
  const [sheets, setSheets] = useState<string[]>([]);          // available Excel sheets
  const [tableFilters, setTableFilters] = useState<Record<string, string>>({});  // per-column filters (by displayed name)
  const [strictHeader, setStrictHeaderRaw] = useState(false);  // require exact header match vs config
  const [minHeader, setMinHeaderRaw] = useState(false);        // require at least config's columns, extra tolerated
  // mutually exclusive: the two modes express contradictory intents
  const setStrictHeader = useCallback((v: boolean) => {
    setStrictHeaderRaw(v);
    if (v) setMinHeaderRaw(false);
  }, []);
  const setMinHeader = useCallback((v: boolean) => {
    setMinHeaderRaw(v);
    if (v) setStrictHeaderRaw(false);
  }, []);
  const [configVariables, setConfigVariables] = useState<Record<string, string>>({});  // named values for expressions
  const [refVariables, setRefVariables] = useState<string[]>([]);  // référentiel variables, resolved server-side by name
  const [tableMarker, setTableMarker] = useState("");          // Excel multi-table: row marker
  const [tableIndex, setTableIndex] = useState(0);             // which table (0-based)
  const [tableHeaderMode, setTableHeaderMode] = useState<"local" | "global">("local");
  const [tableCount, setTableCount] = useState(0);            // tables detected

  // data
  const [preview, setPreview] = useState<TablePreview | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [visible, setVisible] = useState<string[]>([]);
  const [unmapped, setUnmapped] = useState<string[]>([]);   // file cols absent from imported config
  const [configYaml, setConfigYaml] = useState<string | null>(null);  // active imported config (drives visibility)
  const [configFields, setConfigFields] = useState<Record<string, FieldConfig>>({});  // config field defs, keyed by their name
  const [unmatchedConfig, setUnmatchedConfig] = useState<FieldConfig[]>([]);  // config fields that matched no column
  const [header, setHeader] = useState<HeaderConfig>(defaultHeader());
  const [fields, setFields] = useState<Record<string, FieldConfig>>({});
  const [tco, setTco] = useState<TcoResponse | null>(null);
  const [computed, setComputed] = useState<ComputedColumn[]>([]);
  const [sqlComputed, setSqlComputed] = useState<ComputedColumn[]>([]);
  const [styleRules, setStyleRules] = useState<StyleRule[]>([]);
  const [result, setResult] = useState<ProcessResponse | null>(null);

  const [tab, setTab] = useState<Tab>(() => {
    try {
      const h = window.location.hash.replace("#", "");
      return (h ? (h as Tab) : "home");
    } catch { return "schema"; }
  });
  const [yaml, setYaml] = useState("");
  const [yamlGen, setYamlGen] = useState(false);
  const [running, setRunning] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((msg: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  const rawFileRef = useRef<File | null>(null);   // last uploaded file (for sheet re-load)
  const [deletedTotal, setDeletedTotal] = useState(0);
  // The environment's profile — needed early for the tab cap (openNewTab's
  // callers check profile.max_open_tabs), populated later by the env-switch
  // effect further down; declared here only so callbacks defined below can
  // depend on it without a temporal-dead-zone error.
  const [profile, setProfile] = useState<EnvProfile | null>(null);

  // ── multiple open tabs ────────────────────────────────────────────
  // One session is "live" at a time (the state above, exactly as before
  // tabs existed); every OTHER open tab keeps its state in this cache
  // instead. Config/computed/style rules are never persisted server-side
  // (confirmed: they only ever travel to /process on demand), so switching
  // tabs cannot just re-fetch from the backend — it has to be a real
  // client-side snapshot, restored on the way back.
  type TabSnapshot = {
    sid: string | null; loadedName: string | null;
    meta: { encoding: string; delimiter: string } | null;
    sheet: string | null; sheets: string[]; tableCount: number;
    tableMarker: string; tableIndex: number; tableHeaderMode: "local" | "global";
    preview: TablePreview | null; columns: string[]; visible: string[]; unmapped: string[];
    configYaml: string | null; configFields: Record<string, FieldConfig>;
    unmatchedConfig: FieldConfig[]; header: HeaderConfig; fields: Record<string, FieldConfig>;
    tco: TcoResponse | null; computed: ComputedColumn[]; sqlComputed: ComputedColumn[];
    styleRules: StyleRule[]; tableFilters: Record<string, string>;
    configVariables: Record<string, string>; refVariables: string[];
    strictHeader: boolean; minHeader: boolean; result: ProcessResponse | null;
    tab: Tab; deletedTotal: number; rawFile: File | null;
  };

  const [tabs, setTabs] = useState<{ sid: string; label: string }[]>([]);
  const [renamingSid, setRenamingSid] = useState<string | null>(null);
  const tabStash = useRef<Record<string, TabSnapshot>>({});

  const snapshotCurrent = useCallback((): TabSnapshot => ({
    sid, loadedName, meta, sheet, sheets, tableCount, tableMarker, tableIndex, tableHeaderMode,
    preview, columns, visible, unmapped, configYaml, configFields, unmatchedConfig, header, fields,
    tco, computed, sqlComputed, styleRules, tableFilters, configVariables, refVariables,
    strictHeader, minHeader, result, tab, deletedTotal, rawFile: rawFileRef.current,
  }), [sid, loadedName, meta, sheet, sheets, tableCount, tableMarker, tableIndex, tableHeaderMode,
      preview, columns, visible, unmapped, configYaml, configFields, unmatchedConfig, header, fields,
      tco, computed, sqlComputed, styleRules, tableFilters, configVariables, refVariables,
      strictHeader, minHeader, result, tab, deletedTotal]);

  const restoreSnapshot = useCallback((snap: TabSnapshot) => {
    rawFileRef.current = snap.rawFile;
    setSid(snap.sid); setLoadedName(snap.loadedName); setMeta(snap.meta);
    setSheet(snap.sheet); setSheets(snap.sheets); setTableCount(snap.tableCount);
    setTableMarker(snap.tableMarker); setTableIndex(snap.tableIndex); setTableHeaderMode(snap.tableHeaderMode);
    setPreview(snap.preview); setColumns(snap.columns); setVisible(snap.visible); setUnmapped(snap.unmapped);
    setConfigYaml(snap.configYaml); setConfigFields(snap.configFields); setUnmatchedConfig(snap.unmatchedConfig);
    setHeader(snap.header); setFields(snap.fields); setTco(snap.tco);
    setComputed(snap.computed); setSqlComputed(snap.sqlComputed); setStyleRules(snap.styleRules);
    setTableFilters(snap.tableFilters); setConfigVariables(snap.configVariables); setRefVariables(snap.refVariables);
    setStrictHeader(snap.strictHeader); setMinHeader(snap.minHeader); setResult(snap.result);
    setTab(snap.tab); setDeletedTotal(snap.deletedTotal);
  }, []);

  /** Register a brand-new session as its own tab. Stashes whatever was
   * live before, but never restores anything — the caller (adoptSession,
   * onUpload for a genuinely new file) sets fresh values right after this,
   * exactly as it did before tabs existed. */
  const openNewTab = useCallback((newSid: string, label: string) => {
    if (sid) tabStash.current[sid] = snapshotCurrent();
    setTabs((ts) => ts.some((t) => t.sid === newSid) ? ts : [...ts, { sid: newSid, label }]);
  }, [sid, snapshotCurrent]);

  /** Rebuild a minimal live view straight from the server — the fallback for
   * a tab whose in-memory snapshot is gone (a page reload wiped `tabStash`,
   * a `useRef`, while the session itself survives server-side). Only
   * structure/preview come back; config, computed columns and style rules
   * were never persisted server-side in the first place (confirmed reading
   * `session.py`), so this is a fresh look at the table, not an undo. */
  const restoreTabFromServer = useCallback(async (targetSid: string, label: string) => {
    try {
      const prev = await api.rowsPreview(targetSid);
      if (sid) tabStash.current[sid] = snapshotCurrent();
      rawFileRef.current = null;
      setSid(targetSid); setLoadedName(label); setMeta(null);
      setSheet(null); setSheets([]); setTableCount(0);
      setTableMarker(""); setTableIndex(0); setTableHeaderMode("local");
      setPreview(prev); setColumns(prev.columns); setVisible(prev.columns); setUnmapped([]);
      setConfigYaml(null); setConfigFields({}); setUnmatchedConfig([]);
      setHeader(defaultHeader());
      setFields(Object.fromEntries(prev.columns.map((c) => [c, defaultField(c)])));
      setTco(null); setComputed([]); setSqlComputed([]); setStyleRules([]);
      setTableFilters({}); setConfigVariables({}); setRefVariables([]);
      setStrictHeader(false); setMinHeader(false); setResult(null);
      setTab("data"); setDeletedTotal(0);
      return true;
    } catch {
      // The session expired or was dropped server-side (TTL, or someone
      // closed it elsewhere) — the tab it belonged to cannot be reopened.
      setTabs((ts) => ts.filter((t) => t.sid !== targetSid));
      delete tabStash.current[targetSid];
      return false;
    }
  }, [sid, snapshotCurrent]);

  /** Switch to an already-open tab: stash the outgoing one, restore the
   * target from its in-memory snapshot — or, if there isn't one (a reload
   * emptied the cache), fetch a minimal live view from the server. */
  const switchToTab = useCallback((targetSid: string) => {
    if (targetSid === sid) return;
    const snap = tabStash.current[targetSid];
    if (snap) {
      if (sid) tabStash.current[sid] = snapshotCurrent();
      restoreSnapshot(snap);
      delete tabStash.current[targetSid];
      return;
    }
    const label = tabs.find((t) => t.sid === targetSid)?.label ?? targetSid;
    restoreTabFromServer(targetSid, label);
  }, [sid, snapshotCurrent, restoreSnapshot, tabs, restoreTabFromServer]);

  /** Stash the live tab (still listed in `tabs`, recoverable by clicking it)
   * and fall back to the source picker — the only way to start a genuinely
   * new tab once one is already loaded, since the picker itself only
   * renders while no file is active. */
  const newTab = useCallback(() => {
    if (sid) tabStash.current[sid] = snapshotCurrent();
    rawFileRef.current = null;
    setSid(null); setLoadedName(null); setMeta(null);
    setSheet(null); setSheets([]); setTableCount(0);
    setTableMarker(""); setTableIndex(0); setTableHeaderMode("local");
    setPreview(null); setColumns([]); setVisible([]); setUnmapped([]);
    setConfigYaml(null); setConfigFields({}); setUnmatchedConfig([]);
    setHeader(defaultHeader()); setFields({}); setTco(null);
    setComputed([]); setSqlComputed([]); setStyleRules([]);
    setTableFilters({}); setConfigVariables({}); setRefVariables([]);
    setStrictHeader(false); setMinHeader(false); setResult(null);
    setTab("schema"); setDeletedTotal(0);
  }, [sid, snapshotCurrent]);

  /** A tab's label starts as the file/table name, but that name is rarely
   * the one you'd recognise a week later across five open tabs — so it can
   * be renamed freely, independent of what was actually loaded. */
  const renameTab = useCallback((targetSid: string, label: string) => {
    const trimmed = label.trim();
    if (!trimmed) return;
    setTabs((ts) => ts.map((t) => (t.sid === targetSid ? { ...t, label: trimmed } : t)));
  }, []);

  // Survive a page reload: the tab bar itself is cheap to keep in
  // sessionStorage (sid + label only), even though each tab's in-progress
  // edits are not — those were never durable across a reload anyway (see
  // `restoreTabFromServer`). Read once on mount, before anything can
  // overwrite the key with the fresh-boot empty state.
  const tabsHydrated = useRef(false);
  useEffect(() => {
    if (tabsHydrated.current) return;
    tabsHydrated.current = true;
    try {
      const raw = sessionStorage.getItem("fx_tabs");
      if (!raw) return;
      const parsed = JSON.parse(raw) as { tabs?: { sid: string; label: string }[]; activeSid?: string | null };
      if (Array.isArray(parsed.tabs) && parsed.tabs.length) {
        setTabs(parsed.tabs);
        if (parsed.activeSid) {
          const label = parsed.tabs.find((t) => t.sid === parsed.activeSid)?.label ?? parsed.activeSid;
          restoreTabFromServer(parsed.activeSid, label);
        }
      }
    } catch { /* corrupt or unavailable sessionStorage: start empty, as before */ }
  }, [restoreTabFromServer]);

  useEffect(() => {
    try { sessionStorage.setItem("fx_tabs", JSON.stringify({ tabs, activeSid: sid })); }
    catch { /* sessionStorage unavailable (private mode) — tabs just won't survive a reload */ }
  }, [tabs, sid]);

  useEffect(() => { api.presets().then(setPresets).catch(() => toast("Impossible de joindre l'API.", "err")); }, [toast]);

  // Match a YAML config against a set of columns: rebuild fields from defaults,
  // overlay matched ones, and restrict visibility to the config's fields.
  // `applyMeta` also restores header/type/encoding (skipped when re-matching
  // after a structure change, to avoid clobbering the user's header choices).
  const matchConfig = useCallback(async (text: string, cols: string[], applyMeta: boolean) => {
    const res = await api.importYaml(text, cols.length ? cols : null);
    const fc = res.file_config;
    // Keep all config field definitions, keyed by their intended name — used for
    // rename suggestions and rule inheritance in the editor.
    const cf: Record<string, FieldConfig> = {};
    (fc.Fields ?? []).forEach((f) => {
      const label = f.mapping || (f.name && f.name[0]);
      if (label) cf[label] = { ...defaultField(label), ...f };
    });
    setConfigFields(cf);
    if (applyMeta) {
      if (fc.header) setHeader({ ...defaultHeader(), ...fc.header });
      if (fc.type) setFileType(fc.type);
      if (fc.encoding) setEncoding(fc.encoding);
      setTableFilters(fc.filters ?? {});       // restore saved filters
      setStrictHeader(Boolean(fc.strict_header));
      setMinHeader(Boolean(fc.min_header));
      setConfigVariables(fc.variables ?? {});
      setRefVariables(fc.ref_variables ?? []);
      setTableMarker(fc.table_marker ?? "");
      setTableIndex(fc.table_index ?? 0);
      setTableHeaderMode((fc.table_header_mode as "local" | "global") ?? "local");
    }
    if (res.match) {
      const base: Record<string, FieldConfig> = Object.fromEntries(cols.map((c) => [c, defaultField(c)]));
      Object.entries(res.match.matched).forEach(([col, f]) => {
        base[col] = { ...defaultField(col), ...f, name: [col] };
      });
      setFields(base);
      const matchedCols = Object.keys(res.match.matched);
      setVisible(cols.filter((c) => matchedCols.includes(c)));   // only config fields active
      setUnmapped(res.match.unused);
      setUnmatchedConfig(res.match.unmatched ?? []);
      return { matched: matchedCols.length, sheet: fc.sheet ?? null,
               marker: fc.table_marker ?? "", index: fc.table_index ?? 0,
               headerMode: (fc.table_header_mode as "local" | "global") ?? "local" };
    }
    // No file to match against yet: the config's own fields are the best
    // available picture, so the YAML tab (and everything else reading
    // `fields`/`visible`) shows the loaded configuration right away instead
    // of looking empty until a file arrives to match against.
    setFields(cf);
    setVisible(Object.keys(cf));
    setUnmapped([]);
    setUnmatchedConfig([]);
    return { matched: 0, sheet: fc.sheet ?? null,
             marker: fc.table_marker ?? "", index: fc.table_index ?? 0,
             headerMode: (fc.table_header_mode as "local" | "global") ?? "local" };
  }, []);

  // ── upload ───────────────────────────────────────────────
  const onUpload = useCallback(async (file: File, over?: { sheet?: string; marker?: string; index?: number; headerMode?: "local" | "global" }) => {
    try {
      const delim = presets?.delimiters?.[delimiterKey];
      const sheetToUse = over?.sheet !== undefined ? over.sheet : (sheet ?? "");
      const markerToUse = over?.marker !== undefined ? over.marker : tableMarker;
      const indexToUse = over?.index !== undefined ? over.index : tableIndex;
      const modeToUse = over?.headerMode !== undefined ? over.headerMode : tableHeaderMode;
      const res = await api.upload(file, {
        type: fileType,
        encoding,
        delimiter: fileType === "CSV" ? (delim ?? "AUTO") : "AUTO",
        sheet: sheetToUse || undefined,
        tableMarker: markerToUse || undefined,
        tableIndex: indexToUse,
        tableHeaderMode: modeToUse,
      });
      // `over` means this call is correcting the sheet/table slice of the
      // ALREADY-open tab (self-recursion below, or a re-applied config) —
      // never a new tab. Only a bare call (the user picking a file) opens one.
      if (!over) {
        if (!canOpenTab(profile?.max_open_tabs ?? 0, tabs.length)) {
          toast(`Limite de ${profile?.max_open_tabs} onglet(s) ouvert(s) atteinte pour cet environnement.`, "err");
          api.dropSession(res.session_id).catch(() => {});
          return;
        }
        openNewTab(res.session_id, file.name);
      }
      rawFileRef.current = file;
      setSid(res.session_id);
      setLoadedName(file.name);
      setMeta({ encoding: res.encoding, delimiter: res.delimiter });
      setSheets(res.sheets ?? []);
      setSheet(res.sheet ?? null);
      setTableCount(res.table_count ?? 0);
      setTableMarker(markerToUse); setTableIndex(indexToUse); setTableHeaderMode(modeToUse);
      setPreview(res.preview);
      const cols = res.preview.columns;
      setColumns(cols);
      setVisible(cols);
      setUnmapped([]);
      setConfigFields({});
      setUnmatchedConfig([]);
      setComputed([]); setSqlComputed([]); setStyleRules([]);
      setTableFilters({});
      setConfigVariables({}); setRefVariables([]);
      setStrictHeader(false);
      setMinHeader(false);
      setDeletedTotal(0);
      setFields(Object.fromEntries(cols.map((c) => [c, defaultField(c)])));
      setResult(null);
      setTab("schema");
      toast(`Chargé — ${cols.length} colonne(s), ${res.preview.total_rows} ligne(s)${res.sheet ? ` (feuille « ${res.sheet} »)` : ""}${res.table_count ? `, ${res.table_count} table(s)` : ""}.`, "ok");
      if (configYaml) {                        // an active config — re-apply to the new file
        const r = await matchConfig(configYaml, cols, true);
        const sheetDiff = r.sheet && r.sheet !== res.sheet && (res.sheets ?? []).includes(r.sheet);
        const tableDiff = (r.marker || "") !== (markerToUse || "") ||
          (r.marker && (r.index !== indexToUse || r.headerMode !== modeToUse));
        if (sheetDiff || tableDiff) {
          await onUpload(file, { sheet: r.sheet ?? sheetToUse, marker: r.marker ?? "", index: r.index ?? 0, headerMode: r.headerMode ?? "local" });
          return;
        }
      }
    } catch (e) { toast(String((e as Error).message), "err"); }
  }, [presets, delimiterKey, fileType, encoding, sheet, tableMarker, tableIndex, tableHeaderMode, configYaml, matchConfig, toast, openNewTab, profile, tabs]);

  /**
   * Adopt a session created server-side by another route — today the EDI pivot.
   * The flattened table becomes the working file, so the whole cleaning and
   * validation machinery applies to it unchanged.
   */
  const adoptSession = useCallback((res: FileResponse, label = "edi-pivot.csv") => {
    if (!canOpenTab(profile?.max_open_tabs ?? 0, tabs.length)) {
      toast(`Limite de ${profile?.max_open_tabs} onglet(s) ouvert(s) atteinte pour cet environnement.`, "err");
      api.dropSession(res.session_id).catch(() => {});
      return;
    }
    openNewTab(res.session_id, label);       // stash whatever tab was live, register this one
    rawFileRef.current = null;               // no local raw file behind this one
    setSid(res.session_id);
    setLoadedName(label);
    setMeta({ encoding: res.encoding, delimiter: res.delimiter });
    setSheets([]); setSheet(null); setTableCount(0);
    setTableMarker(""); setTableIndex(0); setTableHeaderMode("local");
    setPreview(res.preview);
    const cols = res.preview.columns;
    setColumns(cols); setVisible(cols);
    setUnmapped([]); setConfigFields({}); setUnmatchedConfig([]);
    setComputed([]); setSqlComputed([]); setStyleRules([]);
    setTableFilters({}); setConfigVariables({}); setRefVariables([]);
    setStrictHeader(false);
    setMinHeader(false);
    setConfigYaml(null);
    setHeader(defaultHeader());
    setDeletedTotal(0);
    // A session seeded from a config carries its rules; otherwise start plain.
    setFields(res.seeded_fields && Object.keys(res.seeded_fields).length
      ? Object.fromEntries(cols.map((c) => [c, res.seeded_fields![c] ?? defaultField(c)]))
      : Object.fromEntries(cols.map((c) => [c, defaultField(c)])));
    setResult(null);
    setTab("schema");
  }, [openNewTab, profile, tabs, toast]);

  /**
   * Load a report that did not come from running validation just now — either
   * re-imported from a JSON export, or pulled from a stored flow run. Report
   * only ever reads `report`/`tco_uncovered` off `result` (confirmed in
   * ReportPanel), so every other field here is a harmless empty placeholder.
   */
  const loadReport = useCallback((report: ReportRow[],
                                  tcoUncovered: Record<string, { value: string; count: number }[]> = {}) => {
    setResult({
      columns: [], data: [], status: [], computed: [], compute_errors: {},
      stats: { total_rows: 0, rows_err: 0, rows_clean: 0, per_col: {} },
      report, tco_uncovered: tcoUncovered, warnings: [], index: [],
    });
    setTab("report");
  }, []);

  /** Start a session from a schema instead of a file: columns alone, or seeded
   *  from a library artefact. The data is typed by hand afterwards in Data. */
  const startBlank = useCallback(async (
    body: { columns?: string[]; artefact_id?: string; rows?: number },
    label: string,
  ) => {
    try {
      const res = await api.createBlankSession(body);
      adoptSession(res, label);
      toast(body.artefact_id ? "Session démarrée depuis le schéma." : "Session vierge prête.", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Impossible de démarrer la session.", "err");
    }
  }, [adoptSession, toast]);

  /** Start a session directly from an external SQL query or an API call,
   *  reusing a connection declared in the référentiel — same "server built
   *  a session, activate it" shape as startBlank. */
  const startExternalDb = useCallback(async (
    connection: string, query: string, params: Record<string, string>,
    schemaName: string | undefined, label: string,
  ) => {
    try {
      const res = await api.createSessionFromExternalDb(connection, query, params, schemaName);
      adoptSession(res, label);
      toast("Session démarrée depuis la source SQL externe.", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Impossible de démarrer la session.", "err");
    }
  }, [adoptSession, toast]);

  const startApi = useCallback(async (
    connection: string, path: string, method: string, responseKind: string,
    dataPath: string, schemaName: string | undefined, label: string,
  ) => {
    try {
      const res = await api.createSessionFromApi(connection, path, method, responseKind, dataPath, schemaName);
      adoptSession(res, label);
      toast("Session démarrée depuis la source API.", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Impossible de démarrer la session.", "err");
    }
  }, [adoptSession, toast]);

  const changeSheet = useCallback((name: string) => {
    if (rawFileRef.current) onUpload(rawFileRef.current, { sheet: name });
  }, [onUpload]);

  const applyTableSettings = useCallback((over: { marker?: string; index?: number; headerMode?: "local" | "global" }) => {
    if (rawFileRef.current) onUpload(rawFileRef.current, over);
  }, [onUpload]);

  // ── header ───────────────────────────────────────────────
  const applyHeader = useCallback(async () => {
    if (!sid) return;
    try {
      const pv = await api.applyHeader(sid, header);
      setPreview(pv);
      const newCols = pv.columns;
      setColumns(newCols);
      setResult(null);
      if (configYaml) {
        // Structure can rename/drop columns; re-match the active config against
        // the NEW columns so the configured fields stay the active ones.
        await matchConfig(configYaml, newCols, false);
      } else {
        // No config: keep only previously-active columns that still exist.
        // (We do NOT auto-activate renamed columns — that ballooned to "all".)
        setVisible((prev) => newCols.filter((c) => prev.includes(c)));
        setFields((prev) => Object.fromEntries(newCols.map((c) => [c, prev[c] ?? defaultField(c)])));
      }
      toast("Structure appliquée.", "ok");
    } catch (e) { toast(String((e as Error).message), "err"); }
  }, [sid, header, configYaml, matchConfig, toast]);

  const setField = useCallback((col: string, patch: Partial<FieldConfig>) => {
    setFields((prev) => ({ ...prev, [col]: { ...prev[col], ...patch } }));
  }, []);

  // Declare a column not present in the loaded file (e.g. one per month). It is
  // saved in the config and applies to any future file that has it.
  const addColumn = useCallback((name: string) => {
    setFields((prev) => (prev[name] ? prev : { ...prev, [name]: defaultField(name) }));
    setVisible((prev) => (prev.includes(name) ? prev : [...prev, name]));
    toast(`Colonne « ${name} » déclarée.`, "ok");
  }, [toast]);

  const removeColumn = useCallback((name: string) => {
    setFields((prev) => { const n = { ...prev }; delete n[name]; return n; });
    setVisible((prev) => prev.filter((c) => c !== name));
  }, []);

  // Link an unmatched config field to a file column: the column inherits the
  // config field's rules, is renamed to it, activated, and both leave the
  // "orphan" lists.
  const assignConfigField = useCallback((col: string, field: FieldConfig) => {
    const label = field.mapping || (field.name && field.name[0]) || col;
    setFields((prev) => ({ ...prev, [col]: { ...field, name: [col], mapping: label, rename_output: true } }));
    setVisible((prev) => (prev.includes(col) ? prev : [...prev, col]));
    setUnmapped((prev) => prev.filter((c) => c !== col));
    setUnmatchedConfig((prev) => prev.filter((f) => (f.mapping || (f.name && f.name[0])) !== label));
    toast(`« ${col} » liée au champ de config « ${label} ».`, "ok");
  }, [toast]);

  const resetFields = useCallback(() => {
    setFields(Object.fromEntries(columns.map((c) => [c, defaultField(c)])));
    setResult(null);
    toast("Règles de champ réinitialisées.", "info");
  }, [columns, toast]);

  // ── tco ──────────────────────────────────────────────────
  const onTco = useCallback(async (file: File) => {
    if (!sid) { toast("Charge d'abord un fichier.", "err"); return; }
    try {
      const res = await api.uploadTco(sid, file, ";");
      setTco(res);
      toast(`TCO chargé : ${res.labels.length} libellé(s).`, "ok");
    } catch (e) { toast(String((e as Error).message), "err"); }
  }, [sid, toast]);

  /** Attach a TCO from the library by reference (an artefact this
   * environment owns or was granted read access to) — always its latest
   * version, so an admin's later edit reaches the next session that loads
   * it without anyone re-uploading a file. */
  const onTcoFromArtefact = useCallback(async (artefactId: string) => {
    if (!sid) { toast("Charge d'abord un fichier.", "err"); return; }
    try {
      const res = await api.attachTcoFromArtefact(sid, artefactId);
      setTco(res);
      toast(`TCO chargé depuis la bibliothèque : ${res.labels.length} libellé(s).`, "ok");
    } catch (e) { toast(e instanceof Error ? e.message : String(e), "err"); }
  }, [sid, toast]);

  // ── run validation ───────────────────────────────────────
  const onRun = useCallback(async () => {
    if (!sid) return;
    setRunning(true);
    try {
      const idField = visible.find((c) => fields[c]?.identifiant) ?? null;
      const visFields = Object.fromEntries(visible.filter((c) => fields[c]).map((c) => [c, fields[c]]));
      const validComputed = computed.filter((c) => c.name.trim() && c.expression.trim());
      const validSqlComputed = sqlComputed.filter((c) => c.name.trim() && c.expression.trim());
      const validStyleRules = styleRules.filter((r) => r.column.trim() && r.expression.trim());
      const res = await api.process(sid, { visible_cols: visible, fields: visFields, identifier_field: idField, computed: validComputed, sql_computed: validSqlComputed, style_rules: validStyleRules, variables: configVariables, ref_variables: refVariables });
      setResult(res);
      setTab("data");
      (res.warnings ?? []).forEach((w) => toast(w, "info"));
      const ce = Object.keys(res.compute_errors ?? {});
      if (ce.length) toast(`Colonne(s) calculée(s) en erreur : ${ce.join(", ")}.`, "err");
      else toast(`Validé — ${res.stats.rows_err} ligne(s) en erreur, ${res.stats.rows_clean} nettoyée(s).`, res.stats.rows_err ? "info" : "ok");
    } catch (e) { toast(String((e as Error).message), "err"); }
    finally { setRunning(false); }
  }, [sid, visible, fields, computed, sqlComputed, styleRules, configVariables, refVariables, toast]);

  // ── yaml import ──────────────────────────────────────────
  const onImportYaml = useCallback(async (text: string) => {
    try {
      setConfigYaml(text);                     // becomes the active config for the session
      if (columns.length) {
        const r = await matchConfig(text, columns, true);
        // If the config targets another sheet or a different table slicing, reload.
        const sheetDiff = r.sheet && r.sheet !== sheet && sheets.includes(r.sheet);
        const tableDiff = (r.marker || "") !== (tableMarker || "") ||
          (r.marker && (r.index !== tableIndex || r.headerMode !== tableHeaderMode));
        if ((sheetDiff || tableDiff) && rawFileRef.current) {
          await onUpload(rawFileRef.current, { sheet: r.sheet ?? sheet ?? undefined, marker: r.marker ?? "", index: r.index ?? 0, headerMode: r.headerMode ?? "local" });
        }
        toast(`Config appliquée — ${r.matched} champ(s) actif(s).`, "ok");
      } else {
        // No file yet — remember sheet/table for the next upload, apply config then.
        await matchConfig(text, [], true);
        toast("Config enregistrée — elle s'appliquera automatiquement au chargement d'un fichier.", "info");
      }
    } catch (e) { toast(String((e as Error).message), "err"); }
  }, [columns, sheet, sheets, tableMarker, tableIndex, tableHeaderMode, matchConfig, onUpload, toast]);

  // Clear everything without reloading the page.
  const resetAll = useCallback(() => {
    // "Everything" includes every other open tab, not just the live one —
    // free them server-side too rather than leaving them for the TTL sweep.
    tabs.forEach((t) => api.dropSession(t.sid).catch(() => {}));
    if (sid && !tabs.some((t) => t.sid === sid)) api.dropSession(sid).catch(() => {});
    setTabs([]); tabStash.current = {};
    rawFileRef.current = null;
    setSid(null); setLoadedName(null); setMeta(null);
    setSheet(null); setSheets([]);
    setPreview(null); setColumns([]); setVisible([]); setUnmapped([]);
    setFields({}); setComputed([]); setSqlComputed([]); setStyleRules([]); setResult(null);
    setTco(null); setConfigYaml(null); setConfigFields({}); setUnmatchedConfig([]);
    setTableFilters({}); setHeader(defaultHeader()); setTab("schema");
    setConfigVariables({}); setRefVariables([]); setStrictHeader(false); setMinHeader(false);
    setTableMarker(""); setTableIndex(0); setTableHeaderMode("local"); setTableCount(0);
    setDeletedTotal(0);
    toast("Session réinitialisée — tout a été effacé.", "ok");
  }, [toast, tabs, sid]);

  const closeTab = useCallback((targetSid: string) => {
    api.dropSession(targetSid).catch(() => {});   // best-effort — the TTL sweep would catch it anyway
    delete tabStash.current[targetSid];
    const remaining = tabs.filter((t) => t.sid !== targetSid);
    setTabs(remaining);
    if (targetSid !== sid) return;                // closing a background tab: nothing live to change
    const next = remaining[remaining.length - 1];
    if (!next) { resetAll(); return; }
    const snap = tabStash.current[next.sid];
    if (snap) { restoreSnapshot(snap); delete tabStash.current[next.sid]; }
    else { restoreTabFromServer(next.sid, next.label); }
  }, [tabs, sid, restoreSnapshot, resetAll, restoreTabFromServer]);

  // ── yaml export (lazy, when on yaml tab) ─────────────────
  useEffect(() => {
    if (tab !== "yaml") return;
    setYamlGen(true);
    const delim = presets?.delimiters?.[delimiterKey] ?? ";";
    api.exportYaml({
      type: fileType,
      encoding: encoding === "AUTO" ? null : encoding,
      delimiter: delim ?? ";",
      sheet: sheet ?? null,
      strict_header: strictHeader,
      min_header: minHeader,
      variables: configVariables,
      ref_variables: refVariables,
      table_marker: tableMarker || null,
      table_index: tableIndex,
      table_header_mode: tableHeaderMode,
      header,
      fields: Object.fromEntries(visible.filter((c) => fields[c]).map((c) => [c, fields[c]])),
      visible_cols: visible,
      filters: tableFilters,
    }).then((r) => setYaml(r.yaml)).catch((e) => toast(String((e as Error).message), "err"))
      .finally(() => setYamlGen(false));
  }, [tab, fileType, encoding, delimiterKey, header, fields, visible, presets, sheet, tableFilters, strictHeader, minHeader, configVariables, refVariables, tableMarker, tableIndex, tableHeaderMode, toast]);

  const copyYaml = useCallback(() => {
    navigator.clipboard.writeText(yaml).then(() => toast("YAML copié.", "ok"));
  }, [yaml, toast]);

  const errCount = result?.stats.rows_err ?? 0;
  const hasFile = Boolean(sid && preview);

  // Map displayed column name (post-rename, when shown) -> type, for sorting.
  const fieldTypes = useMemo<Record<string, FieldType>>(() => {
    const m: Record<string, FieldType> = {};
    Object.entries(fields).forEach(([col, f]) => {
      const name = (f.mapping && f.rename_output) ? f.mapping : col;
      m[name] = f.type;
    });
    return m;
  }, [fields]);

  // Displayed column -> SOURCE column in work_df, for the editable table.
  // Identity for every real file column first, so a rename that collided (and
  // was therefore skipped by the backend) never hijacks another column's edits.
  // Computed columns are intentionally absent -> read-only.
  const srcOf = useMemo<Record<string, string>>(() => {
    const m: Record<string, string> = {};
    columns.forEach((c) => { m[c] = c; });
    Object.entries(fields).forEach(([col, f]) => {
      const fin = f.mapping && f.rename_output && f.mapping !== col ? f.mapping : col;
      if (!(fin in m)) m[fin] = col;
    });
    return m;
  }, [fields, columns]);

  const [envs, setEnvs] = useState<string[]>(["default"]);
  /**
   * The environment and the tab live in the URL.
   *
   * Not decoration: an administrator wants to hand someone the address of a
   * place ("/?env=rh#report"), not a sequence of clicks to reproduce. Reading it
   * at start-up and writing it on change is enough — no router needed, and the
   * back button keeps working.
   */
  const [env, setEnv] = useState(() => {
    try { return new URLSearchParams(window.location.search).get("env") || "default"; }
    catch { return "default"; }
  });

  // Switching environment re-scopes every library call. Panels reload their
  // own lists on mount, so changing it here is enough — no prop drilling.
  const [me, setMe] = useState<import("./lib/types").AuthUser | null>(null);
  const [gateDone, setGateDone] = useState(false);

  // Keep the address in step with where we are.
  useEffect(() => {
    try {
      const u = new URL(window.location.href);
      if (env && env !== "default") u.searchParams.set("env", env);
      else u.searchParams.delete("env");
      u.hash = tab;
      window.history.replaceState(null, "", u.toString());
    } catch { /* a browser without history: the app still works */ }
  }, [env, tab]);

  useEffect(() => {
    setEnvironment(env);
    // The profile decides what this environment exposes. Loading it on every
    // switch is what makes a locked environment locked rather than merely
    // documented.
    api.envProfile(env).then(setProfile).catch(() => setProfile(null));
  }, [env]);

  /**
   * A module is shown when the profile lists it (no profile = everything).
   *
   * Applied through `gate()` below rather than inline, because the first version
   * of this checked one tab out of thirteen and the whole feature was
   * decorative. One helper, used for every optional tab.
   */
  const shows = useCallback((m: string) =>
    !profile || profile.modules.length === 0 || profile.modules.includes(m),
    [profile]);

  /**
   * What this person may actually do here.
   *
   * Offering a gesture someone cannot complete is worse than hiding it: they
   * spend the effort, then meet a refusal. An operator working against an
   * imposed configuration has no business being shown "import a YAML".
   */
  const may = useCallback((capability: string) => {
    const caps = me?.capabilities?.[env];
    return !caps || caps.includes(capability);
  }, [me, env]);
  const configImposed = Boolean(profile?.config_locked);

  /** Render a tab button only when the environment exposes that module. */
  const gate = useCallback((m: string, node: React.ReactNode) =>
    (shows(m) ? node : null), [shows]);

  // A pinned configuration is fetched and applied: the operator validates
  // against it and cannot alter it.
  useEffect(() => {
    if (!profile?.config_artefact_id || !sid) return;
    api.getArtefactVersion("config", profile.config_artefact_id,
                           profile.config_version_no ?? 0)
      .then((v) => {
        const y = (v as { body?: { yaml?: string } } & { yaml?: string });
        const text = y.yaml ?? y.body?.yaml ?? "";
        if (text) matchConfig(text, columns, true).catch(() => {});
      })
      .catch(() => {});
  }, [profile?.config_artefact_id, sid]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Only the environments this person belongs to. A superadmin still sees
    // them all, because they are a member of all.
    api.listEnvironments()
      .then((r) => setEnvs(me && !me.is_superadmin && Object.keys(me.environments).length
        ? r.environments.filter((e) => e in me.environments)
        : r.environments))
      .catch(() => {});
  }, []);

  /**
   * Rows were added or deleted server-side. The preview is rebuilt from the
   * active table (deletions excluded) and the last run is dropped: its colors
   * describe a table that no longer exists.
   */
  const onRowsChanged = useCallback(async (st?: RowsMutationResponse) => {
    if (!sid) return;
    setPreview(await api.rowsPreview(sid));
    setResult(null);
    if (st) setDeletedTotal(st.deleted_total);
  }, [sid]);

  const onResetEdits = useCallback(async () => {
    if (!sid) return;
    const tp = await api.resetCells(sid);
    setPreview(tp);
    setResult(null);
    setDeletedTotal(0);
    toast("Toutes les modifications manuelles annulées — fichier restauré.", "info");
  }, [sid, toast]);

  // Effective column names available inside computed expressions: renamed where
  // a rename is applied, original otherwise, plus the computed columns themselves.
  const effectiveColumns = useMemo<string[]>(() => {
    const names = columns.map((c) => {
      const f = fields[c];
      return (f?.mapping && f.rename_output) ? f.mapping : c;
    });
    const declared = Object.keys(fields).filter((k) => !columns.includes(k));   // declared (not in file)
    return [...names, ...declared, ...computed.map((c) => c.name).filter(Boolean)];
  }, [columns, fields, computed]);

  const badges = useMemo(() => {
    if (!hasFile) return null;
    return (
      <div className="badges">
        {loadedName && <span className="badge"><b>{loadedName}</b></span>}
        <span className="badge">Colonnes <b>{columns.length}</b></span>
        <span className="badge">Lignes <b>{(preview?.total_rows ?? 0).toLocaleString()}</b></span>
        {meta && fileType === "CSV" && <span className="badge">Enc. <b className="mono">{meta.encoding}</b></span>}
        {meta && fileType === "CSV" && <span className="badge">Délim. <b className="mono">{meta.delimiter}</b></span>}
        {tco && <span className="badge">TCO <b>{tco.labels.length}</b></span>}
      </div>
    );
  }, [hasFile, loadedName, columns, preview, meta, fileType, tco]);

  if (!gateDone) {
    return <LoginGate onReady={(u) => { setMe(u); setGateDone(true); }} />;
  }

  /**
   * One navigation, always in the same place, whatever tab is active and
   * whether a file is loaded — a previous version had eight buttons on the
   * empty state and a separate tab bar elsewhere, and it read as two apps.
   * Every optional tab goes through `gate()`, no exceptions: a tool absent
   * from the profile must stay absent everywhere, not just on one screen.
   */
  const tabsNav = (
    <nav className="tabs">
      {gate("schema", (
        <button className={`tab ${tab === "schema" ? "active" : ""}`} onClick={() => setTab("schema")}>
          <IconList size={15} /> Schéma & Règles {visible.length > 0 && <span className="count">{visible.length}</span>}
        </button>
      ))}
      {gate("computed", (
        <button className={`tab ${tab === "computed" ? "active" : ""}`} onClick={() => setTab("computed")}>
          <IconCode size={15} /> Calculs {computed.length > 0 && <span className="count">{computed.length}</span>}
        </button>
      ))}
      <button className={`tab ${tab === "data" ? "active" : ""}`} onClick={() => setTab("data")}>
        <IconTable size={15} /> Données
        {errCount > 0 && <span className="count err">{errCount}</span>}
      </button>
      <button className={`tab ${tab === "report" ? "active" : ""}`} onClick={() => setTab("report")}>
        <IconLayers size={15} /> Rapport
        {result && <span className="count">{result.report.length}</span>}
      </button>
      {gate("yaml", (
        <button className={`tab ${tab === "yaml" ? "active" : ""}`} onClick={() => setTab("yaml")}>
          <IconCode size={15} /> Configuration
        </button>
      ))}
      {profile?.tco_artefact_id !== undefined && shows("tco") && (
        <button className={`tab ${tab === "tco" ? "active" : ""}`} onClick={() => setTab("tco")}>
          <IconTable size={15} /> Correspondances
        </button>
      )}
      {gate("datasets", (
        <button className={`tab ${tab === "datasets" ? "active" : ""}`} onClick={() => setTab("datasets")}>
          <IconTable size={15} /> Tables BDD
        </button>
      ))}
      {gate("mapping", (
        <button className={`tab ${tab === "mapping" ? "active" : ""}`} onClick={() => setTab("mapping")}>
          <IconCode size={15} /> Mapping
        </button>
      ))}
      {gate("flows", (
        <button className={`tab ${tab === "flows" ? "active" : ""}`} onClick={() => setTab("flows")}>
          <IconLayers size={15} /> Flux
        </button>
      ))}
      {gate("edi", (
        <button className={`tab ${tab === "edi" ? "active" : ""}`} onClick={() => setTab("edi")}>
          <IconGrid size={15} /> EDIFACT
        </button>
      ))}
      {gate("canvas", (
        <button className={`tab ${tab === "canvas" ? "active" : ""}`} onClick={() => setTab("canvas")}>
          <IconLayers size={15} /> Studio Flux
        </button>
      ))}
      {gate("functions", (
        <button className={`tab ${tab === "functions" ? "active" : ""}`} onClick={() => setTab("functions")}>
          <IconCode size={15} /> Fonctions
        </button>
      ))}
      {gate("ops", (
        <button className={`tab ${tab === "ops" ? "active" : ""}`} onClick={() => setTab("ops")}>
          <IconPlay size={15} /> Exploitation
        </button>
      ))}
      {(me?.is_superadmin || me?.setup_mode) && (
        <button className={`tab ${tab === "admin" ? "active" : ""}`} onClick={() => setTab("admin")}>
          <IconLayers size={15} /> Administration
        </button>
      )}
    </nav>
  );

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand" onClick={() => setTab("home")} style={{ cursor: "pointer" }}
             title="Retour à l'accueil">
          <span className="brand-mark"><IconGrid size={17} /></span>
          <div>
            <div className="brand-name">File Explorer</div>
            <div className="brand-sub">atelier de schéma · csv / xlsx</div>
          </div>
        </div>
        {badges}
        {me && (
          <span className="whoami" title={me.is_superadmin
            ? "Administrateur général" : Object.entries(me.environments)
              .map(([e, r]) => `${e}: ${r}`).join(" · ")}>
            {me.display_name || me.email}
            <button className="whoami-out" title="Se déconnecter"
                    onClick={async () => {
                      try { await api.logout(); } catch { /* already gone */ }
                      setToken(""); setMe(null); setGateDone(false);
                    }}>↩</button>
          </span>
        )}
        <select className="envpick" value={env} onChange={(e) => setEnv(e.target.value)}
                title="Environnement — délimite les configs, correspondances, flux, fonctions et tables">
          {envs.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
      </header>
      {tabs.length > 0 && (
        <div className="session-tabs">
          {tabs.map((t) => (
            <div key={t.sid} className={`session-tab ${t.sid === sid ? "active" : ""}`}>
              {renamingSid === t.sid ? (
                <input className="session-tab-rename" autoFocus defaultValue={t.label}
                       onBlur={(e) => { renameTab(t.sid, e.target.value); setRenamingSid(null); }}
                       onKeyDown={(e) => {
                         if (e.key === "Enter") e.currentTarget.blur();
                         else if (e.key === "Escape") setRenamingSid(null);
                       }} />
              ) : (
                <button className="session-tab-label" onClick={() => switchToTab(t.sid)}
                        onDoubleClick={(e) => { e.stopPropagation(); setRenamingSid(t.sid); }}
                        title={`${t.label} — double-clic pour renommer`}>
                  {t.label}
                </button>
              )}
              <button className="session-tab-close" title="Fermer cet onglet"
                      onClick={(e) => { e.stopPropagation(); closeTab(t.sid); }}>×</button>
            </div>
          ))}
          <button className="session-tab-new" onClick={newTab}
                  disabled={!canOpenTab(profile?.max_open_tabs ?? 0, tabs.length)}
                  title={canOpenTab(profile?.max_open_tabs ?? 0, tabs.length)
                    ? "Ouvrir un nouvel onglet"
                    : `Limite de ${profile?.max_open_tabs} onglet(s) atteinte pour cet environnement`}>+</button>
        </div>
      )}
      {tabsNav}

      <div className={`body ${FULL_WIDTH.has(tab) || sidebarCollapsed ? "wide" : ""}`}>
        {!FULL_WIDTH.has(tab) && <Sidebar
          presets={presets}
          fileType={fileType} setFileType={setFileType}
          encoding={encoding} setEncoding={setEncoding}
          delimiterKey={delimiterKey} setDelimiterKey={setDelimiterKey}
          onUpload={onUpload} loadedName={loadedName}
          sid={sid} onTco={onTco} onTcoFromArtefact={onTcoFromArtefact} tco={tco}
          onImportYaml={onImportYaml}
          sheets={sheets} sheet={sheet} onSheetChange={changeSheet}
          tableMarker={tableMarker} tableIndex={tableIndex} tableHeaderMode={tableHeaderMode}
          tableCount={tableCount} onTableChange={applyTableSettings}
          isExcel={fileType === "XLSX"}
          hasFile={hasFile} onReset={resetAll}
          canChooseConfig={!configImposed && may("config.write")}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        />}

        <main className="main">
          {!hasFile ? (
            tab !== "schema" && tab !== "data" && tab !== "tco" ? (
              <>
              {me?.impersonated_by && (
                <div className="borrowbar">
                  <strong>Vous voyez l'application comme {me.email}</strong>
                  <span>identité empruntée par {me.impersonated_by} · expire dans 1 h</span>
                  <button className="btn sm" onClick={async () => {
                    try { await api.logout(); } catch { /* already gone */ }
                    setToken(""); window.location.reload();
                  }}>Reprendre mon identité</button>
                </div>
              )}
              {profile && (profile.config_locked || profile.actions.length > 0) && (
                <div className="envbar">
                  <strong>{profile.label}</strong>
                  {profile.config_locked && (
                    <span className="envbar-lock">
                      configuration imposée — le fichier est contrôlé contre elle
                    </span>
                  )}
                  {profile.actions.map((a, i) => (
                    <button key={i} className="btn sm" onClick={async () => {
                      if (a.confirm && !window.confirm(`${a.label} ?`)) return;
                      try {
                        const r = await api.callFlow(a.graph_id, a.params ?? {});
                        toast(`${a.label} — ${r.count} ligne(s).`, "ok");
                      } catch (e) { toast(e instanceof Error ? e.message : String(e), "err"); }
                    }}>{a.label}</button>
                  ))}
                </div>
              )}
                <div className="panel">
                  {tab === "home" ? (
                    <Home me={me} env={env} profile={profile} shows={shows}
                          go={(k) => setTab(k as Tab)} />
                  )
                    : tab === "flows" ? <FlowsPanel notify={toast} onOpenReport={loadReport} />
                    : tab === "admin" ? <AdminPanel me={me} notify={toast}
                                                     onIdentityChange={() => window.location.reload()} />
                    : tab === "ops" ? <OpsPanel notify={toast} />
                    : tab === "canvas" ? <FlowCanvas notify={toast}
                        onOpenSession={(res) => { adoptSession(res, "flux"); setTab("schema"); }} />
                    : tab === "functions" ? <FunctionsPanel notify={toast} />
                    : tab === "report" ? <ReportPanel result={result} sid={sid} notify={toast} onLoadReport={loadReport} />
                    : tab === "computed" ? (
                        <ComputedPanel sid={sid} tabs={tabs} notify={toast} columns={effectiveColumns} computed={computed}
                          setComputed={setComputed} sqlComputed={sqlComputed} setSqlComputed={setSqlComputed}
                          styleRules={styleRules} setStyleRules={setStyleRules}
                          variables={configVariables}
                          setVariables={setConfigVariables}
                          refVariables={refVariables} setRefVariables={setRefVariables}
                          errors={result?.compute_errors ?? {}}
                          styleErrors={result?.style_errors ?? {}} />
                      )
                    : tab === "yaml" ? (
                        <YamlPanel yaml={yaml} generating={yamlGen} onCopy={copyYaml}
                          onImportYaml={onImportYaml} notify={toast} />
                      )
                    : tab === "datasets" ? <DatasetPanel sid={null} columns={[]}
                        identifiers={[]} hasRun={false} sourceName="" notify={toast}
                        onOpenSession={(res) => { adoptSession(res, "table"); setTab("data"); }} />
                    : tab === "mapping" ? <MappingPanel notify={toast} sessionId={null}
                        sessionColumns={[]} />
                    : <EdiPanel notify={toast} onSession={adoptSession} />}
                </div>
              </>
            ) : (
              <SourceSelector
                presets={presets}
                fileType={fileType} setFileType={setFileType}
                encoding={encoding} setEncoding={setEncoding}
                delimiterKey={delimiterKey} setDelimiterKey={setDelimiterKey}
                onUpload={onUpload}
                onStartBlank={startBlank}
                onStartExternalDb={startExternalDb}
                onStartApi={startApi}
                onGoTab={(t) => setTab(t as Tab)}
                canChooseConfig={!configImposed && may("config.write")}
              />
            )
          ) : (
            <>
              <div className="view">
                {tab === "home" && (
                  <Home me={me} env={env} profile={profile} shows={shows}
                        go={(k) => setTab(k as Tab)} />
                )}
                {tab === "schema" && presets && (
                  <SchemaPanel
                    columns={columns} visible={visible} setVisible={setVisible}
                    unmapped={unmapped}
                    header={header} setHeader={setHeader} applyHeader={applyHeader}
                    fields={fields} setField={setField} resetFields={resetFields}
                    addColumn={addColumn} removeColumn={removeColumn}
                    presets={presets} tcoLabels={tco?.labels ?? []} stats={result?.stats ?? null}
                    configFields={configFields}
                    unmatchedConfig={unmatchedConfig} assignConfigField={assignConfigField}
                    strictHeader={strictHeader} setStrictHeader={setStrictHeader}
                    minHeader={minHeader} setMinHeader={setMinHeader}
                  />
                )}
                {tab === "computed" && (
                  <ComputedPanel sid={sid} tabs={tabs} notify={toast} columns={effectiveColumns} computed={computed} setComputed={setComputed}
                    sqlComputed={sqlComputed} setSqlComputed={setSqlComputed}
                    styleRules={styleRules} setStyleRules={setStyleRules}
                    variables={configVariables} setVariables={setConfigVariables}
                    refVariables={refVariables} setRefVariables={setRefVariables}
                    errors={result?.compute_errors ?? {}} styleErrors={result?.style_errors ?? {}} />
                )}
                {tab === "data" && (
                  <DataTable preview={preview} result={result} fieldTypes={fieldTypes}
                    onRun={onRun} running={running} canRun={visible.length > 0}
                    sid={sid}
                    defaultName={(loadedName ?? "export").replace(/\.[^.]+$/, "")}
                    originEncoding={meta?.encoding ?? "utf-8"}
                    originDelimiter={meta?.delimiter ?? ";"}
                    filters={tableFilters} setFilters={setTableFilters}
                    srcOf={srcOf} notify={toast} onResetEdits={onResetEdits}
                    onRowsChanged={onRowsChanged} deletedTotal={deletedTotal} />
                )}
                {tab === "report" && <ReportPanel result={result} sid={sid} notify={toast} onLoadReport={loadReport} />}
                {tab === "flows" && <FlowsPanel notify={toast} onOpenReport={loadReport} />}
                {tab === "edi" && <EdiPanel notify={toast} onSession={adoptSession} />}
                {tab === "canvas" && <FlowCanvas notify={toast}
                    onOpenSession={(res) => { adoptSession(res, "flux"); setTab("schema"); }} />}
                {tab === "functions" && <FunctionsPanel notify={toast} />}
                {tab === "ops" && <OpsPanel notify={toast} />}
                {tab === "admin" && (
                  <AdminPanel me={me} notify={toast}
                              onIdentityChange={() => window.location.reload()} />
                )}
                {tab === "tco" && (
                  <TcoFixPanel
                    result={result}
                    fieldTypes={Object.fromEntries(visible.map((c) =>
                      [c, fields[c]?.tco_type || fields[c]?.mapping || c]))}
                    tcoArtefactId={tco?.artefact_id || profile?.tco_artefact_id || ""}
                    editable={profile?.tco_editable !== false}
                    notify={toast} sid={sid} columns={result?.columns ?? visible}
                    onTcoFromArtefact={onTcoFromArtefact} />
                )}
                {tab === "mapping" && (
                  <MappingPanel notify={toast} sessionId={sid}
                                sessionColumns={result?.columns ?? visible} />
                )}
                {tab === "datasets" && (
                  <DatasetPanel
                    sid={sid} columns={result?.columns ?? visible}
                    identifiers={visible.filter((c) => fields[c]?.identifiant)}
                    hasRun={Boolean(result)} sourceName={loadedName ?? ""}
                    notify={toast} onFixRows={() => setTab("data")}
                    onOpenSession={(res) => { adoptSession(res, "table"); setTab("data"); }} />
                )}
                {tab === "yaml" && <YamlPanel yaml={yaml} generating={yamlGen} onCopy={copyYaml} onImportYaml={onImportYaml} notify={toast} />}
              </div>
            </>
          )}
        </main>
      </div>

      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>)}
      </div>
    </div>
  );
}
