import { useCallback, useEffect, useRef, useState } from "react";
import type { AvailableVariable, ComputedColumn, DatasetInfo, SourceInfo, StyleRule, VariableSchema } from "../lib/types";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
import { IconCode, IconReset, IconUpload, IconDownload } from "../lib/icons";
import { InfoTip } from "./InfoTip";
import { SearchInput, matchesSearch } from "./SearchInput";

const fmtShortDate = (iso: string) => new Date(iso).toLocaleDateString(undefined,
  { day: "2-digit", month: "2-digit", year: "2-digit" });

interface Props {
  sid: string | null;
  tabs?: { sid: string; label: string }[];
  columns: string[];
  computed: ComputedColumn[];
  setComputed: (c: ComputedColumn[]) => void;
  sqlComputed: ComputedColumn[];
  setSqlComputed: (c: ComputedColumn[]) => void;
  styleRules: StyleRule[];
  setStyleRules: (r: StyleRule[]) => void;
  variables: Record<string, string>;
  setVariables: (v: Record<string, string>) => void;
  refVariables: string[];
  setRefVariables: (v: string[]) => void;
  errors: Record<string, string>;   // server-side errors from the last run
  styleErrors?: Record<string, string>;
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
}

const TEMPLATES: { label: string; expr: (cols: string[]) => string }[] = [
  { label: "Concaténer", expr: (c) => `CONCAT([${c[0] ?? "col1"}], " ", [${c[1] ?? "col2"}])` },
  { label: "Condition", expr: (c) => `IF([${c[0] ?? "col1"}] == "value", "yes", "no")` },
  { label: "Majuscules", expr: (c) => `UPPER([${c[0] ?? "col1"}])` },
  { label: "Valeur par défaut", expr: (c) => `DEFAULT([${c[0] ?? "col1"}], "N/A")` },
];

const FUNCS = [
  ["IF(cond, a, b)", "renvoie a si cond est vrai, sinon b"],
  ["ISNULL(x) / NOTNULL(x)", "teste si vide / null — ex. IF(ISNULL([x]), \"N/A\", [x])"],
  ["COALESCE(…) / DEFAULT(x, fb)", "première valeur non vide / valeur par défaut si x est vide"],
  ["CONCAT(…)", "assemble des valeurs et du texte"],
  ["UPPER / LOWER / TITLE / TRIM(x)", "transformations de texte"],
  ["LEFT(x, n) / RIGHT(x, n)", "n premiers / derniers caractères"],
  ["LTRIM(x, \"0\") / RTRIM(x, \"0\")", "retire les caractères en tête / en fin — ex. 0012 → 12"],
  ["REGEX_EXTRACT(x, pat, n)", "groupe n capturé par une regex — ex. \"0*([0-9]+)\" → 12"],
  ["REPLACE(x, a, b)", "remplace a par b"],
  ["NUM(x) / STR(x) / LEN(x)", "conversion / longueur"],
  ["EXISTS(\"col\") / COL(\"col\", def)", "la colonne existe-t-elle / sa valeur par son nom"],
  ["STYLE(color, bold, italic)", "pour la mise en forme conditionnelle — ex. STYLE(\"orange\", \"1\")"],
];

function Row({ col, columns, onChange, onRemove, serverError }: {
  col: ComputedColumn;
  columns: string[];
  onChange: (c: ComputedColumn) => void;
  onRemove: () => void;
  serverError?: string;
}) {
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!col.expression.trim()) { setErr(null); setOk(false); return; }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const r = await api.checkExpression(col.expression);
        setErr(r.ok ? null : r.error); setOk(r.ok);
      } catch { setErr("Validation impossible."); setOk(false); }
    }, 350);
    return () => window.clearTimeout(timer.current);
  }, [col.expression]);

  const insert = (token: string) => onChange({ ...col, expression: col.expression + token });

  return (
    <div className="computed-row">
      <div className="computed-head">
        <input className="mono-input" placeholder="nom_colonne" value={col.name}
          onChange={(e) => onChange({ ...col, name: e.target.value.replace(/\s+/g, "_") })}
          style={{ maxWidth: 220 }} />
        <span style={{ marginLeft: "auto" }}>
          {col.expression.trim() && (ok
            ? <span className="valid ok">valide</span>
            : err ? <span className="valid err">{err}</span> : <span className="valid">vérification…</span>)}
        </span>
        <button className="btn sm" onClick={onRemove} title="Retirer">✕</button>
      </div>
      <textarea className="mono-input" rows={2} placeholder='IF([civilite] == "M", "Monsieur", "Madame")'
        value={col.expression} onChange={(e) => onChange({ ...col, expression: e.target.value })} />
      {serverError && <div className="valid err" style={{ marginTop: 4 }}>Dernière exécution : {serverError}</div>}
      <div className="chipbar">
        <span className="chiplabel">insérer une colonne :</span>
        {columns.slice(0, 40).map((c) => (
          <button key={c} className="microchip" onClick={() => insert(`[${c}]`)}>{c}</button>
        ))}
        {columns.length > 40 && <span className="chiplabel">+{columns.length - 40} de plus…</span>}
      </div>
    </div>
  );
}

/** A SQL block plus an optional mapping assistant that writes the SQL text
 * for you — pick which columns identify a row and which columns to pull in,
 * and "Générer" builds the join. The SQL stays the single source of truth;
 * the assistant is a shortcut for writing it, never a second execution
 * path — press it again after tweaking the picks and it just overwrites the
 * text again. */
