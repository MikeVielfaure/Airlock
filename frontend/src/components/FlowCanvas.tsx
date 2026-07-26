import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
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
  dataset_write: { name: "", mode: "replace", key_fields: [] },
  file: { format: "csv", filename: "sortie.csv" },
  response: {},
};

const ROLE_CLASS: Record<string, string> = {
  source: "src", transform: "tr", sink: "sink",
};

export function FlowCanvas({ notify }: Props) {
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

  const surface = useRef<HTMLDivElement>(null);

  const roleOf = (type: string) =>
    bricks.find((b) => b.type === type)?.role ?? "transform";

  useEffect(() => {
    api.flowBricks().then((r) => setBricks(r.bricks)).catch(() => { /* palette optional */ });
    api.listArtefacts("graph").then(setSaved).catch(() => { /* empty is fine */ });
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
      notify(`Flow ran — output “${r.output}”, ${r.preview.total_rows} row(s).`, "ok");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // The runner names the failing node; highlight it on the canvas.
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
        ? `Valid — order: ${r.order.join(" → ")}, output “${r.output}”.`
        : `Unknown brick type(s): ${r.unknown_types.join(", ")}`, r.ok ? "ok" : "err");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setBusy(""); }
  };

  const sel = nodes.find((n) => n.id === selected) ?? null;
  const stepOf = (id: string) => trace.find((t) => t.node === id);

  return (
    <div className="fc">
      {/* palette */}
      <aside className="fc-palette">
        <h4>Bricks</h4>
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
          <input value={name} placeholder="flow name" onChange={(e) => setName(e.target.value)} />
          <button className="btn sm" disabled={!!busy} onClick={check}>
            <IconCheck size={13} /> Check
          </button>
          <button className="btn" disabled={!!busy || !nodes.length} onClick={run}>
            <IconPlay size={14} /> {busy === "run" ? "Running…" : "Run"}
          </button>
          <button className="btn sm" disabled={!name.trim() || !nodes.length}
                  onClick={async () => {
                    try {
                      const ex = saved.find((g) => g.name === name.trim());
                      if (ex) await api.addArtefactVersion("graph", ex.id, { yaml: toYaml() });
                      else await api.createArtefact("graph", { name: name.trim(), yaml: toYaml() });
                      setSaved(await api.listArtefacts("graph"));
                      notify(`Flow “${name}” saved.`, "ok");
                    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>
            <IconSave size={13} /> Save
          </button>
          <select value="" onChange={async (e) => {
            if (!e.target.value) return;
            try {
              const g = await api.loadGraph(e.target.value);
              loadGraph(g as never);
              notify("Flow loaded.", "ok");
            } catch (err) { notify(err instanceof Error ? err.message : String(err), "err"); }
          }}>
            <option value="">— open a flow —</option>
            {saved.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
          <button className="btn sm" onClick={() => setShowYaml(!showYaml)}>
            <IconCode size={13} /> {showYaml ? "Canvas" : "YAML"}
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
                    <div className="fc-metrics">{step.rows} rows · {step.ms}ms</div>
                  )}
                  <button className="fc-port" title="Connect to another brick"
                          onClick={(e) => { e.stopPropagation(); setLinking(n.id); }}>›</button>
                  <button className="fc-kill" onClick={(e) => { e.stopPropagation(); removeNode(n.id); }}>×</button>
                </div>
              );
            })}

            {nodes.length === 0 && (
              <p className="fc-empty">Pick a brick on the left to start a flow.</p>
            )}
            {linking && (
              <div className="fc-linking">Click a brick to connect <strong>{linking}</strong> to it.</div>
            )}
          </div>
        )}

        {trace.length > 0 && (
          <div className="fc-trace">
            <h4><IconLayers size={13} /> Run</h4>
            <div className="fc-steps">
              {trace.map((t, i) => (
                <span key={i} className="fc-step">
                  <strong>{t.node}</strong> {t.rows} rows · {t.ms}ms
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
            <label>Label</label>
            <input value={sel.label ?? ""} placeholder="what this brick does"
                   onChange={(e) => setNodes((ns) =>
                     ns.map((n) => (n.id === sel.id ? { ...n, label: e.target.value } : n)))} />
            <label>Config</label>
            <textarea className="mono" rows={14}
                      defaultValue={JSON.stringify(sel.config, null, 2)}
                      key={sel.id}
                      onChange={(e) => patchConfig(sel.id, e.target.value)} />
            <p className="fc-hint">
              Edited live. An invalid JSON draft is ignored until it parses again,
              so typing never destroys the config.
            </p>
            {failed === sel.id && (
              <p className="fc-fail"><IconWarn size={12} /> This brick failed on the last run.</p>
            )}
          </>
        ) : (
          <>
            <h4>Parameters</h4>
            <p className="fc-hint">
              Named inputs make the flow reusable — and they are the request body
              when it is called as an API.
            </p>
            {params.map((p, i) => (
              <div key={i} className="fc-param">
                <input value={p.name} placeholder="name"
                       onChange={(e) => setParams((ps) =>
                         ps.map((q, j) => (j === i ? { ...q, name: e.target.value } : q)))} />
                <input value={p.default} placeholder="default"
                       onChange={(e) => setParams((ps) =>
                         ps.map((q, j) => (j === i ? { ...q, default: e.target.value } : q)))} />
                <button className="fc-kill" onClick={() =>
                  setParams((ps) => ps.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button className="btn sm" onClick={() => setParams((ps) => [...ps, { name: "", default: "" }])}>
              + parameter
            </button>
            <button className="btn sm" onClick={() => { setNodes([]); setEdges([]); setTrace([]); }}>
              <IconReset size={13} /> Clear canvas
            </button>
          </>
        )}
      </aside>
    </div>
  );
}
