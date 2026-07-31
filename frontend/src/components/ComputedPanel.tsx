import { useCallback, useEffect, useRef, useState } from "react";
import type { ComputedColumn, DatasetInfo, SourceInfo, StyleRule } from "../lib/types";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
import { IconCode, IconReset, IconUpload, IconDownload } from "../lib/icons";

interface Props {
  sid: string | null;
  columns: string[];
  computed: ComputedColumn[];
  setComputed: (c: ComputedColumn[]) => void;
  sqlComputed: ComputedColumn[];
  setSqlComputed: (c: ComputedColumn[]) => void;
  styleRules: StyleRule[];
  setStyleRules: (r: StyleRule[]) => void;
  variables: Record<string, string>;
  setVariables: (v: Record<string, string>) => void;
  errors: Record<string, string>;   // server-side errors from the last run
  styleErrors?: Record<string, string>;
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
}

const TEMPLATES: { label: string; expr: (cols: string[]) => string }[] = [
  { label: "Concatenate", expr: (c) => `CONCAT([${c[0] ?? "col1"}], " ", [${c[1] ?? "col2"}])` },
  { label: "Condition", expr: (c) => `IF([${c[0] ?? "col1"}] == "value", "yes", "no")` },
  { label: "Uppercase", expr: (c) => `UPPER([${c[0] ?? "col1"}])` },
  { label: "Fallback", expr: (c) => `DEFAULT([${c[0] ?? "col1"}], "N/A")` },
];

const FUNCS = [
  ["IF(cond, a, b)", "returns a when cond is true, else b"],
  ["ISNULL(x) / NOTNULL(x)", "test for empty / null — e.g. IF(ISNULL([x]), \"N/A\", [x])"],
  ["COALESCE(…) / DEFAULT(x, fb)", "first non-empty value / fallback when x is empty"],
  ["CONCAT(…)", "joins values and text"],
  ["UPPER / LOWER / TITLE / TRIM(x)", "text transforms"],
  ["LEFT(x, n) / RIGHT(x, n)", "first / last n characters"],
  ["LTRIM(x, \"0\") / RTRIM(x, \"0\")", "strip leading / trailing chars — e.g. 0012 → 12"],
  ["REGEX_EXTRACT(x, pat, n)", "capture group n of a regex — e.g. \"0*([0-9]+)\" → 12"],
  ["REPLACE(x, a, b)", "replace a with b"],
  ["NUM(x) / STR(x) / LEN(x)", "cast / length"],
  ["EXISTS(\"col\") / COL(\"col\", def)", "does a column exist / its value by name"],
  ["STYLE(color, bold, italic)", "for mise en forme conditionnelle — e.g. STYLE(\"orange\", \"1\")"],
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
      } catch { setErr("Could not validate."); setOk(false); }
    }, 350);
    return () => window.clearTimeout(timer.current);
  }, [col.expression]);

  const insert = (token: string) => onChange({ ...col, expression: col.expression + token });

  return (
    <div className="computed-row">
      <div className="computed-head">
        <input className="mono-input" placeholder="new_column_name" value={col.name}
          onChange={(e) => onChange({ ...col, name: e.target.value.replace(/\s+/g, "_") })}
          style={{ maxWidth: 220 }} />
        <span style={{ marginLeft: "auto" }}>
          {col.expression.trim() && (ok
            ? <span className="valid ok">valid</span>
            : err ? <span className="valid err">{err}</span> : <span className="valid">checking…</span>)}
        </span>
        <button className="btn sm" onClick={onRemove} title="Remove">✕</button>
      </div>
      <textarea className="mono-input" rows={2} placeholder='IF([civilite] == "M", "Monsieur", "Madame")'
        value={col.expression} onChange={(e) => onChange({ ...col, expression: e.target.value })} />
      {serverError && <div className="valid err" style={{ marginTop: 4 }}>Last run: {serverError}</div>}
      <div className="chipbar">
        <span className="chiplabel">insert column:</span>
        {columns.slice(0, 40).map((c) => (
          <button key={c} className="microchip" onClick={() => insert(`[${c}]`)}>{c}</button>
        ))}
        {columns.length > 40 && <span className="chiplabel">+{columns.length - 40} more…</span>}
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
        <button className="btn sm" style={{ marginLeft: "auto" }} onClick={onRemove} title="Remove">✕</button>
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
      {serverError && <div className="valid err" style={{ marginTop: 4 }}>Last run: {serverError}</div>}
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
        <button className="btn sm" style={{ marginLeft: "auto" }} onClick={onRemove} title="Remove">✕</button>
      </div>
      <textarea className="mono-input" rows={2}
        placeholder={'IF([age] < "18", STYLE("orange", "1"), STYLE())'}
        value={rule.expression}
        onChange={(e) => onChange({ ...rule, expression: e.target.value })} />
      {serverError && <div className="valid err" style={{ marginTop: 4 }}>Last run: {serverError}</div>}
    </div>
  );
}

