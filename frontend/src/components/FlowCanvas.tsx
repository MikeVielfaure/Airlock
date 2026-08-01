import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo, DatasetInfo, FileResponse, VariableRow } from "../lib/types";
import {
  IconCheck, IconCode, IconLayers, IconPlay, IconReset, IconSave, IconWarn,
} from "../lib/icons";

/* ────────────────────────────────────────────────────────────────
   A flow is a graph. The canvas is a *view* of that graph — never a
   second source of truth. Every gesture edits the same node/edge
   arrays the YAML serialises, so switching between canvas and text
   cannot drift.
   ──────────────────────────────────────────────────────────────── */

interface Node {
  id: string;
  type: string;
  label?: string;
  config: Record<string, unknown>;
  x: number;
  y: number;
}
interface Edge { from: string; to: string }
interface Brick { type: string; role: "source" | "transform" | "sink" }
interface TraceStep {
  node: string; type: string; label?: string;
  ms: number; records: number; rows: number; meta?: Record<string, unknown>;
}

interface Props {
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  /** Lets the graph's output become an ordinary working session — the same
   * mechanism `DatasetPanel` uses to open a stored table. */
  onOpenSession?: (res: FileResponse) => void;
}

const NODE_W = 150;
const NODE_H = 54;
const GRID = 10;

/** Sensible starting config per brick, so a dropped node is never empty. */
const SEED: Record<string, Record<string, unknown>> = {
  api: { url: "https://", path: "" },
  inline: { rows: [{ exemple: "1" }] },
  dataset: { name: "" },
  compute: { columns: { nouveau: "[a] + [b]" } },
  filter: { where: "IF([a] == 'x', '1', '0')" },
  aggregate: { by: ["cle"], agg: { total: { column: "montant", fn: "sum" } } },
  join: { left: "", right: "", on: ["cle"], how: "left" },
  lookup: { key: "code", ref_key: "code", ref_value: "libelle", into: "libelle", values: {} },
  mapping: { mapping_yaml: "name: m\nsource_kind: flat\nlinks: []\n" },
  validate: { rules: {}, block: false },
  graph: { graph_id: "" },
  config: { config_id: "" },
  dataset_write: { name: "", mode: "replace", key_fields: [] },
  file: { format: "csv", filename: "sortie.csv" },
  response: {},
  hotfolder: { connection: "", file_type: "csv", delimiter: ";", encoding: "AUTO",
               pattern: "*", required: true },
  email: { connection: "", to: "", subject: "", body: "" },
  external_db: { connection: "", query: "SELECT * FROM table1 WHERE id = :id", params: {} },
  external_db_write: { connection: "", table: "", mode: "insert", key_fields: [] },
  sftp: { connection: "", file_type: "csv", delimiter: ";", encoding: "AUTO",
          pattern: "*", required: true },
  sftp_write: { connection: "", filename: "sortie.csv" },
};

const ROLE_CLASS: Record<string, string> = {
  source: "src", transform: "tr", sink: "sink",
};

/** Brick types with a dedicated inspector form instead of the raw JSON
 * textarea — a foundation meant to grow to more types over time. */
const ENRICHED_TYPES = new Set([
  "hotfolder", "config", "dataset_write", "email", "join", "lookup", "dataset",
  "external_db", "external_db_write", "sftp", "sftp_write", "api", "http",
]);

