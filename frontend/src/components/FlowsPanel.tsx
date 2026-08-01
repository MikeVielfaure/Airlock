import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo, FlowInfo, ReportRow, RunInfo } from "../lib/types";
import { IconLayers, IconPlay, IconReset } from "../lib/icons";

interface Props {
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  /** Feed a run's report straight into the workbench's Report tab, in the
   * same shape a JSON import would produce — one mechanism, two doors in. */
  onOpenReport: (report: ReportRow[], tcoUncovered?: Record<string, { value: string; count: number }[]>) => void;
}

/** A stored run's report is grouped by id (one entry per row, each holding
 * every flagged cell); the workbench reads a flat one row per cell instead. */
function flattenRunReport(rows: { id: string | number;
                                  errors: { column: string; value: string; status: string; message: string }[] }[]): ReportRow[] {
  const out: ReportRow[] = [];
  for (const group of rows) {
    for (const e of group.errors) {
      out.push({ id: group.id, colonne: e.column, valeur_originale: "",
                valeur_finale: e.value, resultat: e.message, statut: e.status as ReportRow["statut"] });
    }
  }
  return out;
}

/**
 * The flows tab: compose stored artefacts (config + tco + computed) into a
 * named flow, run any flow on an uploaded file in one click, and browse the
 * persisted runs (report summary + export download). The library itself is
 * fed from the Yaml / Computed tabs ("save to library") and from the TCO
 * mini-form below.
 */