export function ComputedPanel({ sid, columns, computed, setComputed, sqlComputed, setSqlComputed,
                               styleRules, setStyleRules, variables, setVariables, errors,
                               styleErrors, notify }: Props) {
  const [lib, setLib] = useState<ArtefactInfo[]>([]);
  const [saveName, setSaveName] = useState("");
  const [saveTarget, setSaveTarget] = useState("");
  const refreshLib = useCallback(() => { api.listArtefacts("computed").then(setLib).catch(() => {}); }, []);
  useEffect(() => { refreshLib(); }, [refreshLib]);

  // ── attached sources (for cross-source SQL) ──────────────────────
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [attachName, setAttachName] = useState("");
  const [attachDatasetId, setAttachDatasetId] = useState("");
  const uploadRef = useRef<HTMLInputElement>(null);
  const refreshSources = useCallback(() => {
    if (sid) api.listSources(sid).then(setSources).catch(() => {});
  }, [sid]);
  useEffect(() => { refreshSources(); }, [refreshSources]);
  useEffect(() => { api.listDatasets().then(setDatasets).catch(() => {}); }, []);

  const attachDataset = async () => {
    if (!sid || !attachName.trim() || !attachDatasetId) return;
    try {
      await api.attachDatasetSource(sid, attachName.trim(), attachDatasetId);
      setAttachName(""); setAttachDatasetId("");
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
      notify("No computed column to save.", "err"); return;
    }
    const body = { computed: valid, ...(validSql.length ? { sql_computed: validSql } : {}),
                  ...(validStyle.length ? { style_rules: validStyle } : {}) };
    try {
      if (saveTarget) {
        const a = await api.addArtefactVersion("computed", saveTarget, body);
        notify(`Saved as version ${a.latest_version_no} of « ${a.name} ».`, "ok");
      } else {
        if (!saveName.trim()) { notify("Give the set a name.", "err"); return; }
        await api.createArtefact("computed", { name: saveName.trim(), ...body });
        notify(`Computed set « ${saveName.trim()} » saved.`, "ok");
        setSaveName("");
      }
      refreshLib();
    } catch (e) { notify(e instanceof Error ? e.message : "Save failed.", "err"); }
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
      notify(`Computed set « ${a.name} » (v${a.latest_version_no}) loaded — ${items.length} column(s)`
            + (sqlItems.length ? `, ${sqlItems.length} bloc(s) SQL` : "")
            + (styleItems.length ? `, ${styleItems.length} règle(s) de style.` : "."), "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Load failed.", "err"); }
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
      alert("Could not read this functions file (expected JSON with a 'computed' array).");
    }
  };

  return (
    <div className="computed-wrap">
      <div>
        <div className="sec-h">
          <h3>Computed columns</h3>
          <span className="sub">Derive new columns from existing ones. Applied on cleaned values when you run validation.</span>
        </div>

        <div className="filterbar">
          <button className="btn primary sm" onClick={() => add()}><IconCode size={14} /> Add column</button>
          {TEMPLATES.map((t) => (
            <button key={t.label} className="btn sm" onClick={() => add(t.expr(columns))}>{t.label}</button>
          ))}
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button className="btn sm" onClick={() => importRef.current?.click()}><IconUpload size={13} /> Import</button>
            <button className="btn sm" onClick={exportFns} disabled={computed.length === 0}><IconDownload size={13} /> Export</button>
            {computed.length > 0 && (
              <button className="btn sm" onClick={() => setComputed([])}><IconReset size={13} /> Clear</button>
            )}
          </span>
          <input ref={importRef} type="file" accept=".json" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importFns(f); e.target.value = ""; }} />
        </div>

        <div className="vars">
          <div className="vars-h">
            <span>Variables <span style={{ color: "var(--ink-faint)", fontWeight: 400 }}>— named values usable in expressions as <code>[name]</code></span></span>
            <button className="btn sm" onClick={addVar}>+ Add variable</button>
          </div>
          {varEntries.length === 0 ? (
            <p className="hint" style={{ margin: "4px 0 0" }}>e.g. <code>societe = italie</code>, then use <code>[societe]</code> in any expression.</p>
          ) : (
            varEntries.map(([k, v], i) => (
              <div className="var-row" key={i}>
                <input className="mono-input" value={k} placeholder="name"
                  onChange={(e) => setVar(k, e.target.value.replace(/[^\w]/g, "_"), v)} />
                <span className="var-eq">=</span>
                <input className="mono-input" value={v} placeholder="value"
                  onChange={(e) => setVar(k, k, e.target.value)} />
                <button className="hclear" title="Remove" onClick={() => removeVar(k)}>×</button>
              </div>
            ))
          )}
        </div>

        {computed.length === 0 ? (
          <div className="banner"><span>No computed columns yet. Add one, or start from a template above.</span></div>
        ) : (
          computed.map((c, i) => (
            <Row key={i} col={c} columns={columns} serverError={errors[c.name]}
              onChange={(nc) => setComputed(computed.map((x, j) => (j === i ? nc : x)))}
              onRemove={() => setComputed(computed.filter((_, j) => j !== i))} />
          ))
        )}
      </div>

      <div className="sec-h" style={{ marginTop: 20 }}>
        <h3>Sources attachées</h3>
        <span className="sub">Une table interne ou un fichier, croisé avec cette session dans une requête SQL — sans construire de flux.</span>
      </div>
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
        </>
      )}

      <div className="sec-h" style={{ marginTop: 20 }}>
        <h3>SQL avancé</h3>
        <span className="sub">
          Requêtes DuckDB contre <code>self</code> (cette session, colonne <code>_row_id</code> incluse) et les
          sources ci-dessus — jointures, fenêtres, agrégations que les colonnes calculées ne peuvent pas faire.
          Le résultat doit renvoyer <code>_row_id</code> ; sans lui, la requête est refusée.
        </span>
      </div>
      <div className="filterbar">
        <button className="btn primary sm" onClick={addSql}><IconCode size={14} /> Ajouter un bloc SQL</button>
        {sqlComputed.length > 0 && (
          <button className="btn sm" onClick={() => setSqlComputed([])}><IconReset size={13} /> Clear</button>
        )}
      </div>
      {sqlComputed.length === 0 ? (
        <div className="banner"><span>Aucun bloc SQL pour l'instant.</span></div>
      ) : (
        sqlComputed.map((c, i) => (
          <SqlBlock key={i} col={c} columns={columns} sources={sources} serverError={errors[c.name]}
            onChange={(nc) => setSqlComputed(sqlComputed.map((x, j) => (j === i ? nc : x)))}
            onRemove={() => setSqlComputed(sqlComputed.filter((_, j) => j !== i))} />
        ))
      )}

      <div className="sec-h" style={{ marginTop: 20 }}>
        <h3>Mise en forme conditionnelle</h3>
        <span className="sub">
          Comment une colonne existante doit <em>paraître</em>, jamais ce qu'elle contient —
          une condition simple avec <code>STYLE(color, bold, italic)</code>, ou une requête
          multi-source (même préfixe SELECT/WITH que le SQL avancé) renvoyant directement une
          couleur.
        </span>
      </div>
      <div className="filterbar">
        <button className="btn primary sm" onClick={addStyleRule}><IconCode size={14} /> Ajouter une règle</button>
        {styleRules.length > 0 && (
          <button className="btn sm" onClick={() => setStyleRules([])}><IconReset size={13} /> Clear</button>
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

      <div className="sec-h" style={{ marginTop: 20 }}>
        <h3>Library</h3>
        <span className="sub">Store this computed set server-side, versioned — reusable in flows.</span>
      </div>
      <div className="flowform">
        <div className="frow"><label>Save as</label>
          <select value={saveTarget} onChange={(e) => setSaveTarget(e.target.value)}>
            <option value="">new set…</option>
            {lib.map((a) => <option key={a.id} value={a.id}>new version of « {a.name} » (v{a.latest_version_no})</option>)}
          </select></div>
        {saveTarget === "" && (
          <div className="frow"><label>Name</label>
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="e.g. colonnes-clients" /></div>
        )}
        <button className="btn primary" onClick={saveToLibrary}>Save to library</button>
        {lib.length > 0 && (
          <div className="libcol" style={{ marginTop: 6 }}>
            {lib.map((a) => (
              <div key={a.id} className="libitem">
                <span>{a.name} <span className="csub">v{a.latest_version_no}</span></span>
                <button className="btn sm" onClick={() => loadFromLibrary(a)}>Load</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <aside className="reference">
        <div className="ref-h">Syntax</div>
        <p className="hint">Reference a column with <code>[name]</code>. Wrap text in <code>"quotes"</code>. Renamed columns use their new name.</p>
        <div className="ref-list">
          {FUNCS.map(([sig, desc]) => (
            <div key={sig} className="ref-item"><code>{sig}</code><span>{desc}</span></div>
          ))}
        </div>
        <div className="ref-h" style={{ marginTop: 16 }}>Dynamic tokens</div>
        <p className="hint">Use like a column: <code>[DATENOW]</code>, <code>[MOIS]</code>, <code>[MOIS_NOM]</code>, <code>[MOIS_COURT]</code>, <code>[JOUR]</code>, <code>[ANNEE]</code>, <code>[JOUR_NOM]</code>.</p>
        <div className="ref-h" style={{ marginTop: 16 }}>Examples</div>
        <div className="ref-list">
          <div className="ref-item"><code>CONCAT([nom], " ", [prenom])</code><span>full name</span></div>
          <div className="ref-item"><code>IF([age] &gt; "18", "adult", "minor")</code><span>conditional label</span></div>
          <div className="ref-item"><code>UPPER(TRIM([code]))</code><span>clean code</span></div>
        </div>
      </aside>
    </div>
  );
}
