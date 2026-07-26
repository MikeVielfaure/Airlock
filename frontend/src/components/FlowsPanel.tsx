import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo, FlowInfo, RunInfo } from "../lib/types";
import { IconPlay, IconReset } from "../lib/icons";

interface Props {
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
}

/**
 * The flows tab: compose stored artefacts (config + tco + computed) into a
 * named flow, run any flow on an uploaded file in one click, and browse the
 * persisted runs (report summary + export download). The library itself is
 * fed from the Yaml / Computed tabs ("save to library") and from the TCO
 * mini-form below.
 */
export function FlowsPanel({ notify }: Props) {
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
      notify(e instanceof Error ? e.message : "Could not load the library.", "err");
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => { refresh(); }, [refresh]);

  const nameOf = (list: ArtefactInfo[], id: string | null) =>
    list.find((a) => a.id === id)?.name ?? (id ? "(archived)" : "—");

  const createFlow = async () => {
    if (!fName.trim() || !fConfig) { notify("A flow needs a name and a config.", "err"); return; }
    try {
      await api.createFlow({
        name: fName.trim(), config_artefact_id: fConfig,
        config_version_no: fPinned ? (configs.find((c) => c.id === fConfig)?.latest_version_no ?? null) : null,
        tco_artefact_id: fTco || null, computed_artefact_id: fComp || null,
        default_export_filename: fExport.trim() || "export",
      });
      setFName(""); setFConfig(""); setFTco(""); setFComp(""); setFPinned(false);
      notify("Flow created.", "ok");
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Flow creation failed.", "err"); }
  };

  const saveTco = async () => {
    const file = tcoFileRef.current?.files?.[0];
    if (!tcoName.trim() || !file) { notify("Pick a name and a TCO CSV file.", "err"); return; }
    try {
      const csv = await file.text();
      await api.createArtefact("tco", { name: tcoName.trim(), csv });
      setTcoName(""); if (tcoFileRef.current) tcoFileRef.current.value = "";
      notify("TCO saved to the library.", "ok");
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "TCO save failed.", "err"); }
  };

  const askRun = (flow: FlowInfo) => { runTarget.current = flow; runFileRef.current?.click(); };

  const doRun = async (file: File | undefined) => {
    const flow = runTarget.current;
    if (!flow || !file) return;
    setRunningFlow(flow.id);
    try {
      const res = await api.runFlow(flow.id, file);
      if (res.ok) {
        notify(`Flow « ${flow.name} » : OK — ${res.stats?.total_rows ?? 0} rows, export ready.`, "ok");
      } else {
        notify(`Flow « ${flow.name} » : blocked at ${res.stage}${res.stats ? ` — ${res.stats.rows_err} row(s) in error` : ""}.`, "err");
      }
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Run failed.", "err"); }
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
    } catch (e) { notify(e instanceof Error ? e.message : "Archive failed.", "err"); }
  };

  const fmtDate = (iso: string) => new Date(iso).toLocaleString();

  return (
    <div>
      <input type="file" ref={runFileRef} accept=".csv,.xlsx,.xls" style={{ display: "none" }}
        onChange={(e) => doRun(e.target.files?.[0])} />

      <div className="sec-h">
        <h3>Flows</h3>
        <span className="sub">A flow = a stored config + optional TCO + optional computed set, under one id. Run it on any file — the run is persisted with the exact versions it used.</span>
      </div>

      {loading ? <div className="banner"><span>Loading the library…</span></div> : (
        <>
          {/* ── composer ── */}
          <div className="flowform">
            <div className="frow"><label>Name</label>
              <input value={fName} onChange={(e) => setFName(e.target.value)} placeholder="e.g. clients-mensuel" /></div>
            <div className="frow"><label>Config</label>
              <select value={fConfig} onChange={(e) => setFConfig(e.target.value)}>
                <option value="">— choose —</option>
                {configs.map((c) => <option key={c.id} value={c.id}>{c.name} (v{c.latest_version_no})</option>)}
              </select></div>
            <div className="frow"><label>TCO</label>
              <select value={fTco} onChange={(e) => setFTco(e.target.value)}>
                <option value="">none</option>
                {tcos.map((t) => <option key={t.id} value={t.id}>{t.name} (v{t.latest_version_no})</option>)}
              </select></div>
            <div className="frow"><label>Computed</label>
              <select value={fComp} onChange={(e) => setFComp(e.target.value)}>
                <option value="">none</option>
                {computeds.map((p) => <option key={p.id} value={p.id}>{p.name} (v{p.latest_version_no})</option>)}
              </select></div>
            <div className="frow"><label>Export name</label>
              <input value={fExport} onChange={(e) => setFExport(e.target.value)} /></div>
            <label className="csub" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={fPinned} onChange={(e) => setFPinned(e.target.checked)} />
              Pin current versions (unchecked = always use the latest version of each artefact)
            </label>
            <button className="btn primary" onClick={createFlow}>Create flow</button>
          </div>

          {/* ── flows list ── */}
          {flows.length === 0 ? (
            <div className="banner"><span>No flow yet. Save a config in the Yaml tab, then compose one above.</span></div>
          ) : (
            <table className="libtable">
              <thead><tr><th>Flow</th><th>Config</th><th>TCO</th><th>Computed</th><th>Versions</th><th></th></tr></thead>
              <tbody>
                {flows.map((f) => (
                  <tr key={f.id}>
                    <td><strong>{f.name}</strong><div className="csub mono">{f.id}</div></td>
                    <td>{nameOf(configs, f.config_artefact_id)}</td>
                    <td>{nameOf(tcos, f.tco_artefact_id)}</td>
                    <td>{nameOf(computeds, f.computed_artefact_id)}</td>
                    <td>{f.config_version_no === null ? "latest" : `pinned v${f.config_version_no}`}</td>
                    <td className="libactions">
                      <button className="btn sm primary" disabled={runningFlow === f.id} onClick={() => askRun(f)}>
                        <IconPlay size={13} /> {runningFlow === f.id ? "Running…" : "Run on a file"}
                      </button>
                      <button className="btn sm" title="Archive" onClick={() => del("flow", f.id)}><IconReset size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* ── runs ── */}
          <div className="sec-h" style={{ marginTop: 26 }}>
            <h3>Stored runs</h3>
            <span className="sub">Every flow run is persisted with the frozen artefact versions it used.</span>
          </div>
          {runs.length === 0 ? (
            <div className="banner"><span>No run yet.</span></div>
          ) : (
            <table className="libtable">
              <thead><tr><th>When</th><th>Flow</th><th>File</th><th>Result</th><th>Rows</th><th></th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.created_at)}</td>
                    <td>{r.flow_name}</td>
                    <td className="mono">{r.source_name}</td>
                    <td>{r.ok
                      ? <span className="runok">OK</span>
                      : <span className="runko">KO · {r.stage}{r.rows_error ? ` · ${r.rows_error} err` : ""}</span>}</td>
                    <td>{r.rows_total.toLocaleString()}</td>
                    <td className="libactions">
                      {r.ok && <a className="btn sm" href={`/api/runs/${r.id}/export`}>Export</a>}
                      <a className="btn sm" href={`/api/runs/${r.id}`} target="_blank" rel="noreferrer">Report (json)</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* ── library inventory + tco upload ── */}
          <div className="sec-h" style={{ marginTop: 26 }}>
            <h3>Library</h3>
            <span className="sub">Configs are saved from the Yaml tab, computed sets from the Computed tab. TCOs are added here.</span>
          </div>
          <div className="libgrid">
            {(["config", "computed", "tco"] as const).map((kind) => {
              const list = kind === "config" ? configs : kind === "computed" ? computeds : tcos;
              return (
                <div key={kind} className="libcol">
                  <div className="libcol-h">{kind} <span className="csub">({list.length})</span></div>
                  {list.length === 0 && <div className="csub">empty</div>}
                  {list.map((a) => (
                    <div key={a.id} className="libitem">
                      <span>{a.name} <span className="csub">v{a.latest_version_no}</span></span>
                      <button className="btn sm" title="Archive" onClick={() => del(kind, a.id)}>×</button>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
          <div className="flowform" style={{ marginTop: 12 }}>
            <div className="frow"><label>New TCO name</label>
              <input value={tcoName} onChange={(e) => setTcoName(e.target.value)} placeholder="e.g. civilites" /></div>
            <div className="frow"><label>CSV file</label>
              <input type="file" ref={tcoFileRef} accept=".csv,.txt" /></div>
            <button className="btn" onClick={saveTco}>Save TCO to library</button>
          </div>
        </>
      )}
    </div>
  );
}
