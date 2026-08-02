import { useEffect, useRef, useState } from "react";
import type { ArtefactInfo, Presets, TcoResponse } from "../lib/types";
import { api } from "../lib/api";
import { IconUpload, IconLayers, IconReset } from "../lib/icons";

interface Props {
  presets: Presets | null;
  /** False when the environment imposes a configuration this person may not
      change: the whole "reuse a config" step is then not theirs to take. */
  canChooseConfig?: boolean;
  fileType: string; setFileType: (t: string) => void;
  encoding: string; setEncoding: (e: string) => void;
  delimiterKey: string; setDelimiterKey: (d: string) => void;
  onUpload: (f: File) => void;
  loadedName: string | null;
  sid: string | null;
  onTco: (f: File) => void;
  onTcoFromArtefact: (artefactId: string) => void;
  tco: TcoResponse | null;
  onImportYaml: (text: string) => void;
  sheets: string[];
  sheet: string | null;
  onSheetChange: (name: string) => void;
  tableMarker: string;
  tableIndex: number;
  tableHeaderMode: "local" | "global";
  tableCount: number;
  onTableChange: (over: { marker?: string; index?: number; headerMode?: "local" | "global" }) => void;
  isExcel: boolean;
  hasFile: boolean;
  onReset: () => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export function Sidebar(p: Props) {
  const [drag, setDrag] = useState(false);
  const [markerInput, setMarkerInput] = useState(p.tableMarker);
  useEffect(() => { setMarkerInput(p.tableMarker); }, [p.tableMarker]);
  const fileRef = useRef<HTMLInputElement>(null);
  const tcoRef = useRef<HTMLInputElement>(null);
  const yamlRef = useRef<HTMLInputElement>(null);

  const [tcoLib, setTcoLib] = useState<ArtefactInfo[]>([]);
  const [tcoLibPick, setTcoLibPick] = useState("");
  useEffect(() => { api.listArtefacts("tco").then(setTcoLib).catch(() => {}); }, []);

  const accept = p.fileType === "CSV" ? ".csv" : ".xlsx,.xls";

  if (p.collapsed) {
    return (
      <aside className="sidebar collapsed">
        <button
          className="sidebar-toggle-btn"
          title="Déplier le panneau latéral"
          onClick={p.onToggleCollapse}
        >
          ▶
        </button>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header-bar">
        <span className="side-title">Options & Source</span>
        {p.onToggleCollapse && (
          <button
            className="sidebar-toggle-btn"
            title="Masquer le panneau latéral"
            onClick={p.onToggleCollapse}
          >
            ◀
          </button>
        )}
      </div>

      {/* SOURCE */}
      <div className="side-block">
        <h2 className="side-head"><span className="num">01</span> Source de données</h2>

        <div className="fgrid">
          <div className="frow">
            <label>Format</label>
            <select value={p.fileType} onChange={(e) => p.setFileType(e.target.value)}>
              <option value="CSV">CSV</option>
              <option value="XLSX">XLSX</option>
            </select>
          </div>
          <div className="frow">
            <label>Encodage</label>
            <select value={p.encoding} onChange={(e) => p.setEncoding(e.target.value)} disabled={p.fileType !== "CSV"}>
              {(p.presets?.encodings ?? ["AUTO"]).map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
          </div>
        </div>

        {p.fileType === "CSV" && (
          <div className="frow">
            <label>Délimiteur</label>
            <select value={p.delimiterKey} onChange={(e) => p.setDelimiterKey(e.target.value)}>
              {Object.keys(p.presets?.delimiters ?? { AUTO: null }).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
        )}

        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault(); setDrag(false);
            const f = e.dataTransfer.files?.[0]; if (f) p.onUpload(f);
          }}
          onClick={() => fileRef.current?.click()}
          style={{ marginTop: 8 }}
        >
          <IconUpload size={20} />
          <div style={{ marginTop: 4 }}>
            {p.loadedName
              ? <><strong>{p.loadedName}</strong><div className="small">Remplacer le fichier</div></>
              : <><strong>Charger un fichier {p.fileType}</strong><div className="small">ou <span className="pick">parcourir</span></div></>}
          </div>
          <input ref={fileRef} type="file" accept={accept} hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) p.onUpload(f); e.target.value = ""; }} />
        </div>

