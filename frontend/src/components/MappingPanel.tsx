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
        <h3><IconTable size={15} /> La source à relier</h3>
        <p className="map-hint">
          Un mapping déclare, explicitement et dans les deux sens, à quoi correspond
          un champ source. Il remplace l'ancienne mise en correspondance par
          coïncidence de noms — chaque lien étant réversible, le même artefact
          pilote source→pivot et pivot→source.
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
              <IconUpload size={13} /> {file ? file.name : "Fichier d'exemple"}
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
            notify("Mapping de départ proposé — chaque champ relié à lui-même. Modifiez-le ici.", "ok");
          })}>
            <IconPlay size={13} /> Suggérer un point de départ
          </button>
        </div>
      </section>

      {/* ── the two modes ──────────────────────────────────────── */}
      <section className="map-block">
        <div className="map-modes">
          <button className={`map-tab ${mode === "list" ? "on" : ""}`} onClick={() => setMode("list")}>
            <IconList size={14} /> Liste
          </button>
          <button className={`map-tab ${mode === "connect" ? "on" : ""}`} onClick={() => setMode("connect")}>
            <IconEdit size={14} /> Relier à la main
          </button>
          <span className="map-count">{doc.links.length} lien(s)</span>
        </div>

        {mode === "connect" ? (
          <div className="map-connect">
            <div className="map-col">
              <h4>Champs source</h4>
              {sourceFields.length === 0 && <p className="map-hint">Chargez une source ci-dessus.</p>}
              {sourceFields.map((f) => (
                <button key={f}
                        className={`map-node ${pending === f ? "pending" : ""} ${linked.has(f) ? "done" : ""}`}
                        onClick={() => setPending(pending === f ? "" : f)}>
                  {f}{linked.has(f) && <span className="map-tick">✓</span>}
                </button>
              ))}
              {unlinked.length > 0 && (
                <p className="map-hint">{unlinked.length} champ(s) pas encore relié(s).</p>
              )}
            </div>

            <div className="map-middle">
              {pending
                ? <><span className="map-arrow">→</span><p className="map-hint">Cliquez maintenant un champ pivot, ou créez-en un.</p></>
                : <p className="map-hint">Cliquez un champ source pour démarrer un lien.</p>}
              {pending && (
                <button className="btn sm" onClick={() => { pairWith(pending); }}>
                  Créer le champ pivot « {pending} »
                </button>
              )}
            </div>

            <div className="map-col">
              <h4>Champs pivot</h4>
              {doc.links.map((l, i) => (
                <button key={i} className={`map-node ${pending ? "target" : ""}`}
                        onClick={() => pending && pairWith(l.pivot)}>
                  {l.pivot || <em>(sans nom)</em>}
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
              <p className="map-hint">Aucun lien pour l'instant — suggérez un point de départ ci-dessus, ou ajoutez-en un.</p>
            )}
            {doc.links.map((l, i) => (
              <div key={i} className={`map-link ${openLink === i ? "open" : ""}`}>
                <div className="map-link-row">
                  <input className="map-pivot" value={l.pivot} placeholder="champ pivot"
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
                      <input value={l.source ?? ""} placeholder="champ source"
                             onChange={(e) => setLink(i, { source: e.target.value, expr: null })} />
                    )
                  ) : (
                    <input className="mono" value={l.expr ?? ""}
                           placeholder="CONCAT([A], '-', [B])"
                           onChange={(e) => setLink(i, { expr: e.target.value, source: "" })} />
                  )}

                  <button className="map-flip" title="Lire un champ, ou calculer la valeur"
                          onClick={() => setLink(i, originOf(l) === "source"
                            ? { expr: "", source: "" } : { expr: null, source: "" })}>
                    {originOf(l) === "source" ? <IconCode size={13} /> : <IconTable size={13} />}
                  </button>

                  <select value={l.scope} onChange={(e) => setLink(i, { scope: e.target.value as "head" | "item" })}>
                    <option value="head">head</option>
                    <option value="item">item</option>
                  </select>

                  <button className="map-flip" title="Contraintes"
                          onClick={() => setOpenLink(openLink === i ? -1 : i)}>
                    <IconCheck size={13} />
                  </button>
                  <button className="map-flip danger" onClick={() => rmLink(i)}>×</button>
                </div>

                {openLink === i && (
                  <div className="map-rules">
                    <p className="map-hint">
                      Les mêmes contraintes qu'une config de nettoyage déclare, vérifiées
                      par le même moteur — une regex veut dire la même chose ici que là-bas.
                    </p>
                    <div className="map-row">
                      <select value={(l.rules?.type as string) ?? ""}
                              onChange={(e) => setLink(i, { rules: { ...(l.rules ?? {}), type: e.target.value || undefined } })}>
                        <option value="">type : any</option>
                        {["string", "integer", "float", "date", "boolean"].map((t) =>
                          <option key={t} value={t}>type : {t}</option>)}
                      </select>
                      <input className="mono" placeholder="regex, ex. ^\\d{13}$"
                             value={(l.rules?.regex as string) ?? ""}
                             onChange={(e) => setLink(i, { rules: { ...(l.rules ?? {}), regex: e.target.value || undefined } })} />
                      <label className="map-check">
                        <input type="checkbox" checked={l.rules?.nullable === false}
                               onChange={(e) => setLink(i, { rules: { ...(l.rules ?? {}), nullable: e.target.checked ? false : undefined } })} />
                        obligatoire
                      </label>
                      <input placeholder="valeur par défaut si vide" value={l.default ?? ""}
                             onChange={(e) => setLink(i, { default: e.target.value || null })} />
                    </div>
                  </div>
                )}
              </div>
            ))}
            <button className="btn sm" onClick={() => addLink()}>+ lien</button>
          </div>
        )}
      </section>

      {/* ── test & save ────────────────────────────────────────── */}
      <section className="map-block">
        <div className="map-row">
          <input value={doc.name} placeholder="nom du mapping"
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
                      ? `${r.documents} document(s) — toutes les contraintes sont respectées.`
                      : `${r.checks.problems.length} problème(s) de contrainte.`,
                      r.checks.ok ? "ok" : "err");
                  })}>
            <IconPlay size={15} /> Tester sur la source
          </button>
          <button className="btn" disabled={!!busy || !doc.name.trim() || !doc.links.length}
                  onClick={() => run("save", async () => {
                    const existing = saved.find((m) => m.name === doc.name.trim());
                    if (existing) await api.addArtefactVersion("mapping", existing.id, { yaml: yamlOf(doc) });
                    else await api.createArtefact("mapping", { name: doc.name.trim(), yaml: yamlOf(doc) });
                    await refresh();
                    notify(`Mapping « ${doc.name} » enregistré.`, "ok");
                  })}>
            <IconSave size={15} /> Enregistrer dans la bibliothèque
          </button>
          {result?.session_id && onSession && (
            <button className="btn sm" onClick={() => onSession(result.session_id!)}>
              Ouvrir dans Données
            </button>
          )}
        </div>

        {result && <ChecksView checks={result.checks} />}
        {result && (
          <div className="map-grid-wrap">
            <div className="map-caption">
              {result.documents} document(s) · {result.preview.total_rows} ligne(s)
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
        <h4><IconSave size={14} /> Mappings enregistrés <span className="map-count">{saved.length}</span></h4>
        {saved.length === 0 && <p className="map-hint">Aucun pour l'instant.</p>}
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
    return <p className="map-hint">Aucune contrainte déclarée — rien à vérifier.</p>;
  if (checks.ok)
    return (
      <div className="map-ok">
        <IconCheck size={14} /> {checks.checked} champ(s) contraint(s), tous respectés.
      </div>
    );
  return (
    <div className="map-ko">
      <h4><IconWarn size={14} /> {checks.problems.length} problème(s)</h4>
      <ul>
        {checks.problems.slice(0, 40).map((p, i) => (
          <li key={i}><code>{p.field}</code> {p.message}</li>
        ))}
      </ul>
    </div>
  );
}
