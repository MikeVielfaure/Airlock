import { useState, useRef } from "react";
import type { Presets } from "../lib/types";
import { IconUpload, IconTable, IconGrid, IconEdit } from "../lib/icons";

interface Props {
  presets: Presets | null;
  fileType: string;
  setFileType: (t: string) => void;
  encoding: string;
  setEncoding: (e: string) => void;
  delimiterKey: string;
  setDelimiterKey: (d: string) => void;
  onUpload: (f: File) => void;
  onStartBlank: (body: { columns?: string[]; rows?: number }, label: string) => void;
  onGoTab: (tab: string) => void;
  canChooseConfig?: boolean;
}

type Mode = "file" | "dataset" | "blank" | "edi";

export function SourceSelector({
  presets,
  fileType,
  setFileType,
  encoding,
  setEncoding,
  delimiterKey,
  setDelimiterKey,
  onUpload,
  onStartBlank,
  onGoTab,
}: Props) {
  const [activeMode, setActiveMode] = useState<Mode>("file");
  const [drag, setDrag] = useState(false);
  const [customCols, setCustomCols] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const accept = fileType === "CSV" ? ".csv" : ".xlsx,.xls";

  const handleCustomBlank = () => {
    const cols = customCols
      .split(/[,;\n]/)
      .map((c) => c.trim())
      .filter(Boolean);
    if (cols.length === 0) return;
    onStartBlank({ columns: cols, rows: 5 }, "Session manuelle");
  };

  return (
    <div className="source-selector-container">
      <div className="source-header">
        <h2>Charger ou créer des données</h2>
        <p className="source-subtitle">
          Sélectionnez le mode d'entrée de vos données pour démarrer la session d'analyse et de contrôle.
        </p>
      </div>

      {/* Choice Tabs */}
      <div className="source-modes">
        <button
          className={`source-mode-btn ${activeMode === "file" ? "active" : ""}`}
          onClick={() => setActiveMode("file")}
        >
          <span className="mode-icon"><IconUpload size={22} /></span>
          <div className="mode-text">
            <strong>Fichier local</strong>
            <span>CSV ou Excel (.xlsx)</span>
          </div>
        </button>

        <button
          className={`source-mode-btn ${activeMode === "dataset" ? "active" : ""}`}
          onClick={() => {
            setActiveMode("dataset");
            onGoTab("datasets");
          }}
        >
          <span className="mode-icon"><IconTable size={22} /></span>
          <div className="mode-text">
            <strong>Table enregistrée</strong>
            <span>Consulter les datasets BDD</span>
          </div>
        </button>

        <button
          className={`source-mode-btn ${activeMode === "blank" ? "active" : ""}`}
          onClick={() => setActiveMode("blank")}
        >
          <span className="mode-icon"><IconEdit size={22} /></span>
          <div className="mode-text">
            <strong>Saisie manuelle / Vierge</strong>
            <span>Définir des colonnes à zéro</span>
          </div>
        </button>

        <button
          className={`source-mode-btn ${activeMode === "edi" ? "active" : ""}`}
          onClick={() => {
            setActiveMode("edi");
            onGoTab("edi");
          }}
        >
          <span className="mode-icon"><IconGrid size={22} /></span>
          <div className="mode-text">
            <strong>Message EDI</strong>
            <span>Décoder du format EDIFACT</span>
          </div>
        </button>
      </div>

      {/* Mode Content */}
      <div className="source-content">
        {activeMode === "file" && (
          <div className="file-upload-card">
            <div className="file-settings-bar">
              <div className="frow-inline">
                <label>Format :</label>
                <select value={fileType} onChange={(e) => setFileType(e.target.value)}>
                  <option value="CSV">CSV</option>
                  <option value="XLSX">Excel (XLSX)</option>
                </select>
              </div>

              {fileType === "CSV" && (
                <>
                  <div className="frow-inline">
                    <label>Encodage :</label>
                    <select value={encoding} onChange={(e) => setEncoding(e.target.value)}>
                      {(presets?.encodings ?? ["AUTO"]).map((e) => (
                        <option key={e} value={e}>{e}</option>
                      ))}
                    </select>
                  </div>

                  <div className="frow-inline">
                    <label>Délimiteur :</label>
                    <select value={delimiterKey} onChange={(e) => setDelimiterKey(e.target.value)}>
                      {Object.keys(presets?.delimiters ?? { "point-virgule (;)": ";" }).map((k) => (
                        <option key={k} value={k}>{k}</option>
                      ))}
                    </select>
                  </div>
                </>
              )}
            </div>

            <div
              className={`dropzone-large ${drag ? "drag" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                const f = e.dataTransfer.files?.[0];
                if (f) onUpload(f);
              }}
              onClick={() => fileRef.current?.click()}
            >
              <div className="drop-icon-wrapper">
                <IconUpload size={36} />
              </div>
              <div className="drop-text">
                <h3>Déposer votre fichier {fileType} ici</h3>
                <p>Glissez-déposez le fichier ou <span className="highlight-browse">parcourez vos dossiers</span></p>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept={accept}
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onUpload(f);
                  e.target.value = "";
                }}
              />
            </div>
          </div>
        )}

        {activeMode === "blank" && (
          <div className="blank-creation-card">
            <h3>Démarrer avec une structure vide</h3>
            <p>Saisissez les noms de vos colonnes séparés par des virgules pour créer une table vierge :</p>
            <div className="blank-input-row">
              <input
                type="text"
                className="input-text"
                placeholder="Exemple: code_client, nom, email, montant"
                value={customCols}
                onChange={(e) => setCustomCols(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleCustomBlank(); }}
              />
              <button className="btn primary" onClick={handleCustomBlank} disabled={!customCols.trim()}>
                Créer la session
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
