import { useMemo, useState } from "react";
import type { CellStatus, ProcessResponse } from "../lib/types";
import { IconDownload } from "../lib/icons";

const STATUSES: CellStatus[] = ["ERROR", "CLEANED", "MAPPING_KO", "MAPPING_OK", "NO_TCO", "OK"];
type Shape = "long" | "by_id" | "pivot";

export function ReportPanel({ result, sid }: { result: ProcessResponse | null; sid: string | null }) {
  const [filter, setFilter] = useState<CellStatus | "ALL" | "ISSUES">("ISSUES");
  const [shape, setShape] = useState<Shape>("long");
  const [fmt, setFmt] = useState<"csv" | "xlsx">("csv");

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    result?.report.forEach((r) => { c[r.statut] = (c[r.statut] ?? 0) + 1; });
    return c;
  }, [result]);

  if (!result) {
    return <div className="banner"><span>Run validation first — the report lists every cell and what happened to it.</span></div>;
  }

  // statuses implied by the active pill, used both for the preview and the download
  const statusesFor = (): string => {
    if (filter === "ISSUES") return "ERROR,MAPPING_KO,NO_TCO";
    if (filter === "ALL") return STATUSES.join(",");
    return filter;
  };

  const rows = result.report.filter((r) => {
    if (filter === "ALL") return true;
    if (filter === "ISSUES") return r.statut === "ERROR" || r.statut === "MAPPING_KO" || r.statut === "NO_TCO";
    return r.statut === filter;
  });

  const download = () => {
    if (!sid) return;
    const qs = new URLSearchParams({ shape, statuses: statusesFor(), fmt, download: "1" });
    const a = document.createElement("a");
    a.href = `/api/files/${sid}/report?${qs.toString()}`;
    a.click();
  };

  const uncovered = result.tco_uncovered ?? {};
  const uncoveredCols = Object.keys(uncovered).filter((c) => uncovered[c]?.length);

  return (
    <div>
      <div className="sec-h">
        <h3>Report</h3>
        <span className="sub">One line per checked cell.</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <label className="csub">Shape
            <select className="mono-input" style={{ marginLeft: 4 }} value={shape} onChange={(e) => setShape(e.target.value as Shape)}>
              <option value="long">flat (one row per cell)</option>
              <option value="by_id">grouped by id</option>
              <option value="pivot">pivot (id × column)</option>
            </select>
          </label>
          <select className="mono-input" value={fmt} onChange={(e) => setFmt(e.target.value as "csv" | "xlsx")}>
            <option value="csv">CSV</option>
            <option value="xlsx">Excel</option>
          </select>
          <button className="btn sm" onClick={download}><IconDownload size={14} /> Download</button>
        </span>
      </div>

      {uncoveredCols.length > 0 && (
        <div className="tco-coverage">
          <div className="tco-coverage-h">TCO — values not covered (add these to your correspondence table)</div>
          {uncoveredCols.map((col) => (
            <div key={col} className="tco-col">
              <span className="tco-colname">{col}</span>
              <div className="tco-vals">
                {uncovered[col].slice(0, 40).map((v) => (
                  <span key={v.value} className="tco-val" title={`${v.count} row(s)`}>
                    {v.value || "∅"} <span className="tco-count">{v.count}</span>
                  </span>
                ))}
                {uncovered[col].length > 40 && <span className="csub">+{uncovered[col].length - 40} more</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="filterbar">
        <button className={`statpill ${filter === "ISSUES" ? "on" : ""}`} onClick={() => setFilter("ISSUES")}>
          Issues <span className="n">{(counts.ERROR ?? 0) + (counts.MAPPING_KO ?? 0) + (counts.NO_TCO ?? 0)}</span>
        </button>
        <button className={`statpill ${filter === "ALL" ? "on" : ""}`} onClick={() => setFilter("ALL")}>
          All <span className="n">{result.report.length}</span>
        </button>
        {STATUSES.filter((s) => counts[s]).map((s) => (
          <button key={s} className={`statpill ${filter === s ? "on" : ""}`} onClick={() => setFilter(s)}>
            {s} <span className="n">{counts[s]}</span>
          </button>
        ))}
      </div>

      <div className="tablewrap">
        <table className="grid">
          <thead>
            <tr>
              <th>id</th><th>column</th><th>original</th><th>final</th><th>result</th><th>status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--ink-faint)", padding: 24 }}>
                Nothing here — no rows match this filter.
              </td></tr>
            ) : rows.slice(0, 2000).map((r, i) => (
              <tr key={i}>
                <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{String(r.id)}</td>
                <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{r.colonne}</td>
                <td title={r.valeur_originale}>{r.valeur_originale || <span style={{ color: "var(--ink-faint)" }}>∅</span>}</td>
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
          Showing first 2,000 of {rows.length.toLocaleString()} matching rows — download for the full set.
        </div>
      )}
      <div className="csub" style={{ marginTop: 8 }}>
        The preview above shows the active filter. <strong>Download</strong> applies the same status filter and your chosen shape over the whole report.
      </div>
    </div>
  );
}
