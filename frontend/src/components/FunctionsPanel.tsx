import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
import { IconCode, IconPlay, IconSave } from "../lib/icons";

interface Props { notify: (m: string, k?: "ok" | "err" | "info") => void }

/**
 * User-defined functions: a named expression, reusable everywhere expressions
 * are evaluated. Written once here, callable from computed columns, mapping
 * links and the compute/filter bricks — because a function *is* an expression,
 * the places that evaluate them needed no change.
 */
export function FunctionsPanel({ notify }: Props) {
  const [saved, setSaved] = useState<ArtefactInfo[]>([]);
  const [name, setName] = useState("");
  const [params, setParams] = useState("montant, taux");
  const [expr, setExpr] = useState("ROUND(NUM([montant]) * (1 + NUM([taux])), 2)");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try { setSaved(await api.listArtefacts("function")); } catch { /* empty is fine */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const paramList = params.split(",").map((p) => p.trim()).filter(Boolean);

  return (
    <div className="fn">
      <p className="fn-hint">
        A function is an expression with named parameters. Inside the body,
        <code>[montant]</code> is the <em>argument</em> — never a column of the
        table it is called on, so it behaves the same everywhere.
      </p>

      <div className="fn-form">
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="prix_ttc" />
        <label>Parameters</label>
        <input value={params} onChange={(e) => setParams(e.target.value)}
               placeholder="montant, taux" />
        <label>Expression</label>
        <textarea className="mono" rows={3} value={expr}
                  onChange={(e) => setExpr(e.target.value)} />
        {name && paramList.length > 0 && (
          <p className="fn-call">
            Call it as <code>{name.toUpperCase()}({paramList.map((p) => `[${p}]`).join(", ")})</code>
          </p>
        )}
        <div className="fn-actions">
          <button className="btn" disabled={busy || !name.trim() || !expr.trim()}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      const body = { name: name.trim(), params: paramList, expr };
                      const ex = saved.find((f) => f.name === name.trim());
                      if (ex) await api.addArtefactVersion("function", ex.id, { body });
                      else await api.createArtefact("function", { name: name.trim(), body });
                      await refresh();
                      notify(`Function “${name}” saved.`, "ok");
                    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                    finally { setBusy(false); }
                  }}>
            <IconSave size={14} /> Save
          </button>
        </div>
      </div>

      <h4><IconCode size={13} /> Functions in this environment <span className="count">{saved.length}</span></h4>
      {saved.length === 0 && <p className="fn-hint">None yet.</p>}
      {saved.map((f) => (
        <div key={f.id} className="fn-row">
          <strong>{f.name.toUpperCase()}</strong>
          <span className="fn-env">{f.environment ?? "default"}</span>
          <button className="btn sm" onClick={async () => {
            try {
              const d = await api.getArtefact("function", f.id);
              const b = (d as { body: { name: string; params: string[]; expr: string } }).body;
              setName(b.name); setParams((b.params || []).join(", ")); setExpr(b.expr);
            } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
          }}><IconPlay size={12} /> Open</button>
        </div>
      ))}
    </div>
  );
}
