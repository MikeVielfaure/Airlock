import { useCallback, useEffect, useRef, useState } from "react";
import { api, downloadBase64, type EdiModelRef } from "../lib/api";
import type {
  ArtefactInfo, EdiConvertResponse, EdiDownload, EdiGenerateResponse,
  EdiInspectResponse, EdiKb, EdiPivotPreview, EdiValidateResponse,
  FileResponse, TablePreview,
} from "../lib/types";
import {
  IconCheck, IconCode, IconDownload, IconLayers, IconList, IconPlay,
  IconSave, IconTable, IconUpload, IconWarn,
} from "../lib/icons";

type Sub = "inspect" | "transform" | "generate" | "convert" | "models" | "doc";

interface Props {
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  /** Hand a pivoted table over to the main Data view, as if it had been uploaded. */
  onSession?: (res: FileResponse) => void;
}

/** A model is either picked from the library or typed inline in the editor. */
function useModelRef(models: ArtefactInfo[]) {
  const [id, setId] = useState("");
  const [pinned, setPinned] = useState(false);
  const [yaml, setYaml] = useState("");
  const [inline, setInline] = useState(false);
  const ref = (): EdiModelRef | null => {
    if (inline) return yaml.trim() ? { yaml } : null;
    if (!id) return null;
    const m = models.find((a) => a.id === id);
    return { id, version: pinned && m ? m.latest_version_no : null };
  };
  return { id, setId, pinned, setPinned, yaml, setYaml, inline, setInline, ref };
}

function ModelPicker({ label, models, ctl }: {
  label: string; models: ArtefactInfo[]; ctl: ReturnType<typeof useModelRef>;
}) {
  return (
    <div className="edi-field">
      <label>{label}</label>
      <div className="edi-row">
        <select value={ctl.inline ? "__inline" : ctl.id}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__inline") { ctl.setInline(true); return; }
                  ctl.setInline(false); ctl.setId(v);
                }}>
          <option value="">— pick a model —</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.name} (v{m.latest_version_no})</option>
          ))}
          <option value="__inline">✎ paste YAML inline…</option>
        </select>
        {!ctl.inline && ctl.id && (
          <label className="edi-check" title="Pin the version instead of tracking the latest">
            <input type="checkbox" checked={ctl.pinned}
                   onChange={(e) => ctl.setPinned(e.target.checked)} /> pin
          </label>
        )}
      </div>
      {ctl.inline && (
        <textarea className="mono" rows={8} value={ctl.yaml} spellCheck={false}
                  placeholder="name: …&#10;message_type: ORDERS&#10;header: …"
                  onChange={(e) => ctl.setYaml(e.target.value)} />
      )}
    </div>
  );
}