        {p.sheets.length > 1 && (
          <div className="frow" style={{ marginTop: 10 }}>
            <label>Feuille <span style={{ color: "var(--ink-faint)" }}>({p.sheets.length} trouvées)</span></label>
            <select value={p.sheet ?? ""} onChange={(e) => p.onSheetChange(e.target.value)}>
              {p.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        )}

        {p.isExcel && (
          <div className="split" style={{ marginTop: 10 }}>
            <label>Découpage en sous-tables <span style={{ color: "var(--ink-faint)" }}>(optionnel)</span></label>
            <input
              type="text" className="mono-input" placeholder="Mot marqueur, ex: TABLEAU"
              value={markerInput}
              onChange={(e) => setMarkerInput(e.target.value)}
              onBlur={() => { if (markerInput !== p.tableMarker) p.onTableChange({ marker: markerInput }); }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
            <div className="csub" style={{ marginTop: 4 }}>Une ligne contenant ce texte démarre une sous-table.</div>
            {p.tableMarker && (
              <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className="csub">{p.tableCount} table{p.tableCount > 1 ? "s" : ""}</span>
                <label className="csub">Garder #
                  <input type="number" min={1} max={Math.max(1, p.tableCount)} className="mono-input" style={{ width: 56, marginLeft: 4 }}
                    value={p.tableIndex + 1}
                    onChange={(e) => p.onTableChange({ index: Math.max(0, (parseInt(e.target.value) || 1) - 1) })} />
                </label>
                <select className="mono-input" value={p.tableHeaderMode}
                  onChange={(e) => p.onTableChange({ headerMode: e.target.value as "local" | "global" })}>
                  <option value="local">En-tête par table</option>
                  <option value="global">Un en-tête pour toutes</option>
                </select>
              </div>
            )}
          </div>
        )}
      </div>

      {/* TCO */}
      <div className="side-block">
        <h2 className="side-head"><span className="num">02</span> Table de référence (TCO)</h2>
        <p className="hint">Fichier CSV de paires <span style={{ fontFamily: "var(--mono)" }}>VALEUR_SOURCE / LIBELLÉ_CIBLE</span>.</p>
        <button className="btn block sm" onClick={() => tcoRef.current?.click()}>
          <IconLayers size={15} /> {p.tco ? "Remplacer TCO" : "Charger TCO"}
        </button>
        <input ref={tcoRef} type="file" accept=".csv" hidden
          onChange={(e) => { const f = e.target.files?.[0]; if (f) p.onTco(f); e.target.value = ""; }} />
        {tcoLib.length > 0 && (
          <div className="frow" style={{ marginTop: 6 }}>
            <select value={tcoLibPick} onChange={(e) => setTcoLibPick(e.target.value)}>
              <option value="">— depuis la bibliothèque —</option>
              {tcoLib.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
            <button className="btn sm" disabled={!tcoLibPick}
                    onClick={() => { p.onTcoFromArtefact(tcoLibPick); }}>
              Charger
            </button>
          </div>
        )}
        {p.tco && (
          <div className="banner ok" style={{ marginTop: 10 }}>
            <span><strong>{p.tco.rows}</strong> lignes · <strong>{p.tco.labels.length}</strong> libellés</span>
          </div>
        )}
      </div>

      {/* YAML IMPORT */}
      {(p.canChooseConfig ?? true) && (
      <div className="side-block">
        <h2 className="side-head"><span className="num">03</span> Importer une configuration</h2>
        <p className="hint">Règles YAML pour pré-remplir le contrôle des colonnes.</p>
        <button className="btn block sm" onClick={() => yamlRef.current?.click()}>
          <IconUpload size={15} /> Importer YAML
        </button>
        <input ref={yamlRef} type="file" accept=".yaml,.yml" hidden
          onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f) p.onImportYaml(await f.text());
            e.target.value = "";
          }} />
      </div>
      )}

      {p.hasFile && (
        <div className="side-block">
          <button className="btn block sm reset" onClick={p.onReset}>
            <IconReset size={15} /> Réinitialiser la session
          </button>
        </div>
      )}
    </aside>
  );
}
