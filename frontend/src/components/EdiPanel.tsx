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
          <option value="">— choisir un modèle —</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.name} (v{m.latest_version_no})</option>
          ))}
          <option value="__inline">✎ coller le YAML directement…</option>
        </select>
        {!ctl.inline && ctl.id && (
          <label className="edi-check" title="Figer la version au lieu de suivre la dernière">
            <input type="checkbox" checked={ctl.pinned}
                   onChange={(e) => ctl.setPinned(e.target.checked)} /> figer
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
        {caption} — {p.total_rows} ligne{p.total_rows === 1 ? "" : "s"}, {p.columns.length} colonnes
        {p.shown_rows < p.total_rows && ` (affichage de ${p.shown_rows})`}
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
    catch (e) { notify(e instanceof Error ? e.message : "Impossible de charger les modèles EDI.", "err"); }
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
    if (!r) { notify("Choisissez d'abord un modèle (bibliothèque ou YAML inline).", "err"); return false; }
    return true;
  };
  const needFile = (f: File | null): f is File => {
    if (!f) { notify("Choisissez d'abord un fichier.", "err"); return false; }
    return true;
  };

  return (
    <div className="edi">
      <nav className="edi-subtabs">
        {([["inspect", "Inspecter", <IconList size={14} />],
           ["transform", "Transformer", <IconTable size={14} />],
           ["generate", "Générer", <IconCode size={14} />],
           ["convert", "Convertir", <IconLayers size={14} />],
           ["models", "Modèles", <IconSave size={14} />],
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
            Les fichiers EDI portent rarement une extension — le format est détecté à partir du
            contenu (UNA/UNB pour EDIFACT). Les compteurs d'enveloppe et les références sont
            vérifiés ici sans aucun modèle : c'est le contrôle structurel gratuit.
          </p>
          <input type="file" ref={inspectRef} hidden onChange={(e) => {
            const f = e.target.files?.[0]; if (!f) return;
            run("inspect", async () => { setInspected(await api.ediInspect(f)); });
            e.target.value = "";
          }} />
          <button className="btn" onClick={() => inspectRef.current?.click()} disabled={!!busy}>
            <IconUpload size={15} /> {busy === "inspect" ? "Lecture…" : "Ouvrir un fichier EDI"}
          </button>

          {inspected && (
            <>
              <div className="edi-summary">
                <span className="pill">{inspected.format}</span>
                <span className="pill">{inspected.had_una ? "UNA présent" : "séparateurs par défaut"}</span>
                <span className="pill">{inspected.interchanges.length} interchange(s)</span>
                <span className="pill">{inspected.total_segments} segments</span>
                <span className={`pill ${inspected.syntax_errors.length ? "err" : "ok"}`}>
                  {inspected.syntax_errors.length
                    ? `${inspected.syntax_errors.length} erreur(s) de syntaxe`
                    : "enveloppe cohérente"}
                </span>
                {inspected.truncated && (
                  <span className="pill warn" title="Seuls les premiers segments sont décodés — les contrôles ci-dessus portent quand même sur tout le fichier">
                    arbre tronqué
                  </span>
                )}
              </div>
              <ErrorList title="Syntaxe" errors={inspected.syntax_errors} />
              {inspected.interchanges.map((it, i) => (
                <div key={i} className="edi-inter">
                  <h4>
                    Interchange {it.ref || "(sans réf.)"}{" "}
                    <span className="edi-loc">{it.sender} → {it.recipient}</span>
                    {it.implicit && <span className="pill warn">sans UNB</span>}
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
            Contrôler un fichier contre un modèle, puis le mettre à plat. <strong>Plat</strong> répète
            la tête sur chaque ligne de détail — une seule table, prête pour le pipeline de nettoyage.{" "}
            <strong>Lié</strong> garde deux tables jointes sur <code>message_no</code>.
          </p>
          <div className="edi-form">
            <div className="edi-field">
              <label>Fichier EDI</label>
              <input type="file" ref={ediFileRef} onChange={(e) => {
                setEdiFile(e.target.files?.[0] ?? null); setReport(null); setPivotOut(null);
              }} />
            </div>
            <ModelPicker label="Modèle" models={models} ctl={tModel} />
            <div className="edi-field">
              <label>Mode de pivot</label>
              <div className="edi-row">
                <label className="edi-check">
                  <input type="radio" checked={mode === "flat"} onChange={() => setMode("flat")} /> plat
                </label>
                <label className="edi-check">
                  <input type="radio" checked={mode === "linked"} onChange={() => setMode("linked")} /> lié
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
                notify(r.ok ? `Valide — ${r.stats.messages} message(s), ${r.stats.items} ligne(s).`
                            : `${r.stats.errors} erreur(s) trouvée(s).`, r.ok ? "ok" : "err");
              });
            }}><IconCheck size={15} /> Contrôler</button>

            <button className="btn" disabled={!!busy} onClick={() => {
              const m = tModel.ref();
              if (!needFile(ediFile) || !needModel(m)) return;
              run("pivot", async () => {
                setPivotOut(await api.ediPivot(ediFile, m, mode, "preview") as EdiPivotPreview);
              });
            }}><IconPlay size={15} /> Pivoter</button>

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
                  notify("Table pivotée ouverte dans la vue Data.", "ok");
                });
              }}><IconTable size={14} /> Ouvrir dans Data</button>
            )}
          </div>

          {report && (
            <>
              <div className="edi-summary">
                <span className={`pill ${report.ok ? "ok" : "err"}`}>{report.ok ? "valide" : "erreurs"}</span>
                <span className="pill">{report.model_name}</span>
                <span className="pill">{report.stats.messages} message(s)</span>
                <span className="pill">{report.stats.items} ligne(s)</span>
              </div>
              <ErrorList title="Syntaxe" errors={report.syntax_errors} />
              <ErrorList title="Modèle" errors={report.model_errors} />
            </>
          )}

          {pivotOut?.flat && <MiniTable p={pivotOut.flat} caption="Plat" />}
          {pivotOut?.heads && <MiniTable p={pivotOut.heads} caption="Têtes" />}
          {pivotOut?.items && <MiniTable p={pivotOut.items} caption="Lignes" />}
        </section>
      )}

      {/* ═══ GENERATE ═══ */}
      {sub === "generate" && (
        <section className="edi-pane">
          <p className="edi-hint">
            Le trajet inverse : un CSV/XLSX plat dont les en-têtes correspondent aux noms de champs
            du modèle devient de l'EDIFACT. Les lignes sont regroupées en messages, les compteurs
            UNT/UNZ sont calculés, et les caractères réservés des données sont échappés.
          </p>
          <div className="edi-form">
            <div className="edi-field">
              <label>Fichier plat (CSV ou XLSX)</label>
              <input type="file" ref={tabFileRef} accept=".csv,.xlsx,.xls,.txt"
                     onChange={(e) => { setTabFile(e.target.files?.[0] ?? null); setGenerated(null); }} />
            </div>
            <ModelPicker label="Modèle" models={models} ctl={gModel} />
            <div className="edi-field">
              <label>Regrouper les lignes en messages par</label>
              <input value={gGroup} onChange={(e) => setGGroup(e.target.value)}
                     placeholder="nom de colonne — par défaut : message_no, sinon un seul message" />
            </div>
            <div className="edi-field">
              <label>Interchange</label>
              <div className="edi-row">
                <input value={gSender} onChange={(e) => setGSender(e.target.value)} placeholder="expéditeur (GLN:14)" />
                <input value={gRecipient} onChange={(e) => setGRecipient(e.target.value)} placeholder="destinataire" />
                <input value={gRef} onChange={(e) => setGRef(e.target.value)} placeholder="référence" />
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
                notify(`${r.messages} message(s), ${r.items} ligne(s) générée(s).`, "ok");
              });
            }}><IconPlay size={15} /> Générer EDI</button>
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
            EDI → EDI passe toujours par le pivot interne : lecture avec le modèle source, écriture
            avec le modèle cible. N modèles couvrent N×N conversions au lieu de nécessiter N²
            mappings. Quand les deux modèles nomment leurs champs différemment, les faire
            correspondre :{" "}
            <code>{"{\"target_field\": \"source_field\"}"}</code>.
          </p>
          <div className="edi-form">
            <div className="edi-field">
              <label>Fichier EDI</label>
              <input type="file" ref={convFileRef}
                     onChange={(e) => { setConvFile(e.target.files?.[0] ?? null); setConverted(null); }} />
            </div>
            <ModelPicker label="Modèle source" models={models} ctl={srcModel} />
            <ModelPicker label="Modèle cible" models={models} ctl={dstModel} />
            <div className="edi-field">
              <label>Mapping des champs (JSON, optionnel)</label>
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
                notify(`${r.messages} message(s) : ${r.source} → ${r.target}.`, "ok");
              });
            }}><IconPlay size={15} /> Convertir</button>
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
            Les modèles sont des artefacts versionnés, comme les configs : enregistrer sous un nom
            existant ajoute une version, jamais un écrasement. Partir d'un fichier exemple — le
            squelette est déduit de ce que le fichier contient réellement — puis l'affiner ici.
          </p>

          <div className="edi-actions">
            <input type="file" ref={inferRef} hidden onChange={(e) => {
              const f = e.target.files?.[0]; if (!f) return;
              run("infer", async () => {
                const r = await api.ediInfer(f, editorName || f.name);
                setEditorYaml(r.yaml); setNotes(r.notes);
                if (!editorName) setEditorName(`${f.name} (déduit)`);
                notify("Squelette déduit — à vérifier avant l'enregistrement.", "ok");
              });
              e.target.value = "";
            }} />
            <button className="btn" disabled={!!busy} onClick={() => inferRef.current?.click()}>
              <IconUpload size={15} /> {busy === "infer" ? "Lecture…" : "Déduire depuis un fichier exemple"}
            </button>

            <input type="file" ref={yamlUpRef} accept=".yaml,.yml" hidden onChange={(e) => {
              const f = e.target.files?.[0]; if (!f) return;
              f.text().then((t) => { setEditorYaml(t); setNotes([]); if (!editorName) setEditorName(f.name); });
              e.target.value = "";
            }} />
            <button className="btn sm" onClick={() => yamlUpRef.current?.click()}>
              <IconUpload size={14} /> Charger un .yaml
            </button>
          </div>

          {notes.length > 0 && (
            <div className="edi-notes">
              <h4>Ce que la déduction n'a pas pu savoir</h4>
              <ul>{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </div>
          )}

          <div className="edi-form">
            <div className="edi-field">
              <label>Nom</label>
              <input value={editorName} onChange={(e) => setEditorName(e.target.value)}
                     placeholder="ORDERS — partenaire X" />
            </div>
            <div className="edi-field">
              <label>YAML du modèle</label>
              <textarea className="mono" rows={18} value={editorYaml} spellCheck={false}
                        onChange={(e) => setEditorYaml(e.target.value)} />
            </div>
          </div>

          <div className="edi-actions">
            <button className="btn" disabled={!!busy || !editorYaml.trim()} onClick={() => {
              if (!editorName.trim()) { notify("Nommez d'abord le modèle.", "err"); return; }
              run("save", async () => {
                const existing = models.find((m) => m.name === editorName.trim());
                if (existing) {
                  await api.addArtefactVersion("edi_model", existing.id, { yaml: editorYaml });
                  notify(`Nouvelle version de « ${editorName} » enregistrée.`, "ok");
                } else {
                  await api.createArtefact("edi_model", { name: editorName.trim(), yaml: editorYaml });
                  notify(`Modèle « ${editorName} » enregistré dans la bibliothèque.`, "ok");
                }
                await refreshModels();
              });
            }}><IconSave size={15} /> Enregistrer dans la bibliothèque</button>
          </div>

          <div className="edi-list">
            <h4>Bibliothèque <span className="count">{models.length}</span></h4>
            {models.length === 0 && <p className="edi-hint">Aucun modèle EDI pour l'instant.</p>}
            {models.map((m) => (
              <div key={m.id} className="edi-list-row">
                <strong>{m.name}</strong>
                <span className="edi-loc">v{m.latest_version_no}</span>
                <button className="btn sm" onClick={() => run("load", async () => {
                  const r = await api.ediModelYaml(m.id, m.latest_version_no);
                  setEditorName(m.name); setEditorYaml(r.yaml); setNotes([]);
                })}><IconCode size={13} /> Ouvrir</button>
                <button className="btn sm" onClick={() => run("archive", async () => {
                  await api.archiveArtefact("edi_model", m.id);
                  await refreshModels();
                  notify(`« ${m.name} » archivé.`, "ok");
                })}>Archiver</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ═══ DOC ═══ */}
      {sub === "doc" && (
        <section className="edi-pane">
          <h3>Comment EDIFACT est structuré</h3>
          <p className="edi-doc-p">
            Un <strong>interchange</strong> est l'enveloppe : il s'ouvre avec <code>UNB</code>{" "}
            (expéditeur, destinataire, date, référence) et se ferme avec <code>UNZ</code>, qui
            indique combien de messages il contenait. À l'intérieur, chaque <strong>message</strong>{" "}
            va de <code>UNH</code> à <code>UNT</code> ; <code>UNH</code> annonce son type et sa
            version d'annuaire (<code>ORDERS:D:96A:UN</code>), <code>UNT</code> indique son nombre
            de segments. Ces deux compteurs sont des contrôles d'intégrité gratuits — un mauvais
            compte signale un fichier tronqué ou altéré, et l'onglet Inspecter le signale avant
            même qu'un modèle intervienne.
          </p>
          <p className="edi-doc-p">
            Un message se lit en trois zones : la <strong>tête</strong> (numéro de document, dates,
            partenaires commerciaux), une <strong>boucle de détail</strong> répétée par article —
            c'est là que plusieurs lignes se rattachent à une même tête — et un{" "}
            <strong>résumé</strong> avec les totaux de contrôle. Un fichier peut porter plusieurs
            messages, donc plusieurs têtes : c'est pourquoi le pivot à plat répète les valeurs de
            tête sur chaque ligne de détail et indexe tout sur <code>message_no</code>.
          </p>
          <p className="edi-doc-p">
            Un <strong>segment</strong> commence par un tag de trois lettres, puis des éléments de
            données, chacun pouvant être découpé en composants. Dans{" "}
            <code>NAD+BY+5412345000013::9</code>, le tag est <code>NAD</code>, <code>BY</code> est
            le qualifiant qui donne son sens au segment (acheteur), et le troisième élément porte un
            GLN ainsi que la liste de codes à laquelle il appartient. Un même tag dit des choses
            différentes selon son qualifiant — c'est pourquoi les modèles déclarent une variante par
            valeur de qualifiant.
          </p>

          {!kb && <p className="edi-hint">Chargement de la référence…</p>}
          {kb && (
            <>
              <h4>Séparateurs</h4>
              <p className="edi-doc-p">
                Définis par la chaîne de service <code>UNA</code> quand elle est présente, et pris
                comme valeurs par défaut ci-dessous quand elle ne l'est pas. Ne jamais les supposer :
                un partenaire peut envoyer une virgule comme marque décimale.
              </p>
              <table className="edi-doc-table">
                <thead><tr><th>Rôle</th><th>Défaut</th><th>Effet</th></tr></thead>
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

              <h4>Segments courants</h4>
              <table className="edi-doc-table">
                <thead><tr><th>Tag</th><th>Nom</th><th>Rôle</th><th>Éléments principaux</th></tr></thead>
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

              <h4>Qualifiants</h4>
              <p className="edi-doc-p">
                Le code qui donne son sens à un segment répété — la valeur sur laquelle un modèle
                indexe ses variantes <code>when:</code>.
              </p>
              <table className="edi-doc-table">
                <thead><tr><th>Segment</th><th>Code</th><th>Signification</th></tr></thead>
                <tbody>
                  {kb.qualifiers.map((q) => (
                    <tr key={`${q.tag}-${q.code}`}>
                      <td><code>{q.tag}</code></td><td><code>{q.code}</code></td><td>{q.label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h4>Formats de date (élément 2379)</h4>
              <table className="edi-doc-table">
                <thead><tr><th>Code</th><th>Format</th></tr></thead>
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
