import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type {
  ArtefactInfo, MappingDoc, MappingLink, PivotChecks, PivotObjectResponse,
} from "../lib/types";
import {
  IconCheck, IconCode, IconEdit, IconList, IconPlay,
  IconSave, IconTable, IconUpload, IconWarn,
} from "../lib/icons";

interface Props {
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  onSession?: (sid: string) => void;
  /** Columns of the current working table, offered as sources. */
  sessionColumns?: string[];
  sessionId?: string | null;
}

type Mode = "list" | "connect";

const EMPTY: MappingDoc = { name: "", source_kind: "flat", links: [] };

/** A field's origin is exactly one of these — the whole unification in one type. */
type Origin = "source" | "expr";

function originOf(l: MappingLink): Origin {
  return l.expr && l.expr.trim() ? "expr" : "source";
}

export function MappingPanel({ notify, onSession, sessionColumns = [], sessionId }: Props) {
  const [doc, setDoc] = useState<MappingDoc>(EMPTY);
  const [mode, setMode] = useState<Mode>("list");
  const [busy, setBusy] = useState("");
  const [saved, setSaved] = useState<ArtefactInfo[]>([]);
  const [ediModels, setEdiModels] = useState<ArtefactInfo[]>([]);

  // source material
  const [sourceFields, setSourceFields] = useState<string[]>([]);
  const [ediModelId, setEdiModelId] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);

  // connect mode: the left-hand item waiting for a partner
  const [pending, setPending] = useState<string>("");
  // which link has its detail drawer open
  const [openLink, setOpenLink] = useState<number>(-1);

  const [result, setResult] = useState<PivotObjectResponse | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [m, e] = await Promise.all([
        api.listArtefacts("mapping"), api.listArtefacts("edi_model")]);
      setSaved(m); setEdiModels(e);
    } catch { /* empty state is fine */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  // the working table's columns are always available as sources
  useEffect(() => {
    if (doc.source_kind === "flat" && sourceFields.length === 0 && sessionColumns.length)
      setSourceFields(sessionColumns);
  }, [sessionColumns]);   // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try { await fn(); }
    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setBusy(""); }
  };

  const setLink = (i: number, patch: Partial<MappingLink>) =>
    setDoc((d) => ({ ...d, links: d.links.map((l, j) => (j === i ? { ...l, ...patch } : l)) }));
  const addLink = (l: Partial<MappingLink> = {}) =>
    setDoc((d) => ({ ...d, links: [...d.links, { pivot: "", source: "", scope: "item", ...l }] }));
  const rmLink = (i: number) =>
    setDoc((d) => ({ ...d, links: d.links.filter((_, j) => j !== i) }));

  const linked = new Set(doc.links.map((l) => l.source).filter(Boolean) as string[]);
  const unlinked = sourceFields.filter((f) => !linked.has(f));

  /** Click a source, then click (or type) a pivot field: the pair becomes a link. */
  const pairWith = (pivot: string) => {
    if (!pending) return;
    addLink({ pivot, source: pending, scope: "item" });
    setPending("");
  };

  const yamlOf = (d: MappingDoc): string => {
    const lines = [`name: ${d.name || "mapping"}`, `source_kind: ${d.source_kind}`, "links:"];
    for (const l of d.links) {
      const parts = [`pivot: ${l.pivot}`];
      if (originOf(l) === "expr") parts.push(`expr: ${JSON.stringify(l.expr ?? "")}`);
      else parts.push(`source: ${l.source ?? ""}`);
      parts.push(`scope: ${l.scope}`);
      if (l.default) parts.push(`default: ${JSON.stringify(l.default)}`);
      lines.push(`  - {${parts.join(", ")}}`);
      if (l.rules && Object.keys(l.rules).length)
        lines[lines.length - 1] =
          `  - {${parts.join(", ")}, rules: ${JSON.stringify(l.rules)}}`;
    }
    return lines.join("\n") + "\n";
  };

  return (
    <div className="map">
      {/* ── source ─────────────────────────────────────────────── */}
      <section className="map-block">
        <h3><IconTable size={15} /> The source to bridge</h3>
        <p className="map-hint">
          A mapping states, explicitly and in both directions, what a source field
          corresponds to. It replaces the old bridge-by-coincidence-of-names, and
          because every link is reversible the same artefact drives source→pivot
          and pivot→source.
        </p>
        <div className="map-row">
          {(["flat", "edi"] as const).map((k) => (
            <button key={k} className={`map-tab ${doc.source_kind === k ? "on" : ""}`}
                    onClick={() => { setDoc({ ...doc, source_kind: k }); setSourceFields([]); }}>
              {k === "flat" ? "Table (CSV, session, base)" : "EDI"}
            </button>
          ))}
        </div>

        <div className="map-row">
          {doc.source_kind === "edi" && (
            <select value={ediModelId} onChange={(e) => setEdiModelId(e.target.value)}>
              <option value="">— EDI model —</option>
              {ediModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          )}
          <input type="file" ref={fileRef} hidden
                 onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {doc.source_kind === "flat" && (
            <button className="btn sm" onClick={() => fileRef.current?.click()}>
              <IconUpload size={13} /> {file ? file.name : "Sample file"}
            </button>
          )}
          <button className="btn sm" disabled={!!busy} onClick={() => run("suggest", async () => {
            const s = await api.suggestMapping({
              source_kind: doc.source_kind,
              file: file ?? undefined,
              session_id: doc.source_kind === "flat" && !file ? (sessionId ?? "") : "",
              edi_model_id: doc.source_kind === "edi" ? ediModelId : undefined,
            });
            setDoc({ ...s.mapping, name: doc.name || s.mapping.name });
            setSourceFields(s.mapping.links.map((l) => l.source ?? "").filter(Boolean));
            notify("Starting mapping proposed — every field linked to itself. Edit from here.", "ok");
          })}>
            <IconPlay size={13} /> Suggest a starting point
          </button>
        </div>
      </section>

      {/* ── the two modes ──────────────────────────────────────── */}
      <section className="map-block">
        <div className="map-modes">
          <button className={`map-tab ${mode === "list" ? "on" : ""}`} onClick={() => setMode("list")}>
            <IconList size={14} /> List
          </button>
          <button className={`map-tab ${mode === "connect" ? "on" : ""}`} onClick={() => setMode("connect")}>
            <IconEdit size={14} /> Connect by hand
          </button>
          <span className="map-count">{doc.links.length} link(s)</span>
        </div>

        {mode === "connect" ? (
          <div className="map-connect">
            <div className="map-col">
              <h4>Source fields</h4>
              {sourceFields.length === 0 && <p className="map-hint">Load a source above.</p>}
              {sourceFields.map((f) => (
                <button key={f}
                        className={`map-node ${pending === f ? "pending" : ""} ${linked.has(f) ? "done" : ""}`}
                        onClick={() => setPending(pending === f ? "" : f)}>
                  {f}{linked.has(f) && <span className="map-tick">✓</span>}
                </button>
              ))}
              {unlinked.length > 0 && (
                <p className="map-hint">{unlinked.length} field(s) not linked yet.</p>
              )}
            </div>

            <div className="map-middle">
              {pending
                ? <><span className="map-arrow">→</span><p className="map-hint">Now click a pivot field, or create one.</p></>
                : <p className="map-hint">Click a source field to start a link.</p>}
              {pending && (
                <button className="btn sm" onClick={() => { pairWith(pending); }}>
                  Create pivot field “{pending}”
                </button>
              )}
            </div>

            <div className="map-col">
              <h4>Pivot fields</h4>
              {doc.links.map((l, i) => (
                <button key={i} className={`map-node ${pending ? "target" : ""}`}
                        onClick={() => pending && pairWith(l.pivot)}>
                  {l.pivot || <em>(unnamed)</em>}
                  <span className="map-src">
                    {originOf(l) === "expr" ? "ƒ(x)" : l.source}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="map-list">
            {doc.links.length === 0 && (
              <p className="map-hint">No link yet — suggest a starting point above, or add one.</p>
            )}
            {doc.links.map((l, i) => (
              <div key={i} className={`map-link ${openLink === i ? "open" : ""}`}>
                <div className="map-link-row">
                  <input className="map-pivot" value={l.pivot} placeholder="pivot field"
                         onChange={(e) => setLink(i, { pivot: e.target.value })} />
                  <span className="map-eq">=</span>

                  {originOf(l) === "source" ? (
                    sourceFields.length ? (
                      <select value={l.source ?? ""}
                              onChange={(e) => setLink(i, { source: e.target.value, expr: null })}>
                        <option value="">— source —</option>
                        {sourceFields.map((f) => <option key={f} value={f}>{f}</option>)}
                      </select>
                    ) : (
                      <input value={l.source ?? ""} placeholder="source field"
                             onChange={(e) => setLink(i, { source: e.target.value, expr: null })} />
                    )
                  ) : (
                    <input className="mono" value={l.expr ?? ""}
                           placeholder="CONCAT([A], '-', [B])"
                           onChange={(e) => setLink(i, { expr: e.target.value, source: "" })} />
                  )}

                  <button className="map-flip" title="Read a field, or compute the value"
                          onClick={() => setLink(i, originOf(l) === "source"
                            ? { expr: "", source: "" } : { expr: null, source: "" })}>
                    {originOf(l) === "source" ? <IconCode size={13} /> : <IconTable size={13} />}
                  </button>

                  <select value={l.scope} onChange={(e) => setLink(i, { scope: e.target.value as "head" | "item" })}>
                    <option value="head">head</option>
                    <option value="item">item</option>
                  </select>

                  <button className="map-flip" title="Constraints"
                          onClick={() => setOpenLink(openLink === i ? -1 : i)}>
                    <IconCheck size={13} />
                  </button>
                  <button className="map-flip danger" onClick={() => rmLink(i)}>×</button>
                </div>

                {openLink === i && (
                  <div className="map-rules">
                    <p className="map-hint">
                      The same constraints a cleaning config declares, checked by the
                      same engine — a regex means the same thing here as there.
                    </p>
                    <div className="map-row">
                      <select value={(l.rules?.type as string) ?? ""}
                              onChange={(e) => setLink(i, { rules: { ...(l.rules ?? {}), type: e.target.value || undefined } })}>
                        <option value="">type: any</option>
                        {["string", "integer", "float", "date", "boolean"].map((t) =>
                          <option key={t} value={t}>type: {t}</option>)}
                      </select>
                      <input className="mono" placeholder="regex, e.g. ^\\d{13}$"
                             value={(l.rules?.regex as string) ?? ""}
                             onChange={(e) => setLink(i, { rules: { ...(l.rules ?? {}), regex: e.target.value || undefined } })} />
                      <label className="map-check">
                        <input type="checkbox" checked={l.rules?.nullable === false}
                               onChange={(e) => setLink(i, { rules: { ...(l.rules ?? {}), nullable: e.target.checked ? false : undefined } })} />
                        required
                      </label>
                      <input placeholder="default if empty" value={l.default ?? ""}
                             onChange={(e) => setLink(i, { default: e.target.value || null })} />
                    </div>
                  </div>
                )}
              </div>
            ))}
            <button className="btn sm" onClick={() => addLink()}>+ link</button>
          </div>
        )}
      </section>

      {/* ── test & save ────────────────────────────────────────── */}
      <section className="map-block">
        <div className="map-row">
          <input value={doc.name} placeholder="mapping name"
                 onChange={(e) => setDoc({ ...doc, name: e.target.value })} />
          <button className="btn" disabled={!!busy || !doc.links.length}
                  onClick={() => run("test", async () => {
                    const payload: Record<string, string | File | undefined> = {
                      mapping_yaml: yamlOf(doc), target: "preview",
                    };
                    if (doc.source_kind === "flat") {
                      if (file) payload.file = file; else payload.session_id = sessionId ?? "";
                    } else {
                      payload.file = file ?? undefined;
                      payload.edi_model_id = ediModelId;
                    }
                    const r = await api.toPivotObject(doc.source_kind, payload);
                    setResult(r);
                    notify(r.checks.ok
                      ? `${r.documents} document(s) — all constraints satisfied.`
                      : `${r.checks.problems.length} constraint problem(s).`,
                      r.checks.ok ? "ok" : "err");
                  })}>
            <IconPlay size={15} /> Test on the source
          </button>
          <button className="btn" disabled={!!busy || !doc.name.trim() || !doc.links.length}
                  onClick={() => run("save", async () => {
                    const existing = saved.find((m) => m.name === doc.name.trim());
                    if (existing) await api.addArtefactVersion("mapping", existing.id, { yaml: yamlOf(doc) });
                    else await api.createArtefact("mapping", { name: doc.name.trim(), yaml: yamlOf(doc) });
                    await refresh();
                    notify(`Mapping “${doc.name}” saved.`, "ok");
                  })}>
            <IconSave size={15} /> Save to library
          </button>
          {result?.session_id && onSession && (
            <button className="btn sm" onClick={() => onSession(result.session_id!)}>
              Open in Data
            </button>
          )}
        </div>

        {result && <ChecksView checks={result.checks} />}
        {result && (
          <div className="map-grid-wrap">
            <div className="map-caption">
              {result.documents} document(s) · {result.preview.total_rows} row(s)
            </div>
            <div className="map-scroll">
              <table className="map-grid">
                <thead><tr>{result.preview.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {result.preview.data.map((row, i) => (
                    <tr key={i}>{row.map((v, j) => <td key={j}>{v}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* ── library ────────────────────────────────────────────── */}
      <section className="map-block">
        <h4><IconSave size={14} /> Saved mappings <span className="map-count">{saved.length}</span></h4>
        {saved.length === 0 && <p className="map-hint">None yet.</p>}
        {saved.map((m) => (
          <div key={m.id} className="map-saved">
            <strong>{m.name}</strong>
            <span className="map-hint">v{m.latest_version_no}</span>
          </div>
        ))}
      </section>
    </div>
  );
}

function ChecksView({ checks }: { checks: PivotChecks }) {
  if (checks.checked === 0)
    return <p className="map-hint">No constraint declared — nothing to check.</p>;
  if (checks.ok)
    return (
      <div className="map-ok">
        <IconCheck size={14} /> {checks.checked} field(s) constrained, all satisfied.
      </div>
    );
  return (
    <div className="map-ko">
      <h4><IconWarn size={14} /> {checks.problems.length} problem(s)</h4>
      <ul>
        {checks.problems.slice(0, 40).map((p, i) => (
          <li key={i}><code>{p.field}</code> {p.message}</li>
        ))}
      </ul>
    </div>
  );
}
