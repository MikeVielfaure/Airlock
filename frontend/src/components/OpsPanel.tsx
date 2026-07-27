import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RunRow, VariableRow } from "../lib/types";
import {
  IconCheck, IconCode, IconLayers, IconReset, IconSave, IconWarn,
} from "../lib/icons";

interface Props { notify: (m: string, k?: "ok" | "err" | "info") => void }

const SCOPES: VariableRow["scope"][] = ["global", "environment", "flow", "brick"];

/**
 * Two halves of one concern: connection points *feed* an execution, the journal
 * *observes* it. Kept in one view because that is the question an operator
 * actually asks — what did this run use, and what became of it.
 */
export function OpsPanel({ notify }: Props) {
  const [tab, setTab] = useState<"runs" | "vars">("runs");

  return (
    <div className="ops">
      <nav className="ops-tabs">
        <button className={`tab ${tab === "runs" ? "active" : ""}`} onClick={() => setTab("runs")}>
          <IconLayers size={14} /> Runs
        </button>
        <button className={`tab ${tab === "vars" ? "active" : ""}`} onClick={() => setTab("vars")}>
          <IconCode size={14} /> Référentiel
        </button>
      </nav>
      {tab === "runs" ? <Runs notify={notify} /> : <Vars notify={notify} />}
    </div>
  );
}

