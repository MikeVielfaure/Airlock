import { useMemo, useRef, useState } from "react";
import type { CellStatus, ProcessResponse, ReportRow } from "../lib/types";
import { IconDownload, IconUpload } from "../lib/icons";
import { InfoTip } from "./InfoTip";

const STATUSES: CellStatus[] = ["ERROR", "CLEANED", "MAPPING_KO", "MAPPING_OK", "NO_TCO", "OK"];
type Shape = "long" | "by_id" | "pivot";

/** A report saved to disk, so it can be reopened later or handed over from
 * elsewhere (a stored flow run) without having just run a validation. */
interface PortableReport {
  kind: "file-explorer-report";
  report: ReportRow[];
  tco_uncovered?: Record<string, { value: string; count: number }[]>;
}

interface Props {
  result: ProcessResponse | null;
  sid: string | null;
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  onLoadReport: (report: ReportRow[], tcoUncovered?: Record<string, { value: string; count: number }[]>) => void;
}

type PivotValue = "status" | "message" | "final";

export function ReportPanel({ result, sid, notify, onLoadReport }: Props) {
  const [filter, setFilter] = useState<CellStatus | "ALL" | "ISSUES">("ISSUES");
  const [shape, setShape] = useState<Shape>("long");
  const [fmt, setFmt] = useState<"csv" | "xlsx">("csv");
  const [pivotValue, setPivotValue] = useState<PivotValue>("status");
  const importRef = useRef<HTMLInputElement>(null);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    result?.report.forEach((r) => { c[r.statut] = (c[r.statut] ?? 0) + 1; });
    return c;
  }, [result]);

  // The same status filter feeds both the flat list and the pivot — a
  // download and the on-screen preview must never disagree about which rows
  // are in scope.
  const rows = useMemo(() => (result?.report ?? []).filter((r) => {
    if (filter === "ALL") return true;
    if (filter === "ISSUES") return r.statut === "ERROR" || r.statut === "MAPPING_KO" || r.statut === "NO_TCO";
    return r.statut === filter;
  }), [result, filter]);

  // id × column, cell = status/message/final for that checked cell — entirely
  // client-side, from rows already in memory, so it works identically for a
  // live run, an imported report, or one opened from a stored flow.
  const pivot = useMemo(() => {
    const cols: string[] = [];
    const seenCols = new Set<string>();
    const byId = new Map<string, Record<string, string[]>>();
    const idOrder: (string | number)[] = [];
    for (const r of rows) {
      if (!seenCols.has(r.colonne)) { seenCols.add(r.colonne); cols.push(r.colonne); }
      const key = String(r.id);
      let cell = byId.get(key);
      if (!cell) { cell = {}; byId.set(key, cell); idOrder.push(r.id); }
      const v = pivotValue === "message" ? r.resultat : pivotValue === "final" ? r.valeur_finale : r.statut;
      (cell[r.colonne] ??= []).push(v);
    }
    return { cols, idOrder, byId };
  }, [rows, pivotValue]);

  const importReport = async (file: File) => {
    try {
      const data = JSON.parse(await file.text()) as Partial<PortableReport>;
      if (!Array.isArray(data.report)) throw new Error("Fichier non reconnu comme un rapport.");
      onLoadReport(data.report, data.tco_uncovered ?? {});
      notify(`Rapport importé — ${data.report.length} ligne(s).`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  const importButton = (
    <>
      <button className="btn sm" onClick={() => importRef.current?.click()}>
        <IconUpload size={14} /> Importer (JSON)
      </button>
      <input ref={importRef} type="file" accept=".json" hidden
             onChange={(e) => { const f = e.target.files?.[0]; if (f) importReport(f); e.target.value = ""; }} />
    </>
  );

  if (!result) {
    return (
      <div>
        <div className="banner"><span>Lancez d'abord une validation — le rapport liste chaque cellule et ce qui lui est arrivé.</span></div>
        <div style={{ marginTop: 10 }}>{importButton}</div>
      </div>
    );
  }

  const exportJson = () => {
    const payload: PortableReport = { kind: "file-explorer-report", report: result.report,
                                      tco_uncovered: result.tco_uncovered ?? {} };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "rapport.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // statuses implied by the active pill, used both for the preview and the download
  const statusesFor = (): string => {
    if (filter === "ISSUES") return "ERROR,MAPPING_KO,NO_TCO";
    if (filter === "ALL") return STATUSES.join(",");
    return filter;
  };

  const download = () => {
    if (!sid) return;
    const qs = new URLSearchParams({ shape, statuses: statusesFor(), fmt, value: pivotValue, download: "1" });
    const a = document.createElement("a");
    a.href = `/api/files/${sid}/report?${qs.toString()}`;
    a.click();
  };

  const uncovered = result.tco_uncovered ?? {};
  const uncoveredCols = Object.keys(uncovered).filter((c) => uncovered[c]?.length);
  // A report opened from a flow run or a JSON import never carries the
  // pre-clean value — showing an all-∅ "original" column next to "final"
  // would be noise, and "final" makes no sense without it to contrast against.
  const hasOriginal = result.report.some((r) => r.valeur_originale);

  return (
    <div>
      <div className="sec-h">
        <h3>Rapport</h3>
        <span className="sub">Une ligne par cellule vérifiée.</span>
        <InfoTip>
          <p><b>À quoi ça sert</b> — voir précisément ce qui s'est passé sur chaque cellule contrôlée : erreur, nettoyage, correspondance TCO.</p>
          <p><b>Comment faire</b> — choisissez la forme (plat, groupé par id, ou pivot id × colonne) selon ce que vous voulez lire ; « plat » est le plus détaillé.</p>
          <p><b>Ce qu'il faut</b> — une validation déjà lancée dans Données. Sans elle, le rapport est vide.</p>
        </InfoTip>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <label className="csub">Forme
            <select className="mono-input" style={{ marginLeft: 4 }} value={shape} onChange={(e) => setShape(e.target.value as Shape)}>
              <option value="long">plat (une ligne par cellule)</option>
              <option value="by_id">groupé par id</option>
              <option value="pivot">pivot (id × colonne)</option>
            </select>
          </label>
          {shape === "pivot" && (
            <label className="csub">Cellule
              <select className="mono-input" style={{ marginLeft: 4 }} value={pivotValue}
                      onChange={(e) => setPivotValue(e.target.value as PivotValue)}>
                <option value="status">statut</option>
                <option value="message">message</option>
                <option value="final">valeur finale</option>
              </select>
            </label>
          )}
          <select className="mono-input" value={fmt} onChange={(e) => setFmt(e.target.value as "csv" | "xlsx")}>
            <option value="csv">CSV</option>
            <option value="xlsx">Excel</option>
          </select>
          <button className="btn sm" onClick={download}><IconDownload size={14} /> Télécharger</button>
          <button className="btn sm" onClick={exportJson}><IconDownload size={14} /> Exporter (JSON)</button>
          {importButton}
        </span>
      </div>

      {uncoveredCols.length > 0 && (
        <div className="tco-coverage">
          <div className="tco-coverage-h">TCO — valeurs non couvertes (ajoutez-les à votre table de correspondance)</div>
          {uncoveredCols.map((col) => (
            <div key={col} className="tco-col">
              <span className="tco-colname">{col}</span>
              <div className="tco-vals">
                {uncovered[col].slice(0, 40).map((v) => (
                  <span key={v.value} className="tco-val" title={`${v.count} ligne(s)`}>
                    {v.value || "∅"} <span className="tco-count">{v.count}</span>
                  </span>
                ))}
                {uncovered[col].length > 40 && <span className="csub">+{uncovered[col].length - 40} de plus</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="filterbar">
        <button className={`statpill ${filter === "ISSUES" ? "on" : ""}`} onClick={() => setFilter("ISSUES")}>
          Problèmes <span className="n">{(counts.ERROR ?? 0) + (counts.MAPPING_KO ?? 0) + (counts.NO_TCO ?? 0)}</span>
        </button>
        <button className={`statpill ${filter === "ALL" ? "on" : ""}`} onClick={() => setFilter("ALL")}>
          Tous <span className="n">{result.report.length}</span>
        </button>
        {STATUSES.filter((s) => counts[s]).map((s) => (
          <button key={s} className={`statpill ${filter === s ? "on" : ""}`} onClick={() => setFilter(s)}>
            {s} <span className="n">{counts[s]}</span>
          </button>
        ))}
      </div>

      {shape === "pivot" ? (
        <>
          <div className="tablewrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>id</th>
                  {pivot.cols.map((c) => <th key={c}>{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {pivot.idOrder.length === 0 ? (
                  <tr><td colSpan={pivot.cols.length + 1} style={{ textAlign: "center", color: "var(--ink-faint)", padding: 24 }}>
                    Rien ici — aucune ligne ne correspond à ce filtre.
                  </td></tr>
                ) : pivot.idOrder.slice(0, 2000).map((id, i) => {
                  const cell = pivot.byId.get(String(id)) ?? {};
                  return (
                    <tr key={i}>
                      <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{String(id)}</td>
                      {pivot.cols.map((c) => {
                        const vals = cell[c];
                        if (!vals) return <td key={c} />;
                        const text = vals.join(" ; ");
                        return (
                          <td key={c}>
                            {pivotValue === "status" && vals.length === 1
                              ? <span className={`statustag ${vals[0]}`}>{text}</span>
                              : text}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {pivot.idOrder.length > 2000 && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-faint)" }}>
              Affichage des 2 000 premiers id sur {pivot.idOrder.length.toLocaleString()} correspondants — téléchargez pour l'ensemble complet.
            </div>
          )}
        </>
      ) : (
        <>
          <div className="tablewrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>id</th><th>colonne</th>
                  {hasOriginal && <th>origine</th>}
                  <th>{hasOriginal ? "finale" : "valeur"}</th>
                  <th>résultat</th><th>statut</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr><td colSpan={hasOriginal ? 6 : 5} style={{ textAlign: "center", color: "var(--ink-faint)", padding: 24 }}>
                    Rien ici — aucune ligne ne correspond à ce filtre.
                  </td></tr>
                ) : rows.slice(0, 2000).map((r, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{String(r.id)}</td>
                    <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{r.colonne}</td>
                    {hasOriginal && (
                      <td title={r.valeur_originale}>{r.valeur_originale || <span style={{ color: "var(--ink-faint)" }}>∅</span>}</td>
                    )}
                    <td title={r.valeur_finale}>{r.valeur_finale || <span style={{ color: "var(--ink-faint)" }}>∅</span>}</td>
                    <td style={{ color: "var(--ink-soft)", fontSize: 12 }}>{r.resultat}</td>
                    <td><span className={`statustag ${r.statut}`}>{r.statut}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 2000 && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-faint)" }}>
              Affichage des 2 000 premières lignes sur {rows.length.toLocaleString()} correspondantes — téléchargez pour l'ensemble complet.
            </div>
          )}
        </>
      )}
      <div className="csub" style={{ marginTop: 8 }}>
        L'aperçu ci-dessus affiche le filtre actif. <strong>Télécharger</strong> applique le même filtre de statut et la forme choisie à l'ensemble du rapport.
      </div>
    </div>
  );
}