function ErrorList({ title, errors }: { title: string; errors: { code: string; message: string; tag: string; message_no: number; segment_pos: number }[] }) {
  if (!errors.length) return null;
  return (
    <div className="edi-errors">
      <h4><IconWarn size={14} /> {title} <span className="count err">{errors.length}</span></h4>
      <ul>
        {errors.slice(0, 200).map((e, i) => (
          <li key={i}>
            <code className="edi-code">{e.code}</code>
            <span className="edi-loc">
              {e.message_no ? `msg ${e.message_no}` : "interchange"}
              {e.tag ? ` › ${e.tag}` : ""}
              {e.segment_pos ? ` › seg ${e.segment_pos}` : ""}
            </span>
            {e.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

function MiniTable({ p, caption }: { p: TablePreview; caption: string }) {
  return (
    <div className="edi-table-wrap">
      <div className="edi-caption">
        {caption} — {p.total_rows} row{p.total_rows === 1 ? "" : "s"}, {p.columns.length} columns
        {p.shown_rows < p.total_rows && ` (showing ${p.shown_rows})`}
      </div>
      <div className="edi-scroll">
        <table className="edi-grid">
          <thead><tr>{p.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {p.data.map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j}>{v || <span className="edi-empty">·</span>}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EdiPanel({ notify, onSession }: Props) {
  const [sub, setSub] = useState<Sub>("inspect");
  const [models, setModels] = useState<ArtefactInfo[]>([]);
  const [busy, setBusy] = useState("");

  const refreshModels = useCallback(async () => {
    try { setModels(await api.listArtefacts("edi_model")); }
    catch (e) { notify(e instanceof Error ? e.message : "Could not load EDI models.", "err"); }
  }, [notify]);
  useEffect(() => { refreshModels(); }, [refreshModels]);

  const fail = (e: unknown) => notify(e instanceof Error ? e.message : String(e), "err");
  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try { await fn(); } catch (e) { fail(e); } finally { setBusy(""); }
  };

  // ── inspect ────────────────────────────────────────────────────────
  const [inspected, setInspected] = useState<EdiInspectResponse | null>(null);
  const inspectRef = useRef<HTMLInputElement>(null);

  // ── transform (validate / pivot) ───────────────────────────────────
  const ediFileRef = useRef<HTMLInputElement>(null);
  const [ediFile, setEdiFile] = useState<File | null>(null);
  const tModel = useModelRef(models);
  const [mode, setMode] = useState<"flat" | "linked">("flat");
  const [report, setReport] = useState<EdiValidateResponse | null>(null);
  const [pivotOut, setPivotOut] = useState<EdiPivotPreview | null>(null);

  // ── generate ───────────────────────────────────────────────────────
  const tabFileRef = useRef<HTMLInputElement>(null);
  const [tabFile, setTabFile] = useState<File | null>(null);
  const gModel = useModelRef(models);
  const [gSender, setGSender] = useState("");
  const [gRecipient, setGRecipient] = useState("");
  const [gRef, setGRef] = useState("");
  const [gGroup, setGGroup] = useState("");
  const [generated, setGenerated] = useState<EdiGenerateResponse | null>(null);

  // ── convert ────────────────────────────────────────────────────────
  const convFileRef = useRef<HTMLInputElement>(null);
  const [convFile, setConvFile] = useState<File | null>(null);
  const srcModel = useModelRef(models);
  const dstModel = useModelRef(models);
  const [mapping, setMapping] = useState("");
  const [converted, setConverted] = useState<EdiConvertResponse | null>(null);

  // ── models tab ─────────────────────────────────────────────────────
  const [editorName, setEditorName] = useState("");
  const [editorYaml, setEditorYaml] = useState("");
  const [notes, setNotes] = useState<string[]>([]);
  const inferRef = useRef<HTMLInputElement>(null);
  const yamlUpRef = useRef<HTMLInputElement>(null);

  // ── doc ────────────────────────────────────────────────────────────
  const [kb, setKb] = useState<EdiKb | null>(null);
  useEffect(() => {
    if (sub === "doc" && !kb) api.ediKb().then(setKb).catch(fail);
  }, [sub, kb]);   // eslint-disable-line react-hooks/exhaustive-deps

  const needModel = (r: EdiModelRef | null): r is EdiModelRef => {
    if (!r) { notify("Pick a model first (library or inline YAML).", "err"); return false; }
    return true;
  };
  const needFile = (f: File | null): f is File => {
    if (!f) { notify("Pick a file first.", "err"); return false; }
    return true;
  };

  return (
    <div className="edi">
      <nav className="edi-subtabs">
        {([["inspect", "Inspect", <IconList size={14} />],
           ["transform", "Transform", <IconTable size={14} />],
           ["generate", "Generate", <IconCode size={14} />],
           ["convert", "Convert", <IconLayers size={14} />],
           ["models", "Models", <IconSave size={14} />],
           ["doc", "Doc", <IconCheck size={14} />]] as [Sub, string, JSX.Element][])
          .map(([k, label, icon]) => (
            <button key={k} className={`tab ${sub === k ? "active" : ""}`} onClick={() => setSub(k)}>
              {icon} {label}
            </button>
          ))}
      </nav>

      {/* ═══ INSPECT ═══ */}
      {sub === "inspect" && (
        <section className="edi-pane">
          <p className="edi-hint">
            EDI files rarely carry an extension — the format is detected from the content
            (UNA/UNB for EDIFACT). Envelope counters and references are checked here without
            any model: this is the free structural check.
          </p>
          <input type="file" ref={inspectRef} hidden onChange={(e) => {
            const f = e.target.files?.[0]; if (!f) return;
            run("inspect", async () => { setInspected(await api.ediInspect(f)); });
            e.target.value = "";
          }} />
          <button className="btn" onClick={() => inspectRef.current?.click()} disabled={!!busy}>
            <IconUpload size={15} /> {busy === "inspect" ? "Reading…" : "Open an EDI file"}
          </button>

          {inspected && (
            <>
              <div className="edi-summary">
                <span className="pill">{inspected.format}</span>
                <span className="pill">{inspected.had_una ? "UNA present" : "default separators"}</span>
                <span className="pill">{inspected.interchanges.length} interchange(s)</span>
                <span className="pill">{inspected.total_segments} segments</span>
                <span className={`pill ${inspected.syntax_errors.length ? "err" : "ok"}`}>
                  {inspected.syntax_errors.length
                    ? `${inspected.syntax_errors.length} syntax error(s)`
                    : "envelope consistent"}
                </span>
                {inspected.truncated && (
                  <span className="pill warn" title="Only the first segments are decoded — the checks above still cover the whole file">
                    tree truncated
                  </span>
                )}
              </div>
              <ErrorList title="Syntax" errors={inspected.syntax_errors} />
              {inspected.interchanges.map((it, i) => (
                <div key={i} className="edi-inter">
                  <h4>
                    Interchange {it.ref || "(no ref)"}{" "}
                    <span className="edi-loc">{it.sender} → {it.recipient}</span>
                    {it.implicit && <span className="pill warn">no UNB</span>}
                  </h4>
                  {it.messages.map((m, j) => (
                    <details key={j} open={j === 0}>
                      <summary>
                        <strong>{m.type || "?"}</strong> {m.ref}
                        {m.directory && <span className="edi-loc"> · {m.directory}</span>}
                        <span className="count">{m.segment_count} seg</span>
                      </summary>
                      <div className="edi-tree">
                        {m.segments.map((s) => (
                          <div key={s.pos} className="edi-seg">
                            <div className="edi-seg-head">
                              <code className="edi-tag">{s.tag}</code>
                              <span className="edi-seg-label">{s.label}</span>
                              {s.qualifier_label && (
                                <em className="edi-decoded">{s.qualifier_label}</em>
                              )}
                              <code className="edi-raw">{s.raw}</code>
                            </div>
                            {s.elements.length > 0 && (
                              <ul className="edi-elems">
                                {s.elements.map((el, k) => (
                                  <li key={k}>
                                    <code className="edi-path">{el.path}</code>
                                    <span className="edi-el-label">{el.label}</span>
                                    <span className="edi-val">{el.value}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  ))}
                </div>
              ))}
            </>
          )}
        </section>
      )}

      {/* ═══ TRANSFORM ═══ */}
      {sub === "transform" && (
        <section className="edi-pane">
          <p className="edi-hint">
            Check a file against a model, then flatten it. <strong>Flat</strong> repeats the head
            on every item line — one table, ready for the cleaning pipeline.{" "}
            <strong>Linked</strong> keeps two tables joined on <code>message_no</code>.
          </p>
          <div className="edi-form">
            <div className="edi-field">
              <label>EDI file</label>
              <input type="file" ref={ediFileRef} onChange={(e) => {
                setEdiFile(e.target.files?.[0] ?? null); setReport(null); setPivotOut(null);
              }} />
            </div>
            <ModelPicker label="Model" models={models} ctl={tModel} />
            <div className="edi-field">
              <label>Pivot mode</label>
              <div className="edi-row">
                <label className="edi-check">
                  <input type="radio" checked={mode === "flat"} onChange={() => setMode("flat")} /> flat
                </label>
                <label className="edi-check">
                  <input type="radio" checked={mode === "linked"} onChange={() => setMode("linked")} /> linked
                </label>
              </div>
            </div>
          </div>

          <div className="edi-actions">
            <button className="btn" disabled={!!busy} onClick={() => {
              const m = tModel.ref();
              if (!needFile(ediFile) || !needModel(m)) return;
              run("validate", async () => {
                const r = await api.ediValidate(ediFile, m);
                setReport(r);
                notify(r.ok ? `Valid — ${r.stats.messages} message(s), ${r.stats.items} item(s).`
                            : `${r.stats.errors} error(s) found.`, r.ok ? "ok" : "err");
              });
            }}><IconCheck size={15} /> Validate</button>

            <button className="btn" disabled={!!busy} onClick={() => {
              const m = tModel.ref();
              if (!needFile(ediFile) || !needModel(m)) return;
              run("pivot", async () => {
                setPivotOut(await api.ediPivot(ediFile, m, mode, "preview") as EdiPivotPreview);
              });
            }}><IconPlay size={15} /> Pivot</button>

            <button className="btn sm" disabled={!!busy} onClick={() => {
              const m = tModel.ref();
              if (!needFile(ediFile) || !needModel(m)) return;
              run("csv", async () => {
                const r = await api.ediPivot(ediFile, m, mode, "csv") as { files: EdiDownload[] };
                r.files.forEach(downloadBase64);
              });
            }}><IconDownload size={14} /> CSV</button>

            <button className="btn sm" disabled={!!busy} onClick={() => {
              const m = tModel.ref();
              if (!needFile(ediFile) || !needModel(m)) return;
              run("xlsx", async () => {
                const r = await api.ediPivot(ediFile, m, mode, "xlsx") as { files: EdiDownload[] };
                r.files.forEach(downloadBase64);
              });
            }}><IconDownload size={14} /> Excel</button>

            {onSession && (
              <button className="btn sm" disabled={!!busy} onClick={() => {
                const m = tModel.ref();
                if (!needFile(ediFile) || !needModel(m)) return;
                run("session", async () => {
                  const r = await api.ediPivot(ediFile, m, "flat", "session") as FileResponse;
                  onSession(r);
                  notify("Pivoted table opened in the Data view.", "ok");
                });
              }}><IconTable size={14} /> Open in Data</button>
            )}
          </div>

          {report && (
            <>
              <div className="edi-summary">
                <span className={`pill ${report.ok ? "ok" : "err"}`}>{report.ok ? "valid" : "errors"}</span>
                <span className="pill">{report.model_name}</span>
                <span className="pill">{report.stats.messages} message(s)</span>
                <span className="pill">{report.stats.items} item(s)</span>
              </div>
              <ErrorList title="Syntax" errors={report.syntax_errors} />
              <ErrorList title="Model" errors={report.model_errors} />
            </>
          )}

          {pivotOut?.flat && <MiniTable p={pivotOut.flat} caption="Flat" />}
          {pivotOut?.heads && <MiniTable p={pivotOut.heads} caption="Heads" />}
          {pivotOut?.items && <MiniTable p={pivotOut.items} caption="Items" />}
        </section>
      )}

      {/* ═══ GENERATE ═══ */}
      {sub === "generate" && (
        <section className="edi-pane">
          <p className="edi-hint">
            The reverse trip: a flat CSV/XLSX whose headers match the model's field names becomes
            EDIFACT. Rows are grouped into messages, UNT/UNZ counters are computed, and reserved
            characters in the data are escaped.
          </p>
          <div className="edi-form">
            <div className="edi-field">
              <label>Flat file (CSV or XLSX)</label>
              <input type="file" ref={tabFileRef} accept=".csv,.xlsx,.xls,.txt"
                     onChange={(e) => { setTabFile(e.target.files?.[0] ?? null); setGenerated(null); }} />
            </div>
            <ModelPicker label="Model" models={models} ctl={gModel} />
            <div className="edi-field">
              <label>Group rows into messages by</label>
              <input value={gGroup} onChange={(e) => setGGroup(e.target.value)}
                     placeholder="column name — default: message_no, else one message" />
            </div>
            <div className="edi-field">
              <label>Interchange</label>
              <div className="edi-row">
                <input value={gSender} onChange={(e) => setGSender(e.target.value)} placeholder="sender (GLN:14)" />
                <input value={gRecipient} onChange={(e) => setGRecipient(e.target.value)} placeholder="recipient" />
                <input value={gRef} onChange={(e) => setGRef(e.target.value)} placeholder="reference" />
              </div>
            </div>
          </div>
          <div className="edi-actions">
            <button className="btn" disabled={!!busy} onClick={() => {
              const m = gModel.ref();
              if (!needFile(tabFile) || !needModel(m)) return;
              run("generate", async () => {
                const r = await api.ediGenerate(tabFile, m, {
                  group_by: gGroup, sender: gSender, recipient: gRecipient, interchange_ref: gRef });
                setGenerated(r);
                notify(`${r.messages} message(s), ${r.items} item(s) generated.`, "ok");
              });
            }}><IconPlay size={15} /> Generate EDI</button>
            {generated && (
              <button className="btn sm" onClick={() => downloadBase64(generated.file)}>
                <IconDownload size={14} /> {generated.file.filename}
              </button>
            )}
          </div>
          {generated && <pre className="edi-preview">{generated.preview}</pre>}
        </section>
      )}

      {/* ═══ CONVERT ═══ */}
      {sub === "convert" && (
        <section className="edi-pane">
          <p className="edi-hint">
            EDI → EDI always goes through the internal pivot: read with the source model, write with
            the target one. N models cover N×N conversions instead of needing N² mappings. When the
            two models name their fields differently, map them:{" "}
            <code>{"{\"target_field\": \"source_field\"}"}</code>.
          </p>
          <div className="edi-form">
            <div className="edi-field">
              <label>EDI file</label>
              <input type="file" ref={convFileRef}
                     onChange={(e) => { setConvFile(e.target.files?.[0] ?? null); setConverted(null); }} />
            </div>
            <ModelPicker label="Source model" models={models} ctl={srcModel} />
            <ModelPicker label="Target model" models={models} ctl={dstModel} />
            <div className="edi-field">
              <label>Field mapping (JSON, optional)</label>
              <textarea className="mono" rows={4} value={mapping} spellCheck={false}
                        placeholder='{"ref_commande": "numero_commande"}'
                        onChange={(e) => setMapping(e.target.value)} />
            </div>
          </div>
          <div className="edi-actions">
            <button className="btn" disabled={!!busy} onClick={() => {
              const s = srcModel.ref(), d = dstModel.ref();
              if (!needFile(convFile) || !needModel(s) || !needModel(d)) return;
              run("convert", async () => {
                const r = await api.ediConvert(convFile, s, d, mapping, "", "");
                setConverted(r);
                notify(`${r.messages} message(s): ${r.source} → ${r.target}.`, "ok");
              });
            }}><IconPlay size={15} /> Convert</button>
            {converted && (
              <button className="btn sm" onClick={() => downloadBase64(converted.file)}>
                <IconDownload size={14} /> {converted.file.filename}
              </button>
            )}
          </div>
          {converted && <pre className="edi-preview">{converted.preview}</pre>}
        </section>
      )}

      {/* ═══ MODELS ═══ */}
      {sub === "models" && (
        <section className="edi-pane">
          <p className="edi-hint">
            Models are versioned artefacts, like configs: saving an existing name adds a version,
            it never overwrites. Start from a sample file — the skeleton is inferred from what the
            file actually contains — then refine it here.
          </p>

          <div className="edi-actions">
            <input type="file" ref={inferRef} hidden onChange={(e) => {
              const f = e.target.files?.[0]; if (!f) return;
              run("infer", async () => {
                const r = await api.ediInfer(f, editorName || f.name);
                setEditorYaml(r.yaml); setNotes(r.notes);
                if (!editorName) setEditorName(`${f.name} (inferred)`);
                notify("Skeleton inferred — review it before saving.", "ok");
              });
              e.target.value = "";
            }} />
            <button className="btn" disabled={!!busy} onClick={() => inferRef.current?.click()}>
              <IconUpload size={15} /> {busy === "infer" ? "Reading…" : "Infer from a sample file"}
            </button>

            <input type="file" ref={yamlUpRef} accept=".yaml,.yml" hidden onChange={(e) => {
              const f = e.target.files?.[0]; if (!f) return;
              f.text().then((t) => { setEditorYaml(t); setNotes([]); if (!editorName) setEditorName(f.name); });
              e.target.value = "";
            }} />
            <button className="btn sm" onClick={() => yamlUpRef.current?.click()}>
              <IconUpload size={14} /> Load a .yaml
            </button>
          </div>

          {notes.length > 0 && (
            <div className="edi-notes">
              <h4>What the inference could not know</h4>
              <ul>{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </div>
          )}

          <div className="edi-form">
            <div className="edi-field">
              <label>Name</label>
              <input value={editorName} onChange={(e) => setEditorName(e.target.value)}
                     placeholder="ORDERS — partner X" />
            </div>
            <div className="edi-field">
              <label>Model YAML</label>
              <textarea className="mono" rows={18} value={editorYaml} spellCheck={false}
                        onChange={(e) => setEditorYaml(e.target.value)} />
            </div>
          </div>

          <div className="edi-actions">
            <button className="btn" disabled={!!busy || !editorYaml.trim()} onClick={() => {
              if (!editorName.trim()) { notify("Name the model first.", "err"); return; }
              run("save", async () => {
                const existing = models.find((m) => m.name === editorName.trim());
                if (existing) {
                  await api.addArtefactVersion("edi_model", existing.id, { yaml: editorYaml });
                  notify(`New version of “${editorName}” saved.`, "ok");
                } else {
                  await api.createArtefact("edi_model", { name: editorName.trim(), yaml: editorYaml });
                  notify(`Model “${editorName}” saved to the library.`, "ok");
                }
                await refreshModels();
              });
            }}><IconSave size={15} /> Save to library</button>
          </div>

          <div className="edi-list">
            <h4>Library <span className="count">{models.length}</span></h4>
            {models.length === 0 && <p className="edi-hint">No EDI model yet.</p>}
            {models.map((m) => (
              <div key={m.id} className="edi-list-row">
                <strong>{m.name}</strong>
                <span className="edi-loc">v{m.latest_version_no}</span>
                <button className="btn sm" onClick={() => run("load", async () => {
                  const r = await api.ediModelYaml(m.id, m.latest_version_no);
                  setEditorName(m.name); setEditorYaml(r.yaml); setNotes([]);
                })}><IconCode size={13} /> Open</button>
                <button className="btn sm" onClick={() => run("archive", async () => {
                  await api.archiveArtefact("edi_model", m.id);
                  await refreshModels();
                  notify(`“${m.name}” archived.`, "ok");
                })}>Archive</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ═══ DOC ═══ */}
      {sub === "doc" && (
        <section className="edi-pane">
          <h3>How EDIFACT is put together</h3>
          <p className="edi-doc-p">
            An <strong>interchange</strong> is the envelope: it opens with <code>UNB</code> (sender,
            recipient, date, reference) and closes with <code>UNZ</code>, which states how many
            messages it contained. Inside, each <strong>message</strong> runs from <code>UNH</code>{" "}
            to <code>UNT</code>; <code>UNH</code> announces its type and directory version
            (<code>ORDERS:D:96A:UN</code>), <code>UNT</code> states its segment count. Those two
            counters are free integrity checks — a wrong count means a truncated or tampered file,
            and the Inspect tab flags it before any model is involved.
          </p>
          <p className="edi-doc-p">
            A message reads in three zones: the <strong>head</strong> (document number, dates,
            trading parties), a <strong>detail loop</strong> repeated per article — this is where
            several items hang under one head — and a <strong>summary</strong> with control totals.
            One file may carry several messages, so several heads: that is why the flat pivot repeats
            head values on every item line and keys everything on <code>message_no</code>.
          </p>
          <p className="edi-doc-p">
            A <strong>segment</strong> starts with a three-letter tag, then data elements, each
            possibly split into components. In <code>NAD+BY+5412345000013::9</code>, the tag is{" "}
            <code>NAD</code>, <code>BY</code> is the qualifier that gives the segment its meaning
            (buyer), and the third element holds a GLN plus the code list it belongs to. The same
            tag says different things depending on its qualifier — which is why models declare one
            variant per qualifier value.
          </p>

          {!kb && <p className="edi-hint">Loading the reference…</p>}
          {kb && (
            <>
              <h4>Separators</h4>
              <p className="edi-doc-p">
                Set by the <code>UNA</code> service string when it is present, and taken as the
                defaults below when it is not. Never assume them: a partner may ship a comma as the
                decimal mark.
              </p>
              <table className="edi-doc-table">
                <thead><tr><th>Role</th><th>Default</th><th>What it does</th></tr></thead>
                <tbody>
                  {kb.separators.map((s) => (
                    <tr key={s.role}>
                      <td>{s.label}</td>
                      <td><code>{s.default === " " ? "␠" : s.default}</code></td>
                      <td>{s.desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h4>Common segments</h4>
              <table className="edi-doc-table">
                <thead><tr><th>Tag</th><th>Name</th><th>Role</th><th>Main elements</th></tr></thead>
                <tbody>
                  {kb.segments.map((s) => (
                    <tr key={s.tag}>
                      <td><code>{s.tag}</code></td>
                      <td>{s.name}</td>
                      <td>{s.desc}</td>
                      <td className="edi-el-col">
                        {s.elements.map((el) => (
                          <span key={el.path}><code>{el.path}</code> {el.label}</span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h4>Qualifiers</h4>
              <p className="edi-doc-p">
                The code that gives a repeated segment its meaning — the value a model keys its{" "}
                <code>when:</code> variants on.
              </p>
              <table className="edi-doc-table">
                <thead><tr><th>Segment</th><th>Code</th><th>Meaning</th></tr></thead>
                <tbody>
                  {kb.qualifiers.map((q) => (
                    <tr key={`${q.tag}-${q.code}`}>
                      <td><code>{q.tag}</code></td><td><code>{q.code}</code></td><td>{q.label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h4>Date formats (element 2379)</h4>
              <table className="edi-doc-table">
                <thead><tr><th>Code</th><th>Layout</th></tr></thead>
                <tbody>
                  {kb.date_formats.map((d) => (
                    <tr key={d.code}><td><code>{d.code}</code></td><td>{d.label}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}
    </div>
  );
}
