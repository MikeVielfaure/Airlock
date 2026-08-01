import { useEffect, useState, useRef } from "react";
import type { AvailableVariable, Presets, VariableSchema } from "../lib/types";
import { api } from "../lib/api";
import { IconUpload, IconTable, IconGrid, IconEdit, IconCode } from "../lib/icons";

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
  onStartExternalDb: (connection: string, query: string, params: Record<string, string>,
                      schemaName: string | undefined, label: string) => void;
  onStartApi: (connection: string, path: string, method: string, responseKind: string,
              dataPath: string, schemaName: string | undefined, label: string) => void;
  onGoTab: (tab: string) => void;
  canChooseConfig?: boolean;
}

type Mode = "file" | "dataset" | "blank" | "edi" | "sql" | "api";

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
  onStartExternalDb,
  onStartApi,
  onGoTab,
}: Props) {
  const [activeMode, setActiveMode] = useState<Mode>("file");
  const [drag, setDrag] = useState(false);
  const [customCols, setCustomCols] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // ── Source BDD externe / Source API — reuse the référentiel's saved
  // connections and their declared schemas, exactly like the Sources tab
  // of Calculs does for attaching a joined source.
  const [connections, setConnections] = useState<AvailableVariable[]>([]);
  useEffect(() => { api.listAvailableVariables().then(setConnections).catch(() => {}); }, []);
  const dbConnections = connections.filter((v) => v.kind === "external_db");
  const apiConnections = connections.filter((v) => v.kind === "api");

  const [dbConn, setDbConn] = useState("");
  const [dbSchemas, setDbSchemas] = useState<VariableSchema[]>([]);
  const [dbSchemaName, setDbSchemaName] = useState("");
  const [dbQuery, setDbQuery] = useState("");
  const [dbParams, setDbParams] = useState<{ key: string; value: string }[]>([]);

  const pickDbConnection = async (name: string) => {
    setDbConn(name); setDbSchemaName(""); setDbSchemas([]);
    const v = dbConnections.find((c) => c.name === name);
    if (!v) return;
    try { setDbSchemas(await api.listConnectionSchemas(v.id)); } catch { setDbSchemas([]); }
  };
  const pickDbSchema = (name: string) => {
    setDbSchemaName(name);
    const sc = dbSchemas.find((s) => s.name === name);
    if (sc) setDbQuery(`SELECT ${sc.columns.map((c) => c.name).join(", ") || "*"} FROM ${name}`);
  };
  const startFromDb = () => {
    if (!dbConn || !dbQuery.trim()) return;
    const params = Object.fromEntries(dbParams.filter((p) => p.key.trim()).map((p) => [p.key.trim(), p.value]));
    onStartExternalDb(dbConn, dbQuery.trim(), params, dbSchemaName || undefined, `sql-${dbConn}`);
  };

  const [apiConn, setApiConn] = useState("");
  const [apiSchemas, setApiSchemas] = useState<VariableSchema[]>([]);
  const [apiSchemaName, setApiSchemaName] = useState("");
  const [apiPath, setApiPath] = useState("");
  const [apiMethod, setApiMethod] = useState("GET");
  const [apiResponseKind, setApiResponseKind] = useState<"json" | "csv" | "xlsx">("json");
  const [apiDataPath, setApiDataPath] = useState("");

  const pickApiConnection = async (name: string) => {
    setApiConn(name); setApiSchemaName(""); setApiSchemas([]);
    const v = apiConnections.find((c) => c.name === name);
    if (!v) return;
    try { setApiSchemas(await api.listConnectionSchemas(v.id)); } catch { setApiSchemas([]); }
  };
  const pickApiSchema = (name: string) => {
    setApiSchemaName(name);
    const sc = apiSchemas.find((s) => s.name === name);
    if (sc) { setApiPath(sc.path ?? ""); setApiMethod(sc.method ?? "GET"); setApiDataPath(sc.data_path ?? ""); }
  };
  const startFromApi = () => {
    if (!apiConn) return;
    onStartApi(apiConn, apiPath, apiMethod, apiResponseKind, apiDataPath,
              apiSchemaName || undefined, `api-${apiConn}`);
  };

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

        <button
          className={`source-mode-btn ${activeMode === "sql" ? "active" : ""}`}
          onClick={() => setActiveMode("sql")}
        >
          <span className="mode-icon"><IconCode size={22} /></span>
          <div className="mode-text">
            <strong>Source SQL externe</strong>
            <span>Requête sur une connexion du référentiel</span>
          </div>
        </button>

        <button
          className={`source-mode-btn ${activeMode === "api" ? "active" : ""}`}
          onClick={() => setActiveMode("api")}
        >
          <span className="mode-icon"><IconCode size={22} /></span>
          <div className="mode-text">
            <strong>Source API</strong>
            <span>Appel sur une connexion du référentiel</span>
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

        {activeMode === "sql" && (
          <div className="blank-creation-card">
            <h3>Démarrer depuis une source SQL externe</h3>
            {dbConnections.length === 0 ? (
              <p>Aucune connexion « Base de données externe » n'est déclarée dans le référentiel. Ajoutez-en une dans Administration &gt; Variables.</p>
            ) : (
              <>
                <p>Choisissez une connexion, puis éventuellement une table connue pour pré-remplir la requête.</p>
                <div className="frow-inline">
                  <label>Connexion :</label>
                  <select value={dbConn} onChange={(e) => pickDbConnection(e.target.value)}>
                    <option value="">— choisir —</option>
                    {dbConnections.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
                  </select>
                </div>
                {dbConn && (
                  <>
                    <div className="frow-inline">
                      <label>Table connue :</label>
                      <select value={dbSchemaName} onChange={(e) => pickDbSchema(e.target.value)}>
                        <option value="">— requête libre —</option>
                        {dbSchemas.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                      </select>
                    </div>
                    {dbSchemaName && (
                      <div className="chipbar">
                        <span className="chiplabel">insérer une colonne :</span>
                        {(dbSchemas.find((s) => s.name === dbSchemaName)?.columns ?? []).map((c) => (
                          <button key={c.name} type="button" className="microchip"
                            onClick={() => setDbQuery((q) => q + c.name)}>{c.name}</button>
                        ))}
                      </div>
                    )}
                    <textarea className="mono-input" rows={3} placeholder="SELECT * FROM ma_table"
                      value={dbQuery} onChange={(e) => setDbQuery(e.target.value)} />
                    <p className="ops-hint">Paramètres liés (jamais de substitution textuelle) — utilisables dans la requête via <code>:nom</code>.</p>
                    {dbParams.map((p, i) => (
                      <div className="frow-inline" key={i}>
                        <input className="input-text" placeholder="nom" value={p.key}
                          onChange={(e) => setDbParams(dbParams.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} />
                        <input className="input-text" placeholder="valeur" value={p.value}
                          onChange={(e) => setDbParams(dbParams.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />
                        <button className="btn sm" onClick={() => setDbParams(dbParams.filter((_, j) => j !== i))}>✕</button>
                      </div>
                    ))}
                    <button className="btn sm" onClick={() => setDbParams([...dbParams, { key: "", value: "" }])}>+ paramètre</button>
                    <div className="blank-input-row">
                      <button className="btn primary" onClick={startFromDb} disabled={!dbQuery.trim()}>
                        Charger
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        )}

        {activeMode === "api" && (
          <div className="blank-creation-card">
            <h3>Démarrer depuis une source API</h3>
            {apiConnections.length === 0 ? (
              <p>Aucune connexion « API » n'est déclarée dans le référentiel. Ajoutez-en une dans Administration &gt; Variables.</p>
            ) : (
              <>
                <p>Choisissez une connexion, puis éventuellement un endpoint connu pour pré-remplir le chemin.</p>
                <div className="frow-inline">
                  <label>Connexion :</label>
                  <select value={apiConn} onChange={(e) => pickApiConnection(e.target.value)}>
                    <option value="">— choisir —</option>
                    {apiConnections.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
                  </select>
                </div>
                {apiConn && (
                  <>
                    <div className="frow-inline">
                      <label>Endpoint connu :</label>
                      <select value={apiSchemaName} onChange={(e) => pickApiSchema(e.target.value)}>
                        <option value="">— chemin libre —</option>
                        {apiSchemas.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                      </select>
                    </div>
                    <div className="frow-inline">
                      <label>Méthode :</label>
                      <select value={apiMethod} onChange={(e) => setApiMethod(e.target.value)}>
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                      </select>
                      <label>Réponse :</label>
                      <select value={apiResponseKind} onChange={(e) => setApiResponseKind(e.target.value as "json" | "csv" | "xlsx")}>
                        <option value="json">JSON</option>
                        <option value="csv">CSV</option>
                        <option value="xlsx">Excel (XLSX)</option>
                      </select>
                    </div>
                    <input className="input-text" placeholder="/chemin/de/l'endpoint"
                      value={apiPath} onChange={(e) => setApiPath(e.target.value)} />
                    {apiResponseKind === "json" && (
                      <input className="input-text" placeholder="Chemin des données dans la réponse JSON (optionnel), ex: data.items"
                        value={apiDataPath} onChange={(e) => setApiDataPath(e.target.value)} />
                    )}
                    <div className="blank-input-row">
                      <button className="btn primary" onClick={startFromApi}>
                        Charger
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