export function FlowsPanel({ notify, onOpenReport }: Props) {
  const [openRun, setOpenRun] = useState<string>("");
  const [openReport, setOpenReport] = useState<ReportRow[]>([]);
  const [openBusy, setOpenBusy] = useState(false);
  const [configs, setConfigs] = useState<ArtefactInfo[]>([]);
  const [computeds, setComputeds] = useState<ArtefactInfo[]>([]);
  const [tcos, setTcos] = useState<ArtefactInfo[]>([]);
  const [flows, setFlows] = useState<FlowInfo[]>([]);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [loading, setLoading] = useState(true);

  // flow composer
  const [fName, setFName] = useState("");
  const [fConfig, setFConfig] = useState("");
  const [fPinned, setFPinned] = useState(false);       // false = track latest
  const [fTco, setFTco] = useState("");
  const [fComp, setFComp] = useState("");
  const [fExport, setFExport] = useState("export");

  // tco mini-form
  const [tcoName, setTcoName] = useState("");
  const tcoFileRef = useRef<HTMLInputElement>(null);

  // run state
  const [runningFlow, setRunningFlow] = useState<string | null>(null);
  const runFileRef = useRef<HTMLInputElement>(null);
  const runTarget = useRef<FlowInfo | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [c, p, t, f, r] = await Promise.all([
        api.listArtefacts("config"), api.listArtefacts("computed"),
        api.listArtefacts("tco"), api.listFlows(), api.listRuns(),
      ]);
      setConfigs(c); setComputeds(p); setTcos(t); setFlows(f); setRuns(r);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Impossible de charger la bibliothèque.", "err");
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => { refresh(); }, [refresh]);

  const nameOf = (list: ArtefactInfo[], id: string | null) =>
    list.find((a) => a.id === id)?.name ?? (id ? "(archivé)" : "—");

  const createFlow = async () => {
    if (!fName.trim() || !fConfig) { notify("Un flux nécessite un nom et une config.", "err"); return; }
    try {
      await api.createFlow({
        name: fName.trim(), config_artefact_id: fConfig,
        config_version_no: fPinned ? (configs.find((c) => c.id === fConfig)?.latest_version_no ?? null) : null,
        tco_artefact_id: fTco || null, computed_artefact_id: fComp || null,
        default_export_filename: fExport.trim() || "export",
      });
      setFName(""); setFConfig(""); setFTco(""); setFComp(""); setFPinned(false);
      notify("Flux créé.", "ok");
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de la création du flux.", "err"); }
  };

  const saveTco = async () => {
    const file = tcoFileRef.current?.files?.[0];
    if (!tcoName.trim() || !file) { notify("Choisissez un nom et un fichier CSV de TCO.", "err"); return; }
    try {
      const csv = await file.text();
      await api.createArtefact("tco", { name: tcoName.trim(), csv });
      setTcoName(""); if (tcoFileRef.current) tcoFileRef.current.value = "";
      notify("TCO enregistré dans la bibliothèque.", "ok");
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'enregistrement du TCO.", "err"); }
  };

  const askRun = (flow: FlowInfo) => { runTarget.current = flow; runFileRef.current?.click(); };

  const doRun = async (file: File | undefined) => {
    const flow = runTarget.current;
    if (!flow || !file) return;
    setRunningFlow(flow.id);
    try {
      const res = await api.runFlow(flow.id, file);
      if (res.ok) {
        notify(`Flux « ${flow.name} » : OK — ${res.stats?.total_rows ?? 0} ligne(s), export prêt.`, "ok");
      } else {
        notify(`Flux « ${flow.name} » : bloqué à ${res.stage}${res.stats ? ` — ${res.stats.rows_err} ligne(s) en erreur` : ""}.`, "err");
      }
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'exécution.", "err"); }
    finally {
      setRunningFlow(null);
      if (runFileRef.current) runFileRef.current.value = "";
    }
  };

  const del = async (kind: "flow" | "config" | "computed" | "tco", id: string) => {
    try {
      if (kind === "flow") await api.archiveFlow(id);
      else await api.archiveArtefact(kind, id);
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'archivage.", "err"); }
  };

  const fmtDate = (iso: string) => new Date(iso).toLocaleString();

  const toggleRun = async (runId: string) => {
    if (openRun === runId) { setOpenRun(""); return; }
    setOpenBusy(true);
    try {
      const detail = await api.getRun(runId);
      setOpenReport(flattenRunReport(detail.report?.rows ?? []));
      setOpenRun(runId);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setOpenBusy(false); }
  };

  return (
    <div>
      <input type="file" ref={runFileRef} accept=".csv,.xlsx,.xls" style={{ display: "none" }}
        onChange={(e) => doRun(e.target.files?.[0])} />

      <div className="sec-h">
        <h3>Flux</h3>
        <span className="sub">Un flux = une config enregistrée + un TCO optionnel + un ensemble calculé optionnel, sous un même id. Lancez-le sur n'importe quel fichier — l'exécution est conservée avec les versions exactes utilisées.</span>
      </div>

      {loading ? <div className="banner"><span>Chargement de la bibliothèque…</span></div> : (
        <>
          {/* ── composer ── */}
          <div className="flowform">
            <div className="frow"><label>Nom</label>
              <input value={fName} onChange={(e) => setFName(e.target.value)} placeholder="ex. clients-mensuel" /></div>
            <div className="frow"><label>Config</label>
              <select value={fConfig} onChange={(e) => setFConfig(e.target.value)}>
                <option value="">— choisir —</option>
                {configs.map((c) => <option key={c.id} value={c.id}>{c.name} (v{c.latest_version_no})</option>)}
              </select></div>
            <div className="frow"><label>TCO</label>
              <select value={fTco} onChange={(e) => setFTco(e.target.value)}>
                <option value="">aucun</option>
                {tcos.map((t) => <option key={t.id} value={t.id}>{t.name} (v{t.latest_version_no})</option>)}
              </select></div>
            <div className="frow"><label>Computed</label>
              <select value={fComp} onChange={(e) => setFComp(e.target.value)}>
                <option value="">aucun</option>
                {computeds.map((p) => <option key={p.id} value={p.id}>{p.name} (v{p.latest_version_no})</option>)}
              </select></div>
            <div className="frow"><label>Nom d'export</label>
              <input value={fExport} onChange={(e) => setFExport(e.target.value)} /></div>
            <label className="csub" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={fPinned} onChange={(e) => setFPinned(e.target.checked)} />
              Épingler les versions actuelles (décoché = toujours utiliser la dernière version de chaque artefact)
            </label>
            <button className="btn primary" onClick={createFlow}>Créer le flux</button>
          </div>

          {/* ── flows list ── */}
          {flows.length === 0 ? (
            <div className="banner"><span>Aucun flux pour l'instant. Enregistrez une config dans l'onglet Yaml, puis composez-en un ci-dessus.</span></div>
          ) : (
            <table className="libtable">
              <thead><tr><th>Flux</th><th>Config</th><th>TCO</th><th>Computed</th><th>Versions</th><th></th></tr></thead>
              <tbody>
                {flows.map((f) => (
                  <tr key={f.id}>
                    <td><strong>{f.name}</strong><div className="csub mono">{f.id}</div></td>
                    <td>{nameOf(configs, f.config_artefact_id)}</td>
                    <td>{nameOf(tcos, f.tco_artefact_id)}</td>
                    <td>{nameOf(computeds, f.computed_artefact_id)}</td>
                    <td>{f.config_version_no === null ? "dernière" : `épinglée v${f.config_version_no}`}</td>
                    <td className="libactions">
                      <button className="btn sm primary" disabled={runningFlow === f.id} onClick={() => askRun(f)}>
                        <IconPlay size={13} /> {runningFlow === f.id ? "En cours…" : "Lancer sur un fichier"}
                      </button>
                      <button className="btn sm" title="Archiver" onClick={() => del("flow", f.id)}><IconReset size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* ── runs ── */}
          <div className="sec-h" style={{ marginTop: 26 }}>
            <h3>Exécutions enregistrées</h3>
            <span className="sub">Chaque exécution de flux est conservée avec les versions figées des artefacts utilisés.</span>
          </div>
          {runs.length === 0 ? (
            <div className="banner"><span>Aucune exécution pour l'instant.</span></div>
          ) : (
            <table className="libtable">
              <thead><tr><th>Date</th><th>Flux</th><th>Fichier</th><th>Résultat</th><th>Lignes</th><th></th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <Fragment key={r.id}>
                    <tr>
                      <td>{fmtDate(r.created_at)}</td>
                      <td>{r.flow_name}</td>
                      <td className="mono">{r.source_name}</td>
                      <td>{r.ok
                        ? <span className="runok">OK</span>
                        : <span className="runko">KO · {r.stage}{r.rows_error ? ` · ${r.rows_error} err` : ""}</span>}</td>
                      <td>{r.rows_total.toLocaleString()}</td>
                      <td className="libactions">
                        <button className="btn sm" disabled={openBusy} onClick={() => toggleRun(r.id)}>
                          {openRun === r.id ? "Fermer" : "Voir"}
                        </button>
                        {r.ok && <a className="btn sm" href={`/api/runs/${r.id}/export`}>Exporter</a>}
                        <a className="btn sm" href={`/api/runs/${r.id}`} target="_blank" rel="noreferrer">Rapport (json)</a>
                      </td>
                    </tr>
                    {openRun === r.id && (
                      <tr>
                        <td colSpan={6} style={{ background: "var(--panel-2)", padding: "10px 12px" }}>
                          {openReport.length === 0 ? (
                            <span className="csub">Aucune cellule signalée dans ce rapport.</span>
                          ) : (
                            <>
                              <div style={{ display: "flex", justifyContent: "space-between",
                                          alignItems: "center", marginBottom: 8 }}>
                                <span className="csub">{openReport.length} ligne(s)</span>
                                <button className="btn sm" onClick={() => onOpenReport(openReport)}>
                                  <IconLayers size={13} /> Ouvrir dans Rapport
                                </button>
                              </div>
                              <div className="tablewrap">
                                <table className="grid">
                                  <thead>
                                    <tr><th>id</th><th>colonne</th><th>valeur</th><th>résultat</th><th>statut</th></tr>
                                  </thead>
                                  <tbody>
                                    {openReport.slice(0, 500).map((row, i) => (
                                      <tr key={i}>
                                        <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{String(row.id)}</td>
                                        <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{row.colonne}</td>
                                        <td>{row.valeur_finale}</td>
                                        <td style={{ color: "var(--ink-soft)", fontSize: 12 }}>{row.resultat}</td>
                                        <td><span className={`statustag ${row.statut}`}>{row.statut}</span></td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                              {openReport.length > 500 && (
                                <div className="csub" style={{ marginTop: 6 }}>
                                  Affichage des 500 premières sur {openReport.length.toLocaleString()} — ouvrez dans Rapport ou exportez pour le reste.
                                </div>
                              )}
                            </>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}

          {/* ── library inventory + tco upload ── */}
          <div className="sec-h" style={{ marginTop: 26 }}>
            <h3>Bibliothèque</h3>
            <span className="sub">Les configs sont enregistrées depuis l'onglet Yaml, les ensembles calculés depuis l'onglet Computed. Les TCO sont ajoutés ici.</span>
          </div>
          <div className="libgrid">
            {(["config", "computed", "tco"] as const).map((kind) => {
              const list = kind === "config" ? configs : kind === "computed" ? computeds : tcos;
              return (
                <div key={kind} className="libcol">
                  <div className="libcol-h">{kind} <span className="csub">({list.length})</span></div>
                  {list.length === 0 && <div className="csub">vide</div>}
                  {list.map((a) => (
                    <div key={a.id} className="libitem">
                      <span>{a.name} <span className="csub">v{a.latest_version_no}</span></span>
                      <button className="btn sm" title="Archiver" onClick={() => del(kind, a.id)}>×</button>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
          <div className="flowform" style={{ marginTop: 12 }}>
            <div className="frow"><label>Nom du nouveau TCO</label>
              <input value={tcoName} onChange={(e) => setTcoName(e.target.value)} placeholder="ex. civilites" /></div>
            <div className="frow"><label>Fichier CSV</label>
              <input type="file" ref={tcoFileRef} accept=".csv,.txt" /></div>
            <button className="btn" onClick={saveTco}>Enregistrer le TCO dans la bibliothèque</button>
          </div>
        </>
      )}
    </div>
  );
}
