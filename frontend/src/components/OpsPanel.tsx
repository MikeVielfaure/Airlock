import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RunRow, VariableRow, VariableSchema } from "../lib/types";
import {
  IconCheck, IconCode, IconLayers, IconReset, IconSave, IconWarn,
} from "../lib/icons";

interface Props { notify: (m: string, k?: "ok" | "err" | "info") => void }

const SCOPES: VariableRow["scope"][] = ["global", "environment", "flow", "brick"];
const STATUS_LABELS: Record<string, string> = { running: "en cours", success: "réussi", error: "erreur" };

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
          <IconLayers size={14} /> Exécutions
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
        ? `Rejoué sur les données enregistrées — ${STATUS_LABELS[r.run.status] ?? r.run.status}.`
        : `Ré-exécuté avec des données fraîches — ${STATUS_LABELS[r.run.status] ?? r.run.status}.`,
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
            {sv === "" ? "tous" : STATUS_LABELS[sv] ?? sv}
            {sv && <span className="ops-n">{counts[sv] ?? 0}</span>}
          </button>
        ))}
        <button className="btn sm" onClick={refresh}><IconReset size={12} /> Actualiser</button>
      </div>

      {runs.length === 0 && <p className="ops-hint">Aucune exécution pour l'instant dans cet environnement.</p>}

      <table className="ops-table">
        <thead>
          <tr><th>Flux</th><th>Statut</th><th>Lignes</th><th>Durée</th>
              <th>Démarré</th><th>Rejouer</th></tr>
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
                  {r.replay_of && <span className="ops-tag">rejeu · {r.replay_mode}</span>}
                </td>
                <td><span className={`ops-badge ${r.status}`}>{STATUS_LABELS[r.status] ?? r.status}</span></td>
                <td>{r.rows_out}</td>
                <td>{r.ms} ms</td>
                <td className="ops-when">{r.started_at.replace("T", " ").slice(0, 16)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <button className="btn sm" disabled={busy === r.id || !r.has_snapshot}
                          title={r.has_snapshot
                            ? "Reproduit l'exécution à l'identique : l'API n'est pas rappelée"
                            : "Aucune entrée n'a été enregistrée pour cette exécution"}
                          onClick={() => replay(r, "same_data")}>mêmes données</button>
                  <button className="btn sm" disabled={busy === r.id}
                          title="Rappelle les sources — l'API est re-interrogée, le déclencheur revérifié"
                          onClick={() => replay(r, "refetch")}>ré-interroger</button>
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
                      <thead><tr><th>#</th><th>Brique</th><th>Type</th><th>Statut</th>
                                 <th>Lignes</th><th>ms</th><th>Détail</th></tr></thead>
                      <tbody>
                        {(open.steps ?? []).map((st) => (
                          <tr key={st.ordinal} className={st.status}>
                            <td>{st.ordinal + 1}</td>
                            <td><strong>{st.node_id}</strong> {st.label && <em>{st.label}</em>}</td>
                            <td>{st.type}</td>
                            <td><span className={`ops-badge ${st.status}`}>{STATUS_LABELS[st.status] ?? st.status}</span></td>
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
const KINDS: VariableRow["kind"][] = ["value", "hotfolder", "smtp", "external_db", "sftp", "api"];

/** Read-only summary for a structured kind, instead of dumping its raw JSON
 * into the table. Falls back to the JSON text if it does not parse (e.g. a
 * masked secret). */
function summarize(v: VariableRow): string {
  if (v.kind === "value") return v.value;
  try {
    const data = JSON.parse(v.value) as Record<string, unknown>;
    if (v.kind === "hotfolder") return `${data.path} · ${data.archive_dir} · ${data.error_dir}`;
    if (v.kind === "smtp") return `${data.host}${data.port ? `:${data.port}` : ""}`;
    if (v.kind === "external_db") return String(data.url ?? "").replace(/:\/\/[^@]*@/, "://***@");
    if (v.kind === "sftp") return `${data.host}${data.port ? `:${data.port}` : ""} · ${data.remote_dir}`;
    if (v.kind === "api") return String(data.base_url ?? "");
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
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ value: true });
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);

  // Known tables (external_db) / endpoints (api) declared on the connection
  // being edited — only meaningful once it has an id (a schema needs a real
  // connection to hang off), reloaded whenever that id changes.
  const [schemas, setSchemas] = useState<VariableSchema[]>([]);
  const [schemaName, setSchemaName] = useState("");
  const [schemaCols, setSchemaCols] = useState<{ name: string; type: string }[]>([{ name: "", type: "string" }]);
  const [schemaPath, setSchemaPath] = useState("");
  const [schemaMethod, setSchemaMethod] = useState("GET");
  const [schemaDataPath, setSchemaDataPath] = useState("");
  const [schemaDetectQuery, setSchemaDetectQuery] = useState("");
  const [detecting, setDetecting] = useState(false);

  const refreshSchemas = useCallback(async () => {
    if (!draft.id) { setSchemas([]); return; }
    try { setSchemas(await api.listConnectionSchemas(draft.id)); }
    catch { setSchemas([]); }
  }, [draft.id]);
  useEffect(() => { refreshSchemas(); }, [refreshSchemas]);

  const resetSchemaForm = () => {
    setSchemaName(""); setSchemaCols([{ name: "", type: "string" }]);
    setSchemaPath(""); setSchemaMethod("GET"); setSchemaDataPath("");
    setSchemaDetectQuery("");
  };

  /** Run the query/call once and propose columns — a starting point to
   * correct by hand, not a substitute for reviewing it. */
  const detectSchema = async () => {
    if (!draft.id) return;
    setDetecting(true);
    try {
      const body = draft.kind === "api"
        ? { path: schemaPath, method: schemaMethod, data_path: schemaDataPath }
        : { query: schemaDetectQuery };
      const r = await api.detectConnectionSchema(draft.id, body);
      if (r.columns.length === 0) { notify("Aucune colonne trouvée dans la réponse.", "err"); return; }
      setSchemaCols(r.columns);
      notify(`${r.columns.length} colonne(s) détectée(s) — vérifiez les types avant d'enregistrer.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setDetecting(false); }
  };

  const saveSchema = async () => {
    if (!draft.id || !schemaName.trim()) return;
    const cols = schemaCols.filter((c) => c.name.trim());
    try {
      await api.saveConnectionSchema(draft.id, {
        name: schemaName.trim(), columns: cols, query: schemaDetectQuery,
        path: schemaPath, method: schemaMethod, data_path: schemaDataPath,
      });
      resetSchemaForm();
      refreshSchemas();
      notify(`Schéma « ${schemaName.trim()} » enregistré.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  const removeSchema = async (name: string) => {
    if (!draft.id) return;
    try { await api.deleteConnectionSchema(draft.id, name); refreshSchemas(); }
    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  const refresh = useCallback(async () => {
    try {
      setRows(await api.listVariables());
      setResolved((await api.resolvedVariables()).variables);
      setEnvs((await api.listEnvironments()).environments);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  }, [notify]);
  useEffect(() => { refresh(); }, [refresh]);

  const resetDraft = (scope = draft.scope) => {
    setDraft({ name: "", value: "", scope, secret: false, kind: "value" });
    setTestResult(null);
    resetSchemaForm();
  };

  const startNew = (kind: VariableRow["kind"]) => {
    setDraft({ name: "", value: "", scope: "environment", secret: false, kind });
    setConn({}); setConnUseTls(true); setTestResult(null);
    resetSchemaForm();
  };

  const edit = (v: VariableRow) => {
    setDraft({ ...v });
    setTestResult(null);
    resetSchemaForm();
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
    if (draft.kind === "external_db") {
      return JSON.stringify({ url: conn.url || "" });
    }
    if (draft.kind === "sftp") {
      return JSON.stringify({
        host: conn.host || "", port: conn.port ? Number(conn.port) : undefined,
        user: conn.user || "", password: conn.password || "",
        private_key: conn.private_key || "", remote_dir: conn.remote_dir || "",
        archive_dir: conn.archive_dir || "", error_dir: conn.error_dir || "",
      });
    }
    if (draft.kind === "api") {
      return JSON.stringify({
        base_url: conn.base_url || "", auth_header: conn.auth_header || "",
        token: conn.token || "",
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

  const testConn = async () => {
    setTesting(true); setTestResult(null);
    try {
      setTestResult(await api.testConnection(draft.kind ?? "value", buildValue()));
    } catch (e) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally { setTesting(false); }
  };

  const itemsFor = (kind: VariableRow["kind"]) => {
    const q = search.trim().toLowerCase();
    return rows.filter((v) => v.kind === kind && (!q || v.name.toLowerCase().includes(q)));
  };

  return (
    <div className="ops-body">
      <p className="ops-hint">
        Une valeur que plusieurs flux partagent — une URL de base, un seuil,
        une clé, ou une connexion structurée (un hotfolder, un relais smtp).
        Le même nom se résout différemment selon où il est lu, le plus
        spécifique l'emportant : <strong>brique → flux → environnement →
        global</strong>. Rien à renommer pour se spécialiser.
      </p>

      <div className="ops-referentiel">
        <aside className="ops-kinds">
          <input className="mono-input ops-kinds-search" placeholder="Rechercher…"
                 value={search} onChange={(e) => setSearch(e.target.value)} />
          {KINDS.map((k) => {
            const items = itemsFor(k);
            const isOpen = search.trim() !== "" || (expanded[k] ?? false);
            return (
              <div key={k} className="ops-kind-node">
                <div className="ops-kind-head" onClick={() => setExpanded((x) => ({ ...x, [k]: !isOpen }))}>
                  <span className="ops-kind-caret">{isOpen ? "▾" : "▸"}</span>
                  <span className="ops-kind-name">{k}</span>
                  <span className="ops-kind-count">{items.length}</span>
                  <button className="btn sm" title={`Nouvelle variable « ${k} »`}
                          onClick={(e) => { e.stopPropagation(); startNew(k); }}>+</button>
                </div>
                {isOpen && (
                  <div className="ops-kind-list">
                    {items.length === 0 ? (
                      <p className="ops-hint ops-kind-empty">Aucune.</p>
                    ) : items.map((v) => (
                      <div key={v.id}
                           className={`ops-kind-item ${draft.id === v.id ? "on" : ""}`}
                           onClick={() => edit(v)} title={summarize(v)}>
                        <span className={`ops-badge scope-${v.scope}`} title={v.scope} />
                        <span className="ops-kind-item-text">
                          <span className="ops-kind-item-name">{v.name}</span>
                          <span className="ops-kind-item-sub">{summarize(v)}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </aside>

        <div className="ops-editor">
          <div className="ops-form">
            <input placeholder="name" value={draft.name ?? ""}
                   onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <select value={draft.kind} onChange={(e) => {
              setDraft({ ...draft, kind: e.target.value as VariableRow["kind"] });
              setConn({}); setConnUseTls(true); setTestResult(null);
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
            <button className="btn sm primary" disabled={!draft.name?.trim()}
                    onClick={async () => {
                      try {
                        await api.saveVariable({ ...draft, value: buildValue() });
                        resetDraft();
                        await refresh();
                        notify("Point de connexion enregistré.", "ok");
                      } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                    }}><IconSave size={12} /> Enregistrer</button>
            {draft.name && (
              <button className="btn sm" onClick={() => resetDraft()}>Réinitialiser</button>
            )}
            {draft.id && (
              <button className="btn sm danger" onClick={async () => {
                try {
                  await api.deleteVariable(draft.id!);
                  resetDraft();
                  await refresh();
                } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
              }}>Supprimer</button>
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
          {draft.kind === "external_db" && (
            <div className="ops-form">
              <input placeholder="postgresql+psycopg2://user:pass@host:5432/db" value={conn.url ?? ""}
                     style={{ minWidth: 340 }}
                     onChange={(e) => setConn({ ...conn, url: e.target.value })} />
            </div>
          )}
          {draft.kind === "sftp" && (
            <div className="ops-form">
              <input placeholder="host" value={conn.host ?? ""}
                     onChange={(e) => setConn({ ...conn, host: e.target.value })} />
              <input placeholder="port (22)" value={conn.port ?? ""}
                     onChange={(e) => setConn({ ...conn, port: e.target.value })} />
              <input placeholder="user" value={conn.user ?? ""}
                     onChange={(e) => setConn({ ...conn, user: e.target.value })} />
              <input placeholder="password" type="password" value={conn.password ?? ""}
                     onChange={(e) => setConn({ ...conn, password: e.target.value })} />
              <input placeholder="private_key (facultatif, texte PEM)" value={conn.private_key ?? ""}
                     onChange={(e) => setConn({ ...conn, private_key: e.target.value })} />
              <input placeholder="remote_dir" value={conn.remote_dir ?? ""}
                     onChange={(e) => setConn({ ...conn, remote_dir: e.target.value })} />
              <input placeholder="archive_dir" value={conn.archive_dir ?? ""}
                     onChange={(e) => setConn({ ...conn, archive_dir: e.target.value })} />
              <input placeholder="error_dir" value={conn.error_dir ?? ""}
                     onChange={(e) => setConn({ ...conn, error_dir: e.target.value })} />
            </div>
          )}
          {draft.kind === "api" && (
            <div className="ops-form">
              <input placeholder="base_url" value={conn.base_url ?? ""} style={{ minWidth: 260 }}
                     onChange={(e) => setConn({ ...conn, base_url: e.target.value })} />
              <input placeholder="auth_header (Authorization)" value={conn.auth_header ?? ""}
                     onChange={(e) => setConn({ ...conn, auth_header: e.target.value })} />
              <input placeholder="token" type="password" value={conn.token ?? ""}
                     onChange={(e) => setConn({ ...conn, token: e.target.value })} />
            </div>
          )}

          {(draft.kind === "external_db" || draft.kind === "api") && (
            <div className="ops-schemas">
              <div className="ops-h3">{draft.kind === "api" ? "Endpoints connus" : "Tables connues"}</div>
              {!draft.id ? (
                <p className="ops-hint">Enregistrez d'abord la connexion pour pouvoir y déclarer
                  {draft.kind === "api" ? " des endpoints" : " des tables"} connu(e)s, réutilisables
                  ensuite pour construire une requête et proposer l'autocomplétion.</p>
              ) : (
                <>
                  {schemas.length === 0 ? (
                    <p className="ops-hint">Aucun{draft.kind === "api" ? " endpoint" : "e table"} déclaré(e) pour l'instant.</p>
                  ) : (
                    schemas.map((sc) => (
                      <div key={sc.name} className="ops-schema-row">
                        <strong>{sc.name}</strong>
                        <span className="ops-hint">
                          {sc.columns.map((c) => `${c.name}:${c.type}`).join(", ") || "aucune colonne"}
                          {draft.kind === "api" && sc.path ? ` · ${sc.method} ${sc.path}` : ""}
                        </span>
                        <button className="btn sm" onClick={() => removeSchema(sc.name)}>Supprimer</button>
                      </div>
                    ))
                  )}
                  <div className="ops-form" style={{ marginTop: 8 }}>
                    <input placeholder={draft.kind === "api" ? "nom de l'endpoint" : "nom de la table"}
                           value={schemaName} onChange={(e) => setSchemaName(e.target.value)} />
                    {draft.kind === "api" && (
                      <>
                        <input placeholder="path" value={schemaPath}
                               onChange={(e) => setSchemaPath(e.target.value)} />
                        <select value={schemaMethod} onChange={(e) => setSchemaMethod(e.target.value)}>
                          {["GET", "POST", "PUT", "DELETE"].map((m) => <option key={m} value={m}>{m}</option>)}
                        </select>
                        <input placeholder="chemin dans la réponse (optionnel)" value={schemaDataPath}
                               onChange={(e) => setSchemaDataPath(e.target.value)} />
                      </>
                    )}
                  </div>
                  {draft.kind === "external_db" && (
                    <div className="ops-form">
                      <input placeholder="requête, ex. SELECT * FROM ma_table — enregistrée avec le schéma"
                             style={{ flex: 1 }} value={schemaDetectQuery}
                             onChange={(e) => setSchemaDetectQuery(e.target.value)} />
                    </div>
                  )}
                  <button className="btn sm" disabled={detecting ||
                            (draft.kind === "external_db" && !schemaDetectQuery.trim())}
                          onClick={detectSchema}>
                    {detecting ? "Détection…" : "Détecter les colonnes"}
                  </button>
                  <span className="ops-hint">Colonnes attendues</span>
                  {schemaCols.map((c, i) => (
                    <div className="sql-pair" key={i}>
                      <input placeholder="nom de colonne" value={c.name}
                             onChange={(e) => setSchemaCols(schemaCols.map((x, j) =>
                               (j === i ? { ...x, name: e.target.value } : x)))} />
                      <select value={c.type} onChange={(e) => setSchemaCols(schemaCols.map((x, j) =>
                        (j === i ? { ...x, type: e.target.value } : x)))}>
                        {["string", "integer", "float", "date", "boolean"].map((t) =>
                          <option key={t} value={t}>{t}</option>)}
                      </select>
                      <button className="hclear" onClick={() => setSchemaCols(schemaCols.filter((_, j) => j !== i))}>×</button>
                    </div>
                  ))}
                  <button className="btn sm" onClick={() => setSchemaCols([...schemaCols, { name: "", type: "string" }])}>
                    + colonne
                  </button>
                  <button className="btn sm primary" style={{ marginLeft: 8 }}
                          disabled={!schemaName.trim()} onClick={saveSchema}>
                    Enregistrer le schéma
                  </button>
                </>
              )}
            </div>
          )}

          {draft.kind !== "value" && (
            <div className="ops-test">
              <button className="btn sm" disabled={testing} onClick={testConn}>
                {testing ? "Test en cours…" : "Tester la connexion"}
              </button>
              {testResult && (
                <span className={`ops-test-result ${testResult.ok ? "ok" : "err"}`}>
                  {testResult.message}
                </span>
              )}
            </div>
          )}

          {draft.id && draft.scope === "global" && (
            <div className="ops-share">
              <h4>Partage</h4>
              <p className="ops-hint">
                Sans restriction, « {draft.name} » est visible partout. Cochez
                des environnements pour la limiter à ceux-là uniquement.
              </p>
              <button className="btn sm" onClick={() => openRestrictions(draft as VariableRow)}>
                {restrictFor === draft.id ? "Masquer" : "Gérer le partage"}
              </button>
              {restrictFor === draft.id && (
                <div className="ops-resolved">
                  {envs.map((e) => (
                    <label key={e} className="ops-check">
                      <input type="checkbox" checked={restrictions.includes(e)}
                             onChange={async (ev) => {
                               try {
                                 if (ev.target.checked) {
                                   await api.setVariableRestriction(draft.id!, e);
                                 } else {
                                   await api.removeVariableRestriction(draft.id!, e);
                                 }
                                 setRestrictions((await api.listVariableRestrictions(draft.id!)).environments);
                               } catch (err) {
                                 notify(err instanceof Error ? err.message : String(err), "err");
                               }
                             }} />
                      {e}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <h4><IconCheck size={13} /> What a brick would see here</h4>
      <div className="ops-resolved">
        {Object.keys(resolved).length === 0 && <span className="ops-hint">Rien de défini.</span>}
        {Object.entries(resolved).map(([k, v]) => (
          <span key={k} className="ops-kv"><code>{k}</code> {v}</span>
        ))}
      </div>
    </div>
  );
}
