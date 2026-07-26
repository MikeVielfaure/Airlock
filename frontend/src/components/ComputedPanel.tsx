import { useCallback, useEffect, useRef, useState } from "react";
import type { ComputedColumn } from "../lib/types";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
import { IconCode, IconReset, IconUpload, IconDownload } from "../lib/icons";

interface Props {
  columns: string[];
  computed: ComputedColumn[];
  setComputed: (c: ComputedColumn[]) => void;
  variables: Record<string, string>;
  setVariables: (v: Record<string, string>) => void;
  errors: Record<string, string>;   // server-side errors from the last run
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

export function ComputedPanel({ columns, computed, setComputed, variables, setVariables, errors, notify }: Props) {
  const [lib, setLib] = useState<ArtefactInfo[]>([]);
  const [saveName, setSaveName] = useState("");
  const [saveTarget, setSaveTarget] = useState("");
  const refreshLib = useCallback(() => { api.listArtefacts("computed").then(setLib).catch(() => {}); }, []);
  useEffect(() => { refreshLib(); }, [refreshLib]);

  const valid = computed.filter((c) => c.name.trim() && c.expression.trim());
  const saveToLibrary = async () => {
    if (!valid.length) { notify("No computed column to save.", "err"); return; }
    try {
      if (saveTarget) {
        const a = await api.addArtefactVersion("computed", saveTarget, { computed: valid });
        notify(`Saved as version ${a.latest_version_no} of « ${a.name} ».`, "ok");
      } else {
        if (!saveName.trim()) { notify("Give the set a name.", "err"); return; }
        await api.createArtefact("computed", { name: saveName.trim(), computed: valid });
        notify(`Computed set « ${saveName.trim()} » saved.`, "ok");
        setSaveName("");
      }
      refreshLib();
    } catch (e) { notify(e instanceof Error ? e.message : "Save failed.", "err"); }
  };

  const loadFromLibrary = async (a: ArtefactInfo) => {
    try {
      const v = await api.getArtefactVersion("computed", a.id, a.latest_version_no);
      const items = (v.body as { computed?: ComputedColumn[] }).computed ?? [];
      setComputed(items);
      notify(`Computed set « ${a.name} » (v${a.latest_version_no}) loaded — ${items.length} column(s).`, "ok");
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

  const exportFns = () => {
    const blob = new Blob([JSON.stringify({ computed }, null, 2)], { type: "application/json" });
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
        .filter((c) => c && typeof c.name === "string" && typeof c.expression === "string")
        .map((c) => ({ name: c.name, expression: c.expression }));
      setComputed([...computed, ...clean]);
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