export function FlowCanvas({ notify, onOpenSession }: Props) {
  const [bricks, setBricks] = useState<Brick[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [name, setName] = useState("");
  const [params, setParams] = useState<{ name: string; default: string }[]>([]);

  const [selected, setSelected] = useState<string>("");
  const [linking, setLinking] = useState<string>("");     // node awaiting a target
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number } | null>(null);
  const [trace, setTrace] = useState<TraceStep[]>([]);
  const [failed, setFailed] = useState<string>("");
  const [busy, setBusy] = useState("");
  const [saved, setSaved] = useState<ArtefactInfo[]>([]);
  const [showYaml, setShowYaml] = useState(false);

  // Options for the per-brick inspector fields (hotfolder/smtp connections,
  // stored configs, stored tables) — loaded once, refreshed on demand.
  const [hotfolderConns, setHotfolderConns] = useState<VariableRow[]>([]);
  const [smtpConns, setSmtpConns] = useState<VariableRow[]>([]);
  const [externalDbConns, setExternalDbConns] = useState<VariableRow[]>([]);
  const [sftpConns, setSftpConns] = useState<VariableRow[]>([]);
  const [apiConns, setApiConns] = useState<VariableRow[]>([]);
  const [configs, setConfigs] = useState<ArtefactInfo[]>([]);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [advanced, setAdvanced] = useState<Record<string, boolean>>({});

  const surface = useRef<HTMLDivElement>(null);

  const roleOf = (type: string) =>
    bricks.find((b) => b.type === type)?.role ?? "transform";

  useEffect(() => {
    api.flowBricks().then((r) => setBricks(r.bricks)).catch(() => { /* palette optional */ });
    api.listArtefacts("graph").then(setSaved).catch(() => { /* empty is fine */ });
    api.listVariables("", "", "hotfolder").then(setHotfolderConns).catch(() => {});
    api.listVariables("", "", "smtp").then(setSmtpConns).catch(() => {});
    api.listVariables("", "", "external_db").then(setExternalDbConns).catch(() => {});
    api.listVariables("", "", "sftp").then(setSftpConns).catch(() => {});
    api.listVariables("", "", "api").then(setApiConns).catch(() => {});
    api.listArtefacts("config").then(setConfigs).catch(() => {});
    api.listDatasets().then(setDatasets).catch(() => {});
  }, []);

  /* ── graph edits ─────────────────────────────────────────── */
  const addNode = (type: string) => {
    const base = type.slice(0, 3);
    let n = 1;
    while (nodes.some((x) => x.id === `${base}${n}`)) n++;
    const id = `${base}${n}`;
    setNodes((ns) => [...ns, {
      id, type, config: JSON.parse(JSON.stringify(SEED[type] ?? {})),
      x: 40 + (ns.length % 4) * (NODE_W + 40),
      y: 40 + Math.floor(ns.length / 4) * (NODE_H + 60),
    }]);
    setSelected(id);
  };

  const removeNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.from !== id && e.to !== id));
    if (selected === id) setSelected("");
  };

  const link = (to: string) => {
    if (!linking || linking === to) { setLinking(""); return; }
    setEdges((es) =>
      es.some((e) => e.from === linking && e.to === to) ? es : [...es, { from: linking, to }]);
    setLinking("");
  };

  const patchConfig = (id: string, raw: string) => {
    try {
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, config: JSON.parse(raw) } : n)));
    } catch { /* keep the last valid config while the user is mid-typing */ }
  };

  /** Write one key of a node's config — used by the dedicated per-brick
   * fields, so a select/input and the raw-JSON fallback edit the very same
   * object rather than two copies that could drift apart. */
  const patchField = (id: string, key: string, value: unknown) => {
    setNodes((ns) => ns.map((n) =>
      n.id === id ? { ...n, config: { ...n.config, [key]: value } } : n));
  };

  /* ── serialisation: the canvas and the YAML are one graph ── */
  const toYaml = useCallback(() => {
    const lines: string[] = [`name: ${name || "flux"}`];
    if (params.length) {
      lines.push("params:");
      params.forEach((p) => lines.push(`  - {name: ${p.name}, default: "${p.default}"}`));
    }
    lines.push("nodes:");
    nodes.forEach((n) => {
      lines.push(`  - id: ${n.id}`);
      lines.push(`    type: ${n.type}`);
      // Position is view state, not behaviour — kept so a reopened flow looks
      // the way it was left, ignored by the runner.
      lines.push(`    label: ${JSON.stringify(n.label ?? "")}`);
      lines.push(`    config: ${JSON.stringify({ ...n.config, _xy: [n.x, n.y] })}`);
    });
    if (edges.length) {
      lines.push("edges:");
      edges.forEach((e) => lines.push(`  - {from: ${e.from}, to: ${e.to}}`));
    }
    return lines.join("\n") + "\n";
  }, [name, params, nodes, edges]);

  const loadGraph = (doc: { name?: string; nodes?: Node[]; edges?: Edge[];
                           params?: { name: string; default: string }[] }) => {
    setName(doc.name ?? "");
    setParams(doc.params ?? []);
    setNodes((doc.nodes ?? []).map((n, i) => {
      const xy = (n.config as { _xy?: number[] })?._xy;
      const { _xy, ...clean } = (n.config ?? {}) as Record<string, unknown>;
      return {
        ...n, config: clean,
        x: xy?.[0] ?? 40 + (i % 4) * (NODE_W + 40),
        y: xy?.[1] ?? 40 + Math.floor(i / 4) * (NODE_H + 60),
      };
    }));
    setEdges(doc.edges ?? []);
    setTrace([]); setFailed("");
  };

  /* ── dragging ────────────────────────────────────────────── */
  const onDown = (e: React.MouseEvent, n: Node) => {
    if (linking) { link(n.id); return; }
    const rect = surface.current?.getBoundingClientRect();
    if (!rect) return;
    setDrag({ id: n.id, dx: e.clientX - rect.left - n.x, dy: e.clientY - rect.top - n.y });
    setSelected(n.id);
  };
  const onMove = (e: React.MouseEvent) => {
    if (!drag) return;
    const rect = surface.current?.getBoundingClientRect();
    if (!rect) return;
    const x = Math.max(0, Math.round((e.clientX - rect.left - drag.dx) / GRID) * GRID);
    const y = Math.max(0, Math.round((e.clientY - rect.top - drag.dy) / GRID) * GRID);
    setNodes((ns) => ns.map((n) => (n.id === drag.id ? { ...n, x, y } : n)));
  };

  /* ── running ─────────────────────────────────────────────── */
  const run = async () => {
    setBusy("run"); setFailed(""); setTrace([]);
    try {
      const r = await api.runGraph({ yaml: toYaml() });
      setTrace(r.trace);
      notify(`Flux exécuté — sortie « ${r.output} », ${r.preview.total_rows} ligne(s).`, "ok");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // The runner names the failing node; highlight it on the canvas.
      const m = msg.match(/Node '([^']+)'/);
      if (m) setFailed(m[1]);
      notify(msg, "err");
    } finally { setBusy(""); }
  };

  const adopt = async () => {
    setBusy("adopt"); setFailed("");
    try {
      const res = await api.adoptGraph({ yaml: toYaml() });
      notify(`Session ouverte — ${res.preview.total_rows} ligne(s).`, "ok");
      onOpenSession?.(res);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const m = msg.match(/Node '([^']+)'/);
      if (m) setFailed(m[1]);
      notify(msg, "err");
    } finally { setBusy(""); }
  };

  const check = async () => {
    setBusy("check"); setFailed("");
    try {
      const r = await api.validateFlow({ yaml: toYaml() });
      notify(r.ok
        ? `Valide — ordre : ${r.order.join(" → ")}, sortie « ${r.output} ».`
        : `Type(s) de brique inconnu(s) : ${r.unknown_types.join(", ")}`, r.ok ? "ok" : "err");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setBusy(""); }
  };

  const sel = nodes.find((n) => n.id === selected) ?? null;
  const stepOf = (id: string) => trace.find((t) => t.node === id);

  return (
    <div className="fc">
      {/* palette */}
      <aside className="fc-palette">
        <h4>Briques</h4>
        {(["source", "transform", "sink"] as const).map((role) => (
          <div key={role} className="fc-group">
            <span className="fc-role">{role}</span>
            {bricks.filter((b) => b.role === role).map((b) => (
              <button key={b.type} className={`fc-brick ${ROLE_CLASS[role]}`}
                      onClick={() => addNode(b.type)}>{b.type}</button>
            ))}
          </div>
        ))}
      </aside>

      <div className="fc-main">
        <div className="fc-bar">
          <input value={name} placeholder="nom du flux" onChange={(e) => setName(e.target.value)} />
          <button className="btn sm" disabled={!!busy} onClick={check}>
            <IconCheck size={13} /> Vérifier
          </button>
          <button className="btn" disabled={!!busy || !nodes.length} onClick={run}>
            <IconPlay size={14} /> {busy === "run" ? "En cours…" : "Lancer"}
          </button>
          {onOpenSession && (
            <button className="btn sm" disabled={!!busy || !nodes.length} onClick={adopt}
                    title="Ouvrir le résultat de ce flux comme une session de travail — Schéma, Calculs, Rapport s'appliquent ensuite normalement.">
              {busy === "adopt" ? "Ouverture…" : "Ouvrir comme session"}
            </button>
          )}
          <button className="btn sm" disabled={!name.trim() || !nodes.length}
                  onClick={async () => {
                    try {
                      const ex = saved.find((g) => g.name === name.trim());
                      if (ex) await api.addArtefactVersion("graph", ex.id, { yaml: toYaml() });
                      else await api.createArtefact("graph", { name: name.trim(), yaml: toYaml() });
                      setSaved(await api.listArtefacts("graph"));
                      notify(`Flux « ${name} » enregistré.`, "ok");
                    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>
            <IconSave size={13} /> Enregistrer
          </button>
          <select value="" onChange={async (e) => {
            if (!e.target.value) return;
            try {
              const g = await api.loadGraph(e.target.value);
              loadGraph(g as never);
              notify("Flux chargé.", "ok");
            } catch (err) { notify(err instanceof Error ? err.message : String(err), "err"); }
          }}>
            <option value="">— ouvrir un flux —</option>
            {saved.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
          <button className="btn sm" onClick={() => setShowYaml(!showYaml)}>
            <IconCode size={13} /> {showYaml ? "Canevas" : "YAML"}
          </button>
        </div>

        {showYaml ? (
          <pre className="fc-yaml">{toYaml()}</pre>
        ) : (
          <div className="fc-surface" ref={surface}
               onMouseMove={onMove} onMouseUp={() => setDrag(null)}
               onMouseLeave={() => setDrag(null)}
               onClick={() => { if (linking) setLinking(""); }}>
            {/* wires, drawn under the nodes */}
            <svg className="fc-wires">
              {edges.map((e, i) => {
                const a = nodes.find((n) => n.id === e.from);
                const b = nodes.find((n) => n.id === e.to);
                if (!a || !b) return null;
                const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
                const x2 = b.x, y2 = b.y + NODE_H / 2;
                const mid = Math.max(30, Math.abs(x2 - x1) / 2);
                return (
                  <g key={i} className="fc-wire">
                    <path d={`M${x1},${y1} C${x1 + mid},${y1} ${x2 - mid},${y2} ${x2},${y2}`} />
                    <circle cx={(x1 + x2) / 2} cy={(y1 + y2) / 2} r="7"
                            onClick={(ev) => { ev.stopPropagation();
                              setEdges((es) => es.filter((_, j) => j !== i)); }} />
                  </g>
                );
              })}
            </svg>

            {nodes.map((n) => {
              const step = stepOf(n.id);
              return (
                <div key={n.id}
                     className={`fc-node ${ROLE_CLASS[roleOf(n.type)]}`
                       + (selected === n.id ? " on" : "")
                       + (linking === n.id ? " linking" : "")
                       + (failed === n.id ? " failed" : "")
                       + (step ? " ran" : "")}
                     style={{ left: n.x, top: n.y, width: NODE_W }}
                     onMouseDown={(e) => onDown(e, n)}
                     onClick={(e) => e.stopPropagation()}>
                  <div className="fc-node-head">
                    <strong>{n.id}</strong>
                    <span className="fc-type">{n.type}</span>
                  </div>
                  {step && (
                    <div className="fc-metrics">{step.rows} lignes · {step.ms}ms</div>
                  )}
                  <button className="fc-port" title="Relier à une autre brique"
                          onClick={(e) => { e.stopPropagation(); setLinking(n.id); }}>›</button>
                  <button className="fc-kill" onClick={(e) => { e.stopPropagation(); removeNode(n.id); }}>×</button>
                </div>
              );
            })}

            {nodes.length === 0 && (
              <p className="fc-empty">Choisissez une brique à gauche pour démarrer un flux.</p>
            )}
            {linking && (
              <div className="fc-linking">Cliquez une brique pour y relier <strong>{linking}</strong>.</div>
            )}
          </div>
        )}

        {trace.length > 0 && (
          <div className="fc-trace">
            <h4><IconLayers size={13} /> Exécution</h4>
            <div className="fc-steps">
              {trace.map((t, i) => (
                <span key={i} className="fc-step">
                  <strong>{t.node}</strong> {t.rows} lignes · {t.ms}ms
                  {t.meta ? <em>{Object.entries(t.meta)
                    .filter(([k]) => !["content_base64"].includes(k))
                    .slice(0, 2)
                    .map(([k, v]) => ` ${k}=${typeof v === "object" ? "…" : String(v)}`)}</em> : null}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* inspector */}
      <aside className="fc-inspect">
        {sel ? (
          <>
            <h4>{sel.id} <span className="fc-type">{sel.type}</span></h4>
            <label>Libellé</label>
            <input value={sel.label ?? ""} placeholder="ce que fait cette brique"
                   onChange={(e) => setNodes((ns) =>
                     ns.map((n) => (n.id === sel.id ? { ...n, label: e.target.value } : n)))} />

            {ENRICHED_TYPES.has(sel.type) && (
              <BrickFields node={sel} patchField={patchField} edges={edges}
                          hotfolderConns={hotfolderConns} smtpConns={smtpConns}
                          externalDbConns={externalDbConns} sftpConns={sftpConns}
                          apiConns={apiConns}
                          configs={configs} datasets={datasets} />
            )}

            {(!ENRICHED_TYPES.has(sel.type) || advanced[sel.id]) && (
              <>
                <label>Config{ENRICHED_TYPES.has(sel.type) ? " (avancé)" : ""}</label>
                <textarea className="mono" rows={14}
                          defaultValue={JSON.stringify(sel.config, null, 2)}
                          key={sel.id}
                          onChange={(e) => patchConfig(sel.id, e.target.value)} />
                <p className="fc-hint">
                  Modifié en direct. Un brouillon JSON invalide est ignoré jusqu'à ce
                  qu'il redevienne valide — la saisie ne détruit donc jamais la config.
                </p>
              </>
            )}
            {ENRICHED_TYPES.has(sel.type) && (
              <button className="btn sm" onClick={() =>
                setAdvanced((a) => ({ ...a, [sel.id]: !a[sel.id] }))}>
                {advanced[sel.id] ? "Masquer le JSON" : "Avancé / JSON"}
              </button>
            )}
            {failed === sel.id && (
              <p className="fc-fail"><IconWarn size={12} /> Cette brique a échoué lors de la dernière exécution.</p>
            )}
          </>
        ) : (
          <>
            <h4>Paramètres</h4>
            <p className="fc-hint">
              Des entrées nommées rendent le flux réutilisable — elles forment aussi
              le corps de la requête quand il est appelé comme API.
            </p>
            {params.map((p, i) => (
              <div key={i} className="fc-param">
                <input value={p.name} placeholder="nom"
                       onChange={(e) => setParams((ps) =>
                         ps.map((q, j) => (j === i ? { ...q, name: e.target.value } : q)))} />
                <input value={p.default} placeholder="défaut"
                       onChange={(e) => setParams((ps) =>
                         ps.map((q, j) => (j === i ? { ...q, default: e.target.value } : q)))} />
                <button className="fc-kill" onClick={() =>
                  setParams((ps) => ps.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button className="btn sm" onClick={() => setParams((ps) => [...ps, { name: "", default: "" }])}>
              + paramètre
            </button>
            <button className="btn sm" onClick={() => { setNodes([]); setEdges([]); setTrace([]); }}>
              <IconReset size={13} /> Vider le canevas
            </button>
          </>
        )}
      </aside>
    </div>
  );
}

/* ── dedicated fields for the bricks enriched beyond raw JSON ──────── */
function BrickFields({ node, patchField, edges, hotfolderConns, smtpConns,
                      externalDbConns, sftpConns, apiConns, configs, datasets }: {
  node: Node;
  patchField: (id: string, key: string, value: unknown) => void;
  edges: Edge[];
  hotfolderConns: VariableRow[];
  smtpConns: VariableRow[];
  externalDbConns: VariableRow[];
  sftpConns: VariableRow[];
  apiConns: VariableRow[];
  configs: ArtefactInfo[];
  datasets: DatasetInfo[];
}) {
  const cfg = node.config;
  const set = (key: string, value: unknown) => patchField(node.id, key, value);

  if (node.type === "dataset") {
    return (
      <>
        <label>Table source</label>
        <input list="fc-dataset-names" value={String(cfg.name ?? "")}
               placeholder="nom de la table"
               onChange={(e) => set("name", e.target.value)} />
        <datalist id="fc-dataset-names">
          {datasets.map((d) => <option key={d.id} value={d.name} />)}
        </datalist>
        <label>Limite de lignes (optionnel)</label>
        <input value={String(cfg.limit ?? "")} placeholder="200000"
               onChange={(e) => set("limit", e.target.value)} />
      </>
    );
  }

  if (node.type === "join") {
    // Which parent is which side matters (see _brick_join) — pick them from
    // the nodes actually wired into this one, never free text, so a user
    // can't reference a node that isn't even connected.
    const parents = edges.filter((e) => e.to === node.id).map((e) => e.from);
    const on = Array.isArray(cfg.on) ? (cfg.on as unknown[]).join(", ")
             : String(cfg.on ?? "");
    return (
      <>
        {parents.length < 2 && (
          <p className="fc-hint">
            Connectez au moins deux bricks à celui-ci (bouton « › ») pour
            choisir les entrées gauche et droite.
          </p>
        )}
        <label>Entrée gauche</label>
        <select value={String(cfg.left ?? "")} onChange={(e) => set("left", e.target.value)}>
          <option value="">— choisir —</option>
          {parents.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <label>Entrée droite</label>
        <select value={String(cfg.right ?? "")} onChange={(e) => set("right", e.target.value)}>
          <option value="">— choisir —</option>
          {parents.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <label>Clés communes (séparées par des virgules)</label>
        <input value={on}
               onChange={(e) => set("on",
                 e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        <label>Type de jointure</label>
        <select value={String(cfg.how ?? "left")} onChange={(e) => set("how", e.target.value)}>
          <option value="left">left</option>
          <option value="inner">inner</option>
          <option value="outer">outer</option>
          <option value="right">right</option>
        </select>
      </>
    );
  }

  if (node.type === "lookup") {
    return (
      <>
        <label>Colonne clé</label>
        <input value={String(cfg.key ?? "")} onChange={(e) => set("key", e.target.value)} />
        <label>Table de référence</label>
        <input list="fc-dataset-names" value={String(cfg.dataset ?? "")}
               placeholder="nom de la table"
               onChange={(e) => set("dataset", e.target.value)} />
        <datalist id="fc-dataset-names">
          {datasets.map((d) => <option key={d.id} value={d.name} />)}
        </datalist>
        <label>Clé côté référence</label>
        <input value={String(cfg.ref_key ?? "")} placeholder="par défaut : même que la clé"
               onChange={(e) => set("ref_key", e.target.value)} />
        <label>Colonne à récupérer</label>
        <input value={String(cfg.ref_value ?? "")} onChange={(e) => set("ref_value", e.target.value)} />
        <label>Nouvelle colonne</label>
        <input value={String(cfg.into ?? "")} onChange={(e) => set("into", e.target.value)} />
        <p className="fc-hint">
          Pour une référence tapée à la main plutôt qu'une table, utilisez «
          Avancé / JSON » ci-dessous et renseignez <code>values</code>.
        </p>
      </>
    );
  }

  if (node.type === "hotfolder") {
    const fileType = String(cfg.file_type ?? "csv");
    return (
      <>
        <label>Connexion (hotfolder)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— choisir —</option>
          {hotfolderConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        <label>Type de fichier</label>
        <select value={fileType} onChange={(e) => set("file_type", e.target.value)}>
          <option value="csv">CSV</option>
          <option value="xlsx">Excel (xlsx)</option>
        </select>
        {fileType === "csv" ? (
          <>
            <label>Délimiteur</label>
            <input value={String(cfg.delimiter ?? "")} placeholder=";"
                   onChange={(e) => set("delimiter", e.target.value)} />
            <label>Encodage</label>
            <input value={String(cfg.encoding ?? "AUTO")}
                   onChange={(e) => set("encoding", e.target.value)} />
          </>
        ) : (
          <>
            <label>Feuille</label>
            <input value={String(cfg.sheet ?? "")} placeholder="0"
                   onChange={(e) => set("sheet", e.target.value)} />
          </>
        )}
        <label>Motif de fichier</label>
        <input value={String(cfg.pattern ?? "*")} onChange={(e) => set("pattern", e.target.value)} />
        <label className="check">
          <input type="checkbox" checked={cfg.required !== false}
                 onChange={(e) => set("required", e.target.checked)} />
          Bloquer si aucun fichier n'est trouvé
        </label>
      </>
    );
  }

  if (node.type === "config") {
    return (
      <>
        <label>Configuration stockée</label>
        <select value={String(cfg.config_id ?? "")} onChange={(e) => set("config_id", e.target.value)}>
          <option value="">— choisir —</option>
          {configs.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </>
    );
  }

  if (node.type === "dataset_write") {
    const keyFields = Array.isArray(cfg.key_fields) ? (cfg.key_fields as unknown[]).join(", ") : "";
    return (
      <>
        <label>Table cible</label>
        <input list="fc-dataset-names" value={String(cfg.name ?? "")}
               placeholder="nom (existant ou nouveau)"
               onChange={(e) => set("name", e.target.value)} />
        <datalist id="fc-dataset-names">
          {datasets.map((d) => <option key={d.id} value={d.name} />)}
        </datalist>
        <label>Mode</label>
        <select value={String(cfg.mode ?? "replace")} onChange={(e) => set("mode", e.target.value)}>
          <option value="replace">Remplacer</option>
          <option value="append">Ajouter</option>
        </select>
        <label>Colonnes clé (séparées par des virgules)</label>
        <input value={keyFields}
               onChange={(e) => set("key_fields",
                 e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
      </>
    );
  }

  if (node.type === "email") {
    return (
      <>
        <label>Connexion (smtp)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— choisir —</option>
          {smtpConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        <label>À</label>
        <input value={String(cfg.to ?? "")} placeholder="destinataire@exemple.fr"
               onChange={(e) => set("to", e.target.value)} />
        <label>Sujet</label>
        <input value={String(cfg.subject ?? "")} onChange={(e) => set("subject", e.target.value)} />
        <label>Corps</label>
        <textarea rows={6} value={String(cfg.body ?? "")}
                  onChange={(e) => set("body", e.target.value)} />
      </>
    );
  }

  if (node.type === "external_db") {
    return (
      <>
        <label>Connexion (base externe)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— choisir —</option>
          {externalDbConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        <label>Requête (paramètres liés : <code>:nom</code>)</label>
        <textarea rows={4} className="mono" value={String(cfg.query ?? "")}
                  onChange={(e) => set("query", e.target.value)} />
        <p className="fc-hint">
          Les valeurs de <code>params</code> (onglet Avancé / JSON) sont liées à
          la requête — jamais insérées comme texte.
        </p>
      </>
    );
  }

  if (node.type === "external_db_write") {
    const keyFields = Array.isArray(cfg.key_fields) ? (cfg.key_fields as unknown[]).join(", ") : "";
    return (
      <>
        <label>Connexion (base externe)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— choisir —</option>
          {externalDbConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        <label>Table cible</label>
        <input value={String(cfg.table ?? "")} placeholder="table existante"
               onChange={(e) => set("table", e.target.value)} />
        <label>Mode</label>
        <select value={String(cfg.mode ?? "insert")} onChange={(e) => set("mode", e.target.value)}>
          <option value="insert">Insérer</option>
          <option value="upsert">Upsert</option>
        </select>
        {cfg.mode === "upsert" && (
          <>
            <label>Colonnes clé (séparées par des virgules)</label>
            <input value={keyFields}
                   onChange={(e) => set("key_fields",
                     e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
          </>
        )}
      </>
    );
  }

  if (node.type === "sftp") {
    const fileType = String(cfg.file_type ?? "csv");
    return (
      <>
        <label>Connexion (sftp)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— choisir —</option>
          {sftpConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        <label>Type de fichier</label>
        <select value={fileType} onChange={(e) => set("file_type", e.target.value)}>
          <option value="csv">CSV</option>
          <option value="xlsx">Excel (xlsx)</option>
        </select>
        {fileType === "csv" ? (
          <>
            <label>Délimiteur</label>
            <input value={String(cfg.delimiter ?? "")} placeholder=";"
                   onChange={(e) => set("delimiter", e.target.value)} />
            <label>Encodage</label>
            <input value={String(cfg.encoding ?? "AUTO")}
                   onChange={(e) => set("encoding", e.target.value)} />
          </>
        ) : (
          <>
            <label>Feuille</label>
            <input value={String(cfg.sheet ?? "")} placeholder="0"
                   onChange={(e) => set("sheet", e.target.value)} />
          </>
        )}
        <label>Motif de fichier</label>
        <input value={String(cfg.pattern ?? "*")} onChange={(e) => set("pattern", e.target.value)} />
        <label className="check">
          <input type="checkbox" checked={cfg.required !== false}
                 onChange={(e) => set("required", e.target.checked)} />
          Bloquer si aucun fichier n'est trouvé
        </label>
      </>
    );
  }

  if (node.type === "api" || node.type === "http") {
    return (
      <>
        <label>Connexion (api, optionnel)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— aucune : url tapée ci-dessous —</option>
          {apiConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        {cfg.connection ? (
          <>
            <label>Chemin (relatif à la connexion)</label>
            <input value={String(cfg.path ?? "")} placeholder="orders"
                   onChange={(e) => set("path", e.target.value)} />
          </>
        ) : (
          <>
            <label>URL</label>
            <input value={String(cfg.url ?? "")} placeholder="https://…"
                   onChange={(e) => set("url", e.target.value)} />
          </>
        )}
        <label>Méthode</label>
        <select value={String(cfg.method ?? (node.type === "http" ? "POST" : "GET"))}
                onChange={(e) => set("method", e.target.value)}>
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
        </select>
      </>
    );
  }

  if (node.type === "sftp_write") {
    return (
      <>
        <label>Connexion (sftp)</label>
        <select value={String(cfg.connection ?? "")} onChange={(e) => set("connection", e.target.value)}>
          <option value="">— choisir —</option>
          {sftpConns.map((v) => <option key={v.id} value={v.name}>{v.name}</option>)}
        </select>
        <label>Nom de fichier</label>
        <input value={String(cfg.filename ?? "")} onChange={(e) => set("filename", e.target.value)} />
      </>
    );
  }

  return null;
}