function SqlBlock({ col, columns, sources, serverError, onChange, onRemove }: {
  col: ComputedColumn;
  columns: string[];
  sources: SourceInfo[];
  serverError?: string;
  onChange: (c: ComputedColumn) => void;
  onRemove: () => void;
}) {
  const [showAssistant, setShowAssistant] = useState(false);
  const [sourceName, setSourceName] = useState("");
  const [idPairs, setIdPairs] = useState<{ local: string; src: string }[]>([{ local: "", src: "" }]);
  const [fillPairs, setFillPairs] = useState<{ src: string; local: string }[]>([{ src: "", local: "" }]);
  const source = sources.find((s) => s.name === sourceName);

  const generate = () => {
    const validId = idPairs.filter((p) => p.local && p.src);
    const validFill = fillPairs.filter((p) => p.src && p.local);
    if (!sourceName || !validId.length || !validFill.length) return;
    const select = ["self._row_id", ...validFill.map((p) => `${sourceName}.${p.src} AS ${p.local}`)].join(", ");
    const on = validId.map((p) => `self.${p.local} = ${sourceName}.${p.src}`).join(" AND ");
    onChange({ ...col, mode: "fill_empty",
              expression: `SELECT ${select}\nFROM self LEFT JOIN ${sourceName} ON ${on}` });
  };

  return (
    <div className="computed-row">
      <div className="computed-head">
        <input className="mono-input" placeholder="nom_du_bloc" value={col.name}
          onChange={(e) => onChange({ ...col, name: e.target.value.replace(/\s+/g, "_") })}
          style={{ maxWidth: 200 }} />
        <select className="mono-input" value={col.mode ?? "replace"}
          onChange={(e) => onChange({ ...col, mode: e.target.value as ComputedColumn["mode"] })}>
          <option value="replace">Remplacer</option>
          <option value="fill_empty">Compléter le vide</option>
        </select>
        <button className="btn sm" onClick={() => setShowAssistant((v) => !v)}>
          {showAssistant ? "Masquer l'assistant" : "Assistant"}
        </button>
        <button className="btn sm" style={{ marginLeft: "auto" }} onClick={onRemove} title="Retirer">✕</button>
      </div>

      {showAssistant && (
        <div className="sql-assistant">
          <p className="hint">
            Choisissez une source, les colonnes qui identifient une ligne (ex.
            nom + prénom) et les colonnes à en tirer — « Générer » écrit la
            requête et bascule sur « Compléter le vide ».
          </p>
          <div className="frow"><label>Source</label>
            <select value={sourceName} onChange={(e) => setSourceName(e.target.value)}>
              <option value="">— choisir —</option>
              {sources.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>

          <span className="csub">Colonnes identifiantes (locale = source)</span>
          {idPairs.map((p, i) => (
            <div className="sql-pair" key={i}>
              <select value={p.local} onChange={(e) => setIdPairs(idPairs.map((x, j) =>
                (j === i ? { ...x, local: e.target.value } : x)))}>
                <option value="">colonne locale…</option>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <span>=</span>
              <select value={p.src} disabled={!source} onChange={(e) => setIdPairs(idPairs.map((x, j) =>
                (j === i ? { ...x, src: e.target.value } : x)))}>
                <option value="">colonne source…</option>
                {(source?.columns ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <button className="hclear" onClick={() => setIdPairs(idPairs.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          <button className="btn sm" onClick={() => setIdPairs([...idPairs, { local: "", src: "" }])}>
            + identifiant
          </button>

          <span className="csub">Colonnes à compléter (source → locale)</span>
          {fillPairs.map((p, i) => (
            <div className="sql-pair" key={i}>
              <select value={p.src} disabled={!source} onChange={(e) => setFillPairs(fillPairs.map((x, j) =>
                (j === i ? { ...x, src: e.target.value } : x)))}>
                <option value="">colonne source…</option>
                {(source?.columns ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <span>→</span>
              <select value={p.local} onChange={(e) => setFillPairs(fillPairs.map((x, j) =>
                (j === i ? { ...x, local: e.target.value } : x)))}>
                <option value="">colonne locale…</option>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <button className="hclear" onClick={() => setFillPairs(fillPairs.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          <button className="btn sm" onClick={() => setFillPairs([...fillPairs, { src: "", local: "" }])}>
            + colonne
          </button>

          <div>
            <button className="btn sm primary"
              disabled={!sourceName || !idPairs.some((p) => p.local && p.src)
                       || !fillPairs.some((p) => p.src && p.local)}
              onClick={generate}>Générer la requête</button>
          </div>
        </div>
      )}

      <textarea className="mono-input" rows={4}
        placeholder="SELECT self._row_id, ext.libelle FROM self LEFT JOIN referentiel_clients ext ON self.code = ext.code"
        value={col.expression}
        onChange={(e) => onChange({ ...col, expression: e.target.value })} />
      {serverError && <div className="valid err" style={{ marginTop: 4 }}>Dernière exécution : {serverError}</div>}
    </div>
  );
}

/** How one existing column should look — a condition (plain or, starting
 * with SELECT/WITH, cross-source SQL) producing a STYLE() token, never a
 * new value. */
function StyleRuleRow({ rule, columns, serverError, onChange, onRemove }: {
  rule: StyleRule;
  columns: string[];
  serverError?: string;
  onChange: (r: StyleRule) => void;
  onRemove: () => void;
}) {
  return (
    <div className="computed-row">
      <div className="computed-head">
        <select className="mono-input" value={rule.column} style={{ maxWidth: 220 }}
          onChange={(e) => onChange({ ...rule, column: e.target.value })}>
          <option value="">— colonne —</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="btn sm" style={{ marginLeft: "auto" }} onClick={onRemove} title="Retirer">✕</button>
      </div>
      <textarea className="mono-input" rows={2}
        placeholder={'IF([age] < "18", STYLE("orange", "1"), STYLE())'}
        value={rule.expression}
        onChange={(e) => onChange({ ...rule, expression: e.target.value })} />
      {serverError && <div className="valid err" style={{ marginTop: 4 }}>Dernière exécution : {serverError}</div>}
    </div>
  );
}

type SubTab = "columns" | "sql" | "sources" | "style" | "library";

export function ComputedPanel({ sid, tabs, columns, computed, setComputed, sqlComputed, setSqlComputed,
                               styleRules, setStyleRules, variables, setVariables,
                               refVariables, setRefVariables, errors,
                               styleErrors, notify }: Props) {
  const [tab, setTab] = useState<SubTab>("columns");
  const [lib, setLib] = useState<ArtefactInfo[]>([]);
  const [libSearch, setLibSearch] = useState("");
  const [saveName, setSaveName] = useState("");
  const [saveTarget, setSaveTarget] = useState("");
  const refreshLib = useCallback(() => { api.listArtefacts("computed").then(setLib).catch(() => {}); }, []);
  useEffect(() => { refreshLib(); }, [refreshLib]);

  // ── référentiel variables — usable in an expression as [nom] or,
  // when the value is a JSON object (a connection point), [nom.champ] ──
  const [available, setAvailable] = useState<AvailableVariable[]>([]);
  useEffect(() => { api.listAvailableVariables().then(setAvailable).catch(() => {}); }, []);
  const [pickVar, setPickVar] = useState("");
  const pickableVars = available.filter((v) => !refVariables.includes(v.name));
  const addRefVariable = () => {
    if (!pickVar) return;
    setRefVariables([...refVariables, pickVar]);
    setPickVar("");
  };
  const removeRefVariable = (name: string) =>
    setRefVariables(refVariables.filter((n) => n !== name));

  // ── attached sources (for cross-source SQL) ──────────────────────
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [attachName, setAttachName] = useState("");
  const [attachDatasetId, setAttachDatasetId] = useState("");
  const [attachSessionSid, setAttachSessionSid] = useState("");
  const uploadRef = useRef<HTMLInputElement>(null);
  const otherTabs = (tabs ?? []).filter((t) => t.sid !== sid);
  const refreshSources = useCallback(() => {
    if (sid) api.listSources(sid).then(setSources).catch(() => {});
  }, [sid]);
  useEffect(() => { refreshSources(); }, [refreshSources]);
  useEffect(() => { api.listDatasets().then(setDatasets).catch(() => {}); }, []);

  // ── attach a BDD externe source ───────────────────────────────
  const [dbConn, setDbConn] = useState("");
  const [dbName, setDbName] = useState("");
  const [dbQuery, setDbQuery] = useState("");
  const [dbParams, setDbParams] = useState<{ key: string; value: string }[]>([]);
  const [dbSchemas, setDbSchemas] = useState<VariableSchema[]>([]);
  const [dbSchemaName, setDbSchemaName] = useState("");
  const dbConnections = available.filter((v) => v.kind === "external_db");

  const pickDbConn = async (name: string) => {
    setDbConn(name); setDbSchemaName(""); setDbSchemas([]);
    const v = dbConnections.find((c) => c.name === name);
    if (!v) return;
    try { setDbSchemas(await api.listConnectionSchemas(v.id)); } catch { setDbSchemas([]); }
  };
  const pickDbSchemaName = (name: string) => {
    setDbSchemaName(name);
    const sc = dbSchemas.find((s) => s.name === name);
    if (sc) {
      setDbQuery(sc.query?.trim()
        || `SELECT ${sc.columns.map((c) => c.name).join(", ") || "*"} FROM ${name}`);
    }
  };

  const attachExternalDb = async () => {
    if (!sid || !dbName.trim() || !dbConn || !dbQuery.trim()) return;
    const params = Object.fromEntries(dbParams.filter((p) => p.key.trim()).map((p) => [p.key.trim(), p.value]));
    try {
      await api.attachExternalDbSource(sid, dbName.trim(), dbConn, dbQuery.trim(), params, dbSchemaName || undefined);
      setDbName(""); setDbQuery(""); setDbParams([]);
      refreshSources();
      notify(`Source « ${dbName.trim()} » attachée.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'attachement.", "err"); }
  };

  // ── attach an API source ───────────────────────────────────────
  const [apiConn, setApiConn] = useState("");
  const [apiName, setApiName] = useState("");
  const [apiPath, setApiPath] = useState("");
  const [apiMethod, setApiMethod] = useState("GET");
  const [apiResponseKind, setApiResponseKind] = useState<"json" | "csv" | "xlsx">("json");
  const [apiDataPath, setApiDataPath] = useState("");
  const [apiBody, setApiBody] = useState("");
  const [apiSchemas, setApiSchemas] = useState<VariableSchema[]>([]);
  const [apiSchemaName, setApiSchemaName] = useState("");
  const apiConnections = available.filter((v) => v.kind === "api");

  const pickApiConn = async (name: string) => {
    setApiConn(name); setApiSchemaName(""); setApiSchemas([]);
    const v = apiConnections.find((c) => c.name === name);
    if (!v) return;
    try { setApiSchemas(await api.listConnectionSchemas(v.id)); } catch { setApiSchemas([]); }
  };
  const pickApiSchemaName = (name: string) => {
    setApiSchemaName(name);
    const sc = apiSchemas.find((s) => s.name === name);
    if (sc) { setApiPath(sc.path ?? ""); setApiMethod(sc.method ?? "GET"); setApiDataPath(sc.data_path ?? ""); }
  };

  const attachApi = async () => {
    if (!sid || !apiName.trim() || !apiConn) return;
    let body: Record<string, unknown> | undefined;
    if (apiMethod !== "GET" && apiBody.trim()) {
      try { body = JSON.parse(apiBody); }
      catch { notify("Le corps doit être du JSON valide.", "err"); return; }
    }
    try {
      await api.attachApiSource(sid, apiName.trim(), apiConn, apiPath, apiMethod,
        apiResponseKind, apiDataPath, body, apiSchemaName || undefined);
      setApiName(""); setApiPath(""); setApiDataPath(""); setApiBody("");
      refreshSources();
      notify(`Source « ${apiName.trim()} » attachée.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'attachement.", "err"); }
  };

  const attachDataset = async () => {
    if (!sid || !attachName.trim() || !attachDatasetId) return;
    try {
      await api.attachDatasetSource(sid, attachName.trim(), attachDatasetId);
      setAttachName(""); setAttachDatasetId("");
      refreshSources();
      notify(`Source « ${attachName.trim()} » attachée.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'attachement.", "err"); }
  };

  const attachSession = async () => {
    if (!sid || !attachName.trim() || !attachSessionSid) return;
    try {
      await api.attachSessionSource(sid, attachName.trim(), attachSessionSid);
      setAttachName(""); setAttachSessionSid("");
      refreshSources();
      notify(`Source « ${attachName.trim()} » attachée.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'attachement.", "err"); }
  };

  const attachUpload = async (file: File) => {
    if (!sid) return;
    const name = attachName.trim() || file.name.replace(/\.[^.]+$/, "");
    try {
      await api.attachUploadSource(sid, name, file);
      setAttachName("");
      refreshSources();
      notify(`Source « ${name} » attachée.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'attachement.", "err"); }
  };

  const detachSource = async (name: string) => {
    if (!sid) return;
    try { await api.detachSource(sid, name); refreshSources(); }
    catch (e) { notify(e instanceof Error ? e.message : "Échec du détachement.", "err"); }
  };

  const valid = computed.filter((c) => c.name.trim() && c.expression.trim());
  const validSql = sqlComputed.filter((c) => c.name.trim() && c.expression.trim());
  const validStyle = styleRules.filter((r) => r.column.trim() && r.expression.trim());
  const saveToLibrary = async () => {
    if (!valid.length && !validSql.length && !validStyle.length) {
      notify("Aucune colonne calculée à enregistrer.", "err"); return;
    }
    const body = { computed: valid, ...(validSql.length ? { sql_computed: validSql } : {}),
                  ...(validStyle.length ? { style_rules: validStyle } : {}) };
    try {
      if (saveTarget) {
        const a = await api.addArtefactVersion("computed", saveTarget, body);
        notify(`Enregistré comme version ${a.latest_version_no} de « ${a.name} ».`, "ok");
      } else {
        if (!saveName.trim()) { notify("Donnez un nom à cet ensemble.", "err"); return; }
        await api.createArtefact("computed", { name: saveName.trim(), ...body });
        notify(`Ensemble calculé « ${saveName.trim()} » enregistré.`, "ok");
        setSaveName("");
      }
      refreshLib();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'enregistrement.", "err"); }
  };

  const loadFromLibrary = async (a: ArtefactInfo) => {
    try {
      const v = await api.getArtefactVersion("computed", a.id, a.latest_version_no);
      const body = v.body as { computed?: ComputedColumn[]; sql_computed?: ComputedColumn[];
                               style_rules?: StyleRule[] };
      const items = body.computed ?? [];
      const sqlItems = body.sql_computed ?? [];
      const styleItems = body.style_rules ?? [];
      setComputed(items);
      setSqlComputed(sqlItems);
      setStyleRules(styleItems);
      notify(`Ensemble calculé « ${a.name} » (v${a.latest_version_no}) chargé — ${items.length} colonne(s)`
            + (sqlItems.length ? `, ${sqlItems.length} bloc(s) SQL` : "")
            + (styleItems.length ? `, ${styleItems.length} règle(s) de style.` : "."), "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec du chargement.", "err"); }
  };
  const importRef = useRef<HTMLInputElement>(null);

  const varEntries = Object.entries(variables);
  const setVar = (oldKey: string, newKey: string, val: string) => {
    const next: Record<string, string> = {};
    varEntries.forEach(([k, v]) => { if (k === oldKey) next[newKey] = val; else if (k !== newKey) next[k] = v; });
    setVariables(next);
  };
  const addVar = () => {
    let i = varEntries.length + 1, name = `var${i}`;
    while (name in variables) name = `var${++i}`;
    setVariables({ ...variables, [name]: "" });
  };
  const removeVar = (key: string) =>
    setVariables(Object.fromEntries(varEntries.filter(([k]) => k !== key)));

  const add = (expr = "") =>
    setComputed([...computed, { name: `computed_${computed.length + 1}`, expression: expr }]);

  const addSql = () =>
    setSqlComputed([...sqlComputed,
      { name: `sql_${sqlComputed.length + 1}`, expression: "SELECT self._row_id\nFROM self" }]);

  const addStyleRule = () =>
    setStyleRules([...styleRules, { column: columns[0] ?? "", expression: "" }]);

  const exportFns = () => {
    const blob = new Blob([JSON.stringify({ computed, sql_computed: sqlComputed, style_rules: styleRules }, null, 2)],
                          { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "computed-columns.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importFns = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text());
      const list = Array.isArray(parsed) ? parsed : parsed.computed;
      if (!Array.isArray(list)) throw new Error("bad shape");
      const clean = list
        .filter((c: unknown): c is ComputedColumn =>
          !!c && typeof (c as ComputedColumn).name === "string" && typeof (c as ComputedColumn).expression === "string")
        .map((c) => ({ name: c.name, expression: c.expression }));
      setComputed([...computed, ...clean]);
      const sqlList = Array.isArray(parsed) ? [] : (parsed.sql_computed ?? []);
      if (Array.isArray(sqlList) && sqlList.length) {
        const cleanSql = sqlList
          .filter((c: unknown): c is ComputedColumn =>
            !!c && typeof (c as ComputedColumn).name === "string" && typeof (c as ComputedColumn).expression === "string")
          .map((c) => ({ name: c.name, expression: c.expression }));
        setSqlComputed([...sqlComputed, ...cleanSql]);
      }
      const styleList = Array.isArray(parsed) ? [] : (parsed.style_rules ?? []);
      if (Array.isArray(styleList) && styleList.length) {
        const cleanStyle = styleList
          .filter((r: unknown): r is StyleRule =>
            !!r && typeof (r as StyleRule).column === "string" && typeof (r as StyleRule).expression === "string")
          .map((r) => ({ column: r.column, expression: r.expression }));
        setStyleRules([...styleRules, ...cleanStyle]);
      }
    } catch {
      alert("Impossible de lire ce fichier de fonctions (un JSON avec un tableau 'computed' est attendu).");
    }
  };

  const computedErrCount = computed.filter((c) => errors[c.name]).length;
  const sqlErrCount = sqlComputed.filter((c) => errors[c.name]).length;
  const styleErrCount = styleRules.filter((r) => (styleErrors ?? {})[r.column]).length;

  const Badge = ({ n, err }: { n: number; err: number }) =>
    n > 0 ? <span className={`count ${err > 0 ? "err" : ""}`}>{err > 0 ? err : n}</span> : null;

  return (
    <div className="computed-wrap">
      <div>
        <nav className="ops-tabs computed-tabs">
          <button className={`tab ${tab === "columns" ? "active" : ""}`} onClick={() => setTab("columns")}>
            Colonnes calculées <Badge n={computed.length} err={computedErrCount} />
          </button>
          <button className={`tab ${tab === "sql" ? "active" : ""}`} onClick={() => setTab("sql")}>
            SQL avancé <Badge n={sqlComputed.length} err={sqlErrCount} />
          </button>
          <button className={`tab ${tab === "style" ? "active" : ""}`} onClick={() => setTab("style")}>
            Mise en forme <Badge n={styleRules.length} err={styleErrCount} />
          </button>
          <button className={`tab ${tab === "sources" ? "active" : ""}`} onClick={() => setTab("sources")}>
            Sources <Badge n={sources.length} err={0} />
          </button>
          <button className={`tab ${tab === "library" ? "active" : ""}`} onClick={() => setTab("library")}>
            Bibliothèque <Badge n={lib.length} err={0} />
          </button>
        </nav>

        {tab === "columns" && (
          <div className="tab-panel">
            <p className="hint">Dérivez de nouvelles colonnes à partir des colonnes existantes. Appliqué sur les valeurs nettoyées lors d'une validation.
              <InfoTip>
                <p><b>À quoi ça sert</b> — créer une colonne dont la valeur dépend d'autres colonnes (concaténation, condition, calcul…).</p>
                <p><b>Comment faire</b> — « Ajouter une colonne », nommez-la, écrivez une expression avec <code>[nom_colonne]</code>. Cliquez une colonne dans la barre du bas pour l'insérer sans la taper.</p>
                <p><b>Ce qu'il faut</b> — rien de particulier : ça marche dès qu'un fichier est chargé.</p>
              </InfoTip>
            </p>
            <div className="filterbar">
              <button className="btn primary sm" onClick={() => add()}><IconCode size={14} /> Ajouter une colonne</button>
              {TEMPLATES.map((t) => (
                <button key={t.label} className="btn sm" onClick={() => add(t.expr(columns))}>{t.label}</button>
              ))}
              <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                <button className="btn sm" onClick={() => importRef.current?.click()}><IconUpload size={13} /> Importer</button>
                <button className="btn sm" onClick={exportFns} disabled={computed.length === 0}><IconDownload size={13} /> Exporter</button>
                {computed.length > 0 && (
                  <button className="btn sm" onClick={() => setComputed([])}><IconReset size={13} /> Réinitialiser</button>
                )}
              </span>
              <input ref={importRef} type="file" accept=".json" hidden
                onChange={(e) => { const f = e.target.files?.[0]; if (f) importFns(f); e.target.value = ""; }} />
            </div>

            <div className="vars">
              <div className="vars-h">
                <span>Variables <span style={{ color: "var(--ink-faint)", fontWeight: 400 }}>— valeurs nommées, utilisables dans les expressions comme <code>[nom]</code></span></span>
                <button className="btn sm" onClick={addVar}>+ Ajouter une variable</button>
              </div>
              {varEntries.length === 0 ? (
                <p className="hint" style={{ margin: "4px 0 0" }}>ex. <code>societe = italie</code>, puis utilisez <code>[societe]</code> dans n'importe quelle expression.</p>
              ) : (
                varEntries.map(([k, v], i) => (
                  <div className="var-row" key={i}>
                    <input className="mono-input" value={k} placeholder="nom"
                      onChange={(e) => setVar(k, e.target.value.replace(/[^\w]/g, "_"), v)} />
                    <span className="var-eq">=</span>
                    <input className="mono-input" value={v} placeholder="valeur"
                      onChange={(e) => setVar(k, k, e.target.value)} />
                    <button className="hclear" title="Retirer" onClick={() => removeVar(k)}>×</button>
                  </div>
                ))
              )}
            </div>

            <div className="vars">
              <div className="vars-h">
                <span>Variables du référentiel <span style={{ color: "var(--ink-faint)", fontWeight: 400 }}>
                  — sélectionnées ici, résolues à chaque calcul ; un champ d'un objet JSON s'utilise avec un point,
                  ex. <code>[connexion.champ]</code></span></span>
              </div>
              <div className="flowform" style={{ marginTop: 6 }}>
                <select value={pickVar} onChange={(e) => setPickVar(e.target.value)}>
                  <option value="">— choisir une variable —</option>
                  {pickableVars.map((v) => <option key={v.name} value={v.name}>{v.name} ({v.kind})</option>)}
                </select>
                <button className="btn sm" disabled={!pickVar} onClick={addRefVariable}>+ Ajouter</button>
              </div>
              {refVariables.length === 0 ? (
                <p className="hint" style={{ margin: "6px 0 0" }}>Aucune variable du référentiel sélectionnée.</p>
              ) : (
                refVariables.map((name) => {
                  const v = available.find((x) => x.name === name);
                  return (
                    <div className="var-row" key={name}>
                      <code className="mono-input" style={{ flex: 1 }}>{name}</code>
                      <span className="csub" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {v ? `${v.kind} · ${v.value}` : "introuvable dans cet environnement"}
                      </span>
                      <button className="hclear" title="Retirer" onClick={() => removeRefVariable(name)}>×</button>
                    </div>
                  );
                })
              )}
            </div>

            {computed.length === 0 ? (
              <div className="banner"><span>Aucune colonne calculée pour l'instant. Ajoutez-en une, ou partez d'un modèle ci-dessus.</span></div>
            ) : (
              computed.map((c, i) => (
                <Row key={i} col={c} columns={columns} serverError={errors[c.name]}
                  onChange={(nc) => setComputed(computed.map((x, j) => (j === i ? nc : x)))}
                  onRemove={() => setComputed(computed.filter((_, j) => j !== i))} />
              ))
            )}
          </div>
        )}

        {tab === "sql" && (
          <div className="tab-panel">
            <p className="hint">
              Requêtes DuckDB contre <code>self</code> (cette session, colonne <code>_row_id</code> incluse) et les
              sources attachées — jointures, fenêtres, agrégations que les colonnes calculées ne peuvent pas faire.
              Le résultat doit renvoyer <code>_row_id</code> ; sans lui, la requête est refusée.
              <InfoTip>
                <p><b>À quoi ça sert</b> — croiser cette session avec une autre table, un fichier ou une base externe pour compléter ou remplacer des valeurs.</p>
                <p><b>Comment faire</b> — écrivez une requête SQL (DuckDB) ; <code>self</code> désigne la session courante. « Remplacer » écrase la colonne, « Compléter le vide » ne touche que les cellules vides.</p>
                <p><b>Ce qu'il faut</b> — au moins une source attachée dans l'onglet « Sources » pour joindre autre chose que la session elle-même.</p>
              </InfoTip>
            </p>
            <div className="filterbar">
              <button className="btn primary sm" onClick={addSql}><IconCode size={14} /> Ajouter un bloc SQL</button>
              {sqlComputed.length > 0 && (
                <button className="btn sm" onClick={() => setSqlComputed([])}><IconReset size={13} /> Réinitialiser</button>
              )}
            </div>
            {sqlComputed.length === 0 ? (
              <div className="banner"><span>Aucun bloc SQL pour l'instant. Attachez d'abord une source dans l'onglet « Sources » si besoin.</span></div>
            ) : (
              sqlComputed.map((c, i) => (
                <SqlBlock key={i} col={c} columns={columns} sources={sources} serverError={errors[c.name]}
                  onChange={(nc) => setSqlComputed(sqlComputed.map((x, j) => (j === i ? nc : x)))}
                  onRemove={() => setSqlComputed(sqlComputed.filter((_, j) => j !== i))} />
              ))
            )}
          </div>
        )}

        {tab === "style" && (
          <div className="tab-panel">
            <p className="hint">
              Comment une colonne existante doit <em>paraître</em>, jamais ce qu'elle contient —
              une condition simple avec <code>STYLE(color, bold, italic)</code>, ou une requête
              multi-source (même préfixe SELECT/WITH que le SQL avancé) renvoyant directement une
              couleur.
              <InfoTip>
                <p><b>À quoi ça sert</b> — mettre en évidence des lignes selon une condition (ex. colorer en orange un âge mineur), sans changer la donnée.</p>
                <p><b>Comment faire</b> — choisissez la colonne à styler, écrivez <code>IF(condition, STYLE("couleur", "1"), STYLE())</code> ; ou une requête SQL renvoyant juste une couleur.</p>
                <p><b>Ce qu'il faut</b> — la colonne visée doit déjà exister dans le fichier.</p>
              </InfoTip>
            </p>
            <div className="filterbar">
              <button className="btn primary sm" onClick={addStyleRule}><IconCode size={14} /> Ajouter une règle</button>
              {styleRules.length > 0 && (
                <button className="btn sm" onClick={() => setStyleRules([])}><IconReset size={13} /> Réinitialiser</button>
              )}
            </div>
            {styleRules.length === 0 ? (
              <div className="banner"><span>Aucune règle de mise en forme pour l'instant.</span></div>
            ) : (
              styleRules.map((r, i) => (
                <StyleRuleRow key={i} rule={r} columns={columns} serverError={(styleErrors ?? {})[r.column]}
                  onChange={(nr) => setStyleRules(styleRules.map((x, j) => (j === i ? nr : x)))}
                  onRemove={() => setStyleRules(styleRules.filter((_, j) => j !== i))} />
              ))
            )}
          </div>
        )}

        {tab === "sources" && (
          <div className="tab-panel">
            <p className="hint">Une table interne ou un fichier, croisé avec cette session dans une requête SQL ou une règle de mise en forme — sans construire de flux.
              <InfoTip>
                <p><b>À quoi ça sert</b> — donner au SQL avancé et à la mise en forme une autre table à joindre, sans construire un flux visuel.</p>
                <p><b>Comment faire</b> — choisissez un type (table interne, fichier, base externe, API), donnez un nom : c'est ce nom qui sert dans vos requêtes (<code>FROM self LEFT JOIN nom ...</code>).</p>
                <p><b>Ce qu'il faut</b> — pour une base externe ou une API, un point de connexion doit déjà exister dans Exploitation → Référentiel.</p>
              </InfoTip>
            </p>
            {!sid ? (
              <div className="banner"><span>Chargez d'abord une session.</span></div>
            ) : (
              <>
                <div className="flowform">
                  <div className="frow"><label>Nom</label>
                    <input className="mono-input" value={attachName} placeholder="ex. referentiel_clients"
                      onChange={(e) => setAttachName(e.target.value.replace(/\s+/g, "_"))} /></div>
                  <div className="frow"><label>Table interne</label>
                    <select value={attachDatasetId} onChange={(e) => setAttachDatasetId(e.target.value)}>
                      <option value="">— choisir —</option>
                      {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select></div>
                  <button className="btn sm" disabled={!attachName.trim() || !attachDatasetId} onClick={attachDataset}>
                    Attacher la table
                  </button>
                  <span style={{ marginLeft: 8 }}>ou</span>
                  <button className="btn sm" onClick={() => uploadRef.current?.click()}>
                    <IconUpload size={13} /> Importer un fichier
                  </button>
                  <input ref={uploadRef} type="file" accept=".csv,.xlsx,.xls" hidden
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) attachUpload(f); e.target.value = ""; }} />
                  {otherTabs.length > 0 && (
                    <>
                      <span style={{ marginLeft: 8 }}>ou</span>
                      <select value={attachSessionSid} onChange={(e) => setAttachSessionSid(e.target.value)}>
                        <option value="">— onglet ouvert —</option>
                        {otherTabs.map((t) => <option key={t.sid} value={t.sid}>{t.label}</option>)}
                      </select>
                      <button className="btn sm" disabled={!attachName.trim() || !attachSessionSid} onClick={attachSession}>
                        Attacher l'onglet
                      </button>
                    </>
                  )}
                </div>
                {sources.length === 0 ? (
                  <p className="hint">Aucune source attachée pour l'instant.</p>
                ) : (
                  <div className="libcol" style={{ marginTop: 6 }}>
                    {sources.map((s) => (
                      <div key={s.name} className="libitem">
                        <span><code>{s.name}</code> <span className="csub">{s.row_count} ligne(s), {s.columns.length} colonne(s)</span></span>
                        <button className="btn sm" onClick={() => detachSource(s.name)}>Détacher</button>
                      </div>
                    ))}
                  </div>
                )}

                <div className="sec-h gap-lg">
                  <h3 style={{ fontSize: 14 }}>Source BDD externe</h3>
                  <span className="sub">Requête en lecture seule sur un point de connexion enregistré — paramètres liés, jamais de substitution textuelle.</span>
                </div>
                <div className="flowform">
                  <div className="frow"><label>Connexion</label>
                    <select value={dbConn} onChange={(e) => pickDbConn(e.target.value)}>
                      <option value="">— choisir —</option>
                      {dbConnections.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                    </select></div>
                  <div className="frow"><label>Nom de la source</label>
                    <input className="mono-input" value={dbName} placeholder="ex. clients_externe"
                      onChange={(e) => setDbName(e.target.value.replace(/\s+/g, "_"))} /></div>
                  {dbConn && (
                    <div className="frow"><label>Schéma connu</label>
                      <select value={dbSchemaName} onChange={(e) => pickDbSchemaName(e.target.value)}>
                        <option value="">— requête libre —</option>
                        {dbSchemas.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                      </select></div>
                  )}
                </div>
                {dbSchemaName && (
                  <div className="chipbar">
                    <span className="chiplabel">insérer une colonne :</span>
                    {(dbSchemas.find((s) => s.name === dbSchemaName)?.columns ?? []).map((c) => (
                      <button key={c.name} type="button" className="microchip"
                        onClick={() => setDbQuery((q) => q + c.name)}>{c.name}</button>
                    ))}
                  </div>
                )}
                <textarea className="mono-input" rows={3} style={{ width: "100%", marginTop: 6 }}
                  placeholder="SELECT * FROM clients WHERE pays = :pays"
                  value={dbQuery} onChange={(e) => setDbQuery(e.target.value)} />
                <span className="csub">Paramètres liés (jamais insérés comme texte)</span>
                {dbParams.map((p, i) => (
                  <div className="sql-pair" key={i}>
                    <input placeholder="nom du paramètre" value={p.key}
                      onChange={(e) => setDbParams(dbParams.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
                    <span>=</span>
                    <input placeholder="valeur" value={p.value}
                      onChange={(e) => setDbParams(dbParams.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
                    <button className="hclear" onClick={() => setDbParams(dbParams.filter((_, j) => j !== i))}>×</button>
                  </div>
                ))}
                <div className="filterbar" style={{ marginTop: 6 }}>
                  <button className="btn sm" onClick={() => setDbParams([...dbParams, { key: "", value: "" }])}>+ paramètre</button>
                  <button className="btn primary sm" disabled={!dbConn || !dbName.trim() || !dbQuery.trim()}
                    onClick={attachExternalDb}>Attacher la source</button>
                </div>

                <div className="sec-h gap-lg">
                  <h3 style={{ fontSize: 14 }}>Source API</h3>
                  <span className="sub">La réponse peut être des données JSON ou un fichier (CSV/Excel) — à choisir explicitement, jamais deviné.</span>
                </div>
                <div className="flowform">
                  <div className="frow"><label>Connexion</label>
                    <select value={apiConn} onChange={(e) => pickApiConn(e.target.value)}>
                      <option value="">— choisir —</option>
                      {apiConnections.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                    </select></div>
                  <div className="frow"><label>Nom de la source</label>
                    <input className="mono-input" value={apiName} placeholder="ex. commandes_api"
                      onChange={(e) => setApiName(e.target.value.replace(/\s+/g, "_"))} /></div>
                  {apiConn && (
                    <div className="frow"><label>Endpoint connu</label>
                      <select value={apiSchemaName} onChange={(e) => pickApiSchemaName(e.target.value)}>
                        <option value="">— chemin libre —</option>
                        {apiSchemas.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                      </select></div>
                  )}
                  <div className="frow"><label>Chemin</label>
                    <input value={apiPath} placeholder="orders" onChange={(e) => setApiPath(e.target.value)} /></div>
                  <div className="frow"><label>Méthode</label>
                    <select value={apiMethod} onChange={(e) => setApiMethod(e.target.value)}>
                      {["GET", "POST", "PUT", "DELETE"].map((m) => <option key={m} value={m}>{m}</option>)}
                    </select></div>
                  <div className="frow"><label>Réponse</label>
                    <select value={apiResponseKind}
                      onChange={(e) => setApiResponseKind(e.target.value as "json" | "csv" | "xlsx")}>
                      <option value="json">Données JSON</option>
                      <option value="csv">Fichier CSV</option>
                      <option value="xlsx">Fichier Excel</option>
                    </select></div>
                </div>
                {apiResponseKind === "json" && (
                  <div className="frow"><label>Chemin dans la réponse (optionnel)</label>
                    <input value={apiDataPath} placeholder="data.orders"
                      onChange={(e) => setApiDataPath(e.target.value)} /></div>
                )}
                {apiMethod !== "GET" && (
                  <textarea className="mono-input" rows={2} style={{ width: "100%", marginTop: 6 }}
                    placeholder='Corps JSON optionnel, ex. {"depuis": "2026-01-01"}'
                    value={apiBody} onChange={(e) => setApiBody(e.target.value)} />
                )}
                <div className="filterbar" style={{ marginTop: 6 }}>
                  <button className="btn primary sm" disabled={!apiConn || !apiName.trim()} onClick={attachApi}>
                    Attacher la source
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {tab === "library" && (
          <div className="tab-panel">
            <p className="hint">Enregistre cet ensemble côté serveur, versionné — réutilisable dans un flux.
              <InfoTip>
                <p><b>À quoi ça sert</b> — réutiliser le même ensemble de colonnes calculées, blocs SQL et règles de mise en forme dans une autre session ou un flux.</p>
                <p><b>Comment faire</b> — donnez un nom et « Enregistrer » (ou choisissez un ensemble existant pour l'enregistrer comme nouvelle version). « Charger » remplace les colonnes/règles actuelles.</p>
                <p><b>Ce qu'il faut</b> — au moins une colonne calculée, un bloc SQL ou une règle valide.</p>
              </InfoTip>
            </p>
            <div className="flowform">
              <div className="frow"><label>Enregistrer sous</label>
                <select value={saveTarget} onChange={(e) => setSaveTarget(e.target.value)}>
                  <option value="">nouvel ensemble…</option>
                  {lib.map((a) => <option key={a.id} value={a.id}>new version of « {a.name} » (v{a.latest_version_no})</option>)}
                </select></div>
              {saveTarget === "" && (
                <div className="frow"><label>Nom</label>
                  <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="ex. colonnes-clients" /></div>
              )}
              <button className="btn primary" onClick={saveToLibrary}>Enregistrer dans la bibliothèque</button>
            </div>
            {lib.length === 0 ? (
              <div className="banner"><span>Rien dans la bibliothèque pour l'instant.</span></div>
            ) : (
              <>
                <div className="frow" style={{ maxWidth: 260, marginTop: 6 }}>
                  <SearchInput value={libSearch} onChange={setLibSearch} placeholder="Rechercher un ensemble..." />
                </div>
                <div className="libcol kind-computed">
                  <div className="libcol-list">
                    {lib.filter((a) => matchesSearch(a.name, libSearch)).map((a) => (
                      <div key={a.id} className="libitem">
                        <span className="libitem-name">{a.name} <span className="csub">v{a.latest_version_no}</span></span>
                        <span className="libitem-date" title={`Modifié le ${new Date(a.updated_at).toLocaleString()}`}>
                          {fmtShortDate(a.updated_at)}
                        </span>
                        <button className="btn sm" onClick={() => loadFromLibrary(a)}>Charger</button>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <aside className="reference">
        {tab === "sources" ? (
          <>
            <div className="ref-h">Sources attachées</div>
            <p className="hint">Une table interne déjà chargée dans l'application, ou un fichier importé à la volée — devient une table nommée utilisable dans une requête SQL avancée ou une règle de mise en forme.</p>
            <p className="hint">Le nom donné ici est celui à utiliser dans vos requêtes, ex. <code>FROM self LEFT JOIN referentiel_clients ...</code>.</p>
          </>
        ) : tab === "library" ? (
          <>
            <div className="ref-h">Bibliothèque</div>
            <p className="hint">Un ensemble enregistré regroupe colonnes calculées, blocs SQL et règles de mise en forme actuels — versionné, chargeable dans une autre session ou un flux.</p>
          </>
        ) : (
          <>
            <div className="ref-h">Syntaxe</div>
            <p className="hint">Référencez une colonne avec <code>[nom]</code>. Le texte s'écrit entre <code>"guillemets"</code>. Une colonne renommée s'utilise sous son nouveau nom.</p>
            <p className="hint">Une variable du référentiel dont la valeur est un objet JSON (un point de connexion, par exemple) s'utilise avec un point : <code>[connexion.champ]</code>.</p>
            <div className="ref-list">
              {FUNCS.map(([sig, desc]) => (
                <div key={sig} className="ref-item"><code>{sig}</code><span>{desc}</span></div>
              ))}
            </div>
            <div className="ref-h" style={{ marginTop: 16 }}>Jetons dynamiques</div>
            <p className="hint">S'utilisent comme une colonne : <code>[DATENOW]</code>, <code>[MOIS]</code>, <code>[MOIS_NOM]</code>, <code>[MOIS_COURT]</code>, <code>[JOUR]</code>, <code>[ANNEE]</code>, <code>[JOUR_NOM]</code>.</p>
            <div className="ref-h" style={{ marginTop: 16 }}>Exemples</div>
            <div className="ref-list">
              <div className="ref-item"><code>CONCAT([nom], " ", [prenom])</code><span>nom complet</span></div>
              <div className="ref-item"><code>IF([age] &gt; "18", "adult", "minor")</code><span>libellé conditionnel</span></div>
              <div className="ref-item"><code>UPPER(TRIM([code]))</code><span>code nettoyé</span></div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