/* ── the operations table ───────────────────────────────────────── */
function Runs({ notify }: Props) {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<RunRow | null>(null);
  const [busy, setBusy] = useState("");

  const refresh = useCallback(async () => {
    try {
      const r = await api.listOpsRuns(status);
      setRuns(r.runs); setCounts(r.counts);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  }, [status, notify]);
  useEffect(() => { refresh(); }, [refresh]);

  const replay = async (run: RunRow, mode: "same_data" | "refetch") => {
    setBusy(run.id);
    try {
      const r = await api.replayRun(run.id, mode);
      notify(mode === "same_data"
        ? `Replayed on the recorded data — ${r.run.status}.`
        : `Re-executed with fresh data — ${r.run.status}.`,
        r.run.status === "success" ? "ok" : "err");
      await refresh();
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setBusy(""); }
  };

  return (
    <div className="ops-body">
      <div className="ops-bar">
        {["", "running", "success", "error"].map((sv) => (
          <button key={sv} className={`ops-chip ${status === sv ? "on" : ""} ${sv}`}
                  onClick={() => setStatus(sv)}>
            {sv === "" ? "all" : sv}
            {sv && <span className="ops-n">{counts[sv] ?? 0}</span>}
          </button>
        ))}
        <button className="btn sm" onClick={refresh}><IconReset size={12} /> Refresh</button>
      </div>

      {runs.length === 0 && <p className="ops-hint">No run yet in this environment.</p>}

      <table className="ops-table">
        <thead>
          <tr><th>Flow</th><th>Status</th><th>Rows</th><th>Time</th>
              <th>Started</th><th>Replay</th></tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <>
              <tr key={r.id} className={`ops-row ${r.status}`}
                  onClick={async () => {
                    if (open?.id === r.id) { setOpen(null); return; }
                    try { setOpen(await api.getOpsRun(r.id)); }
                    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>
                <td>
                  <strong>{r.graph_name}</strong>
                  {r.replay_of && <span className="ops-tag">replay · {r.replay_mode}</span>}
                </td>
                <td><span className={`ops-badge ${r.status}`}>{r.status}</span></td>
                <td>{r.rows_out}</td>
                <td>{r.ms} ms</td>
                <td className="ops-when">{r.started_at.replace("T", " ").slice(0, 16)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <button className="btn sm" disabled={busy === r.id || !r.has_snapshot}
                          title={r.has_snapshot
                            ? "Reproduce the exact run: the API is not called again"
                            : "No input was recorded for this run"}
                          onClick={() => replay(r, "same_data")}>same data</button>
                  <button className="btn sm" disabled={busy === r.id}
                          title="Call the sources again — the API is re-queried, the trigger re-checked"
                          onClick={() => replay(r, "refetch")}>re-fetch</button>
                </td>
              </tr>
              {open?.id === r.id && (
                <tr key={`${r.id}-d`} className="ops-detail">
                  <td colSpan={6}>
                    {open.error && (
                      <p className="ops-err">
                        <IconWarn size={13} /> <code>{open.error_node}</code> {open.error}
                      </p>
                    )}
                    {open.messages.length > 0 && (
                      <div className="ops-msgs">
                        {open.messages.map((m, i) => (
                          <span key={i} className={`ops-msg ${m.level}`}>
                            <code>{m.node}</code> {m.text}
                          </span>
                        ))}
                      </div>
                    )}
                    <table className="ops-steps">
                      <thead><tr><th>#</th><th>Brick</th><th>Type</th><th>Status</th>
                                 <th>Rows</th><th>ms</th><th>Detail</th></tr></thead>
                      <tbody>
                        {(open.steps ?? []).map((st) => (
                          <tr key={st.ordinal} className={st.status}>
                            <td>{st.ordinal + 1}</td>
                            <td><strong>{st.node_id}</strong> {st.label && <em>{st.label}</em>}</td>
                            <td>{st.type}</td>
                            <td><span className={`ops-badge ${st.status}`}>{st.status}</span></td>
                            <td>{st.rows}</td>
                            <td>{st.ms}</td>
                            <td className="ops-meta">
                              {st.message || Object.entries(st.meta)
                                .slice(0, 3)
                                .map(([k, v]) => `${k}=${typeof v === "object" ? "…" : String(v)}`)
                                .join(" · ")}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── connection points ──────────────────────────────────────────── */
const KINDS: VariableRow["kind"][] = ["value", "hotfolder", "smtp"];

/** Read-only summary for a structured kind, instead of dumping its raw JSON
 * into the table. Falls back to the JSON text if it does not parse (e.g. a
 * masked secret). */
function summarize(v: VariableRow): string {
  if (v.kind === "value") return v.value;
  try {
    const data = JSON.parse(v.value) as Record<string, unknown>;
    if (v.kind === "hotfolder") return `${data.path} · ${data.archive_dir} · ${data.error_dir}`;
    if (v.kind === "smtp") return `${data.host}${data.port ? `:${data.port}` : ""}`;
  } catch { /* masked or malformed — show the raw text below */ }
  return v.value;
}

function Vars({ notify }: Props) {
  const [rows, setRows] = useState<VariableRow[]>([]);
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const [envs, setEnvs] = useState<string[]>([]);
  const [draft, setDraft] = useState<Partial<VariableRow>>({
    name: "", value: "", scope: "environment", secret: false, kind: "value",
  });
  // Structured fields for hotfolder/smtp, serialised into draft.value on save.
  const [conn, setConn] = useState<Record<string, string>>({});
  const [connUseTls, setConnUseTls] = useState(true);
  const [restrictFor, setRestrictFor] = useState<string>("");   // variable id
  const [restrictions, setRestrictions] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    try {
      setRows(await api.listVariables());
      setResolved((await api.resolvedVariables()).variables);
      setEnvs((await api.listEnvironments()).environments);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  }, [notify]);
  useEffect(() => { refresh(); }, [refresh]);

  const resetDraft = (scope = draft.scope) =>
    setDraft({ name: "", value: "", scope, secret: false, kind: "value" });

  const edit = (v: VariableRow) => {
    setDraft({ ...v });
    if (v.kind !== "value") {
      try {
        const data = JSON.parse(v.value) as Record<string, unknown>;
        setConn(Object.fromEntries(Object.entries(data).map(([k, val]) => [k, String(val ?? "")])));
        setConnUseTls(data.use_tls !== false);
      } catch { setConn({}); }
    } else {
      setConn({});
    }
  };

  const buildValue = (): string => {
    if (draft.kind === "hotfolder") {
      return JSON.stringify({ path: conn.path || "", archive_dir: conn.archive_dir || "",
                             error_dir: conn.error_dir || "" });
    }
    if (draft.kind === "smtp") {
      return JSON.stringify({
        host: conn.host || "", port: conn.port ? Number(conn.port) : undefined,
        user: conn.user || "", password: conn.password || "",
        use_tls: connUseTls, from: conn.from || "",
      });
    }
    return draft.value ?? "";
  };

  const openRestrictions = async (v: VariableRow) => {
    if (restrictFor === v.id) { setRestrictFor(""); return; }
    try {
      setRestrictions((await api.listVariableRestrictions(v.id)).environments);
      setRestrictFor(v.id);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  return (
    <div className="ops-body">
      <p className="ops-hint">
        A value that several flows share — a base URL, a threshold, a key, or a
        structured connection (a hotfolder, an smtp relay). The same name
        resolves differently depending on where it is read, most specific
        winning: <strong>brick → flow → environment → global</strong>. Nothing
        has to be renamed to be specialised.
      </p>

      <div className="ops-form">
        <input placeholder="name" value={draft.name ?? ""}
               onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <select value={draft.kind} onChange={(e) => {
          setDraft({ ...draft, kind: e.target.value as VariableRow["kind"] });
          setConn({}); setConnUseTls(true);
        }}>
          {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        {draft.kind === "value" && (
          <input placeholder="value" value={draft.value ?? ""}
                 onChange={(e) => setDraft({ ...draft, value: e.target.value })} />
        )}
        <select value={draft.scope} onChange={(e) =>
          setDraft({ ...draft, scope: e.target.value as VariableRow["scope"] })}>
          {SCOPES.map((sc) => <option key={sc} value={sc}>{sc}</option>)}
        </select>
        {(draft.scope === "flow" || draft.scope === "brick") && (
          <input placeholder="graph id" value={draft.graph_id ?? ""}
                 onChange={(e) => setDraft({ ...draft, graph_id: e.target.value })} />
        )}
        {draft.scope === "brick" && (
          <input placeholder="node id" value={draft.node_id ?? ""}
                 onChange={(e) => setDraft({ ...draft, node_id: e.target.value })} />
        )}
        <label className="ops-check">
          <input type="checkbox" checked={!!draft.secret}
                 onChange={(e) => setDraft({ ...draft, secret: e.target.checked })} /> secret
        </label>
        <button className="btn sm" disabled={!draft.name?.trim()}
                onClick={async () => {
                  try {
                    await api.saveVariable({ ...draft, value: buildValue() });
                    resetDraft();
                    await refresh();
                    notify("Connection point saved.", "ok");
                  } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                }}><IconSave size={12} /> Save</button>
        {draft.name && (
          <button className="btn sm" onClick={() => resetDraft()}>Clear</button>
        )}
      </div>

      {draft.kind === "hotfolder" && (
        <div className="ops-form">
          <input placeholder="path" value={conn.path ?? ""}
                 onChange={(e) => setConn({ ...conn, path: e.target.value })} />
          <input placeholder="archive_dir" value={conn.archive_dir ?? ""}
                 onChange={(e) => setConn({ ...conn, archive_dir: e.target.value })} />
          <input placeholder="error_dir" value={conn.error_dir ?? ""}
                 onChange={(e) => setConn({ ...conn, error_dir: e.target.value })} />
        </div>
      )}
      {draft.kind === "smtp" && (
        <div className="ops-form">
          <input placeholder="host" value={conn.host ?? ""}
                 onChange={(e) => setConn({ ...conn, host: e.target.value })} />
          <input placeholder="port (587)" value={conn.port ?? ""}
                 onChange={(e) => setConn({ ...conn, port: e.target.value })} />
          <input placeholder="user" value={conn.user ?? ""}
                 onChange={(e) => setConn({ ...conn, user: e.target.value })} />
          <input placeholder="password" type="password" value={conn.password ?? ""}
                 onChange={(e) => setConn({ ...conn, password: e.target.value })} />
          <input placeholder="from" value={conn.from ?? ""}
                 onChange={(e) => setConn({ ...conn, from: e.target.value })} />
          <label className="ops-check">
            <input type="checkbox" checked={connUseTls}
                   onChange={(e) => setConnUseTls(e.target.checked)} /> tls
          </label>
        </div>
      )}

      <h4><IconCheck size={13} /> What a brick would see here</h4>
      <div className="ops-resolved">
        {Object.keys(resolved).length === 0 && <span className="ops-hint">Nothing defined.</span>}
        {Object.entries(resolved).map(([k, v]) => (
          <span key={k} className="ops-kv"><code>{k}</code> {v}</span>
        ))}
      </div>

      <table className="ops-table">
        <thead><tr><th>Name</th><th>Kind</th><th>Scope</th><th>Value</th><th>Where</th><th /></tr></thead>
        <tbody>
          {rows.map((v) => (
            <>
              <tr key={v.id} className="ops-row" onClick={() => edit(v)}>
                <td><code>{v.name}</code></td>
                <td><span className="ops-badge">{v.kind}</span></td>
                <td><span className={`ops-badge scope-${v.scope}`}>{v.scope}</span></td>
                <td className={v.secret ? "ops-secret" : ""}>{summarize(v)}</td>
                <td className="ops-when">
                  {[v.environment, v.graph_id && `flow ${v.graph_id.slice(0, 6)}`, v.node_id]
                    .filter(Boolean).join(" · ") || "—"}
                </td>
                <td onClick={(e) => e.stopPropagation()}>
                  {v.scope === "global" && (
                    <button className="btn sm" onClick={() => openRestrictions(v)}>
                      restrict
                    </button>
                  )}
                  <button className="btn sm" onClick={async () => {
                    try { await api.deleteVariable(v.id); await refresh(); }
                    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>×</button>
                </td>
              </tr>
              {restrictFor === v.id && (
                <tr key={`${v.id}-r`} className="ops-detail">
                  <td colSpan={6}>
                    <p className="ops-hint">
                      Sans restriction, « {v.name} » est visible partout. Cochez
                      des environnements pour la limiter à ceux-là uniquement.
                    </p>
                    <div className="ops-resolved">
                      {envs.map((e) => (
                        <label key={e} className="ops-check">
                          <input type="checkbox" checked={restrictions.includes(e)}
                                 onChange={async (ev) => {
                                   try {
                                     if (ev.target.checked) {
                                       await api.setVariableRestriction(v.id, e);
                                     } else {
                                       await api.removeVariableRestriction(v.id, e);
                                     }
                                     setRestrictions((await api.listVariableRestrictions(v.id)).environments);
                                   } catch (err) {
                                     notify(err instanceof Error ? err.message : String(err), "err");
                                   }
                                 }} />
                          {e}
                        </label>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}
