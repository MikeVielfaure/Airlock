import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
import { IconCode, IconList, IconPlay, IconReset, IconTable } from "../lib/icons";

interface Props {
  onStart: (body: { columns?: string[]; artefact_id?: string; rows?: number }, label: string) => void;
}

/**
 * Start without a file: the schema is the source of truth, the data comes later
 * (typed by hand in Data) or never. Two paths — columns by hand, or seeded from
 * a library config / EDI model so a config can be tried on hand-typed rows.
 */
export function BlankStart({ onStart }: Props) {
  const [mode, setMode] = useState<"scratch" | "artefact">("scratch");
  const [cols, setCols] = useState<string[]>(["", "", ""]);
  const [rows, setRows] = useState(0);

  const [configs, setConfigs] = useState<ArtefactInfo[]>([]);
  const [ediModels, setEdiModels] = useState<ArtefactInfo[]>([]);
  const [pick, setPick] = useState("");

  useEffect(() => {
    Promise.all([api.listArtefacts("config"), api.listArtefacts("edi_model")])
      .then(([c, e]) => { setConfigs(c); setEdiModels(e); })
      .catch(() => { /* the empty state is fine */ });
  }, []);

  const setCol = (i: number, v: string) =>
    setCols((cs) => cs.map((c, j) => (j === i ? v : c)));
  const addCol = () => setCols((cs) => [...cs, ""]);
  const rmCol = (i: number) => setCols((cs) => cs.filter((_, j) => j !== i));

  const clean = cols.map((c) => c.trim()).filter(Boolean);
  const dupes = clean.filter((c, i) => clean.indexOf(c) !== i);
  const scratchOk = clean.length > 0 && dupes.length === 0;

  const all = [...configs.map((a) => ({ ...a, k: "config" as const })),
               ...ediModels.map((a) => ({ ...a, k: "edi_model" as const }))];
  const picked = all.find((a) => a.id === pick);

  return (
    <div className="blank">
      <div className="blank-head">
        <h2>Démarrer à partir d'un schéma</h2>
        <p>Aucun fichier nécessaire : définissez les colonnes, puis saisissez les lignes
          à la main dans Données — pour essayer une config, construire une petite référence,
          ou amorcer une table vide.</p>
      </div>

      <div className="blank-modes">
        <button className={`blank-mode ${mode === "scratch" ? "on" : ""}`}
                onClick={() => setMode("scratch")}>
          <IconList size={16} /> Colonnes à la main
        </button>
        <button className={`blank-mode ${mode === "artefact" ? "on" : ""}`}
                onClick={() => setMode("artefact")}>
          <IconTable size={16} /> Depuis une config enregistrée
        </button>
      </div>

      {mode === "scratch" ? (
        <div className="blank-body">
          <label className="blank-label">Colonnes</label>
          <div className="blank-cols">
            {cols.map((c, i) => (
              <div key={i} className="blank-col">
                <input value={c} placeholder={`colonne ${i + 1}`} autoFocus={i === 0}
                       onChange={(e) => setCol(i, e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter" && scratchOk)
                         onStart({ columns: clean, rows }, "manual"); }} />
                {cols.length > 1 && (
                  <button className="blank-rm" title="Retirer" onClick={() => rmCol(i)}>
                    <IconReset size={13} />
                  </button>
                )}
              </div>
            ))}
          </div>
          <button className="btn sm" onClick={addCol}><IconCode size={13} /> Ajouter une colonne</button>

          {dupes.length > 0 && (
            <p className="blank-warn">Nom de colonne en double : {[...new Set(dupes)].join(", ")}</p>
          )}

          <label className="blank-label">Démarrer avec</label>
          <div className="blank-rows">
            {[0, 1, 3, 5].map((n) => (
              <button key={n} className={`blank-chip ${rows === n ? "on" : ""}`}
                      onClick={() => setRows(n)}>
                {n === 0 ? "aucune ligne" : `${n} ligne${n > 1 ? "s" : ""} vide${n > 1 ? "s" : ""}`}
              </button>
            ))}
          </div>

          <button className="btn primary blank-go" disabled={!scratchOk}
                  onClick={() => onStart({ columns: clean, rows }, "manual")}>
            <IconPlay size={15} /> Créer la session
          </button>
        </div>
      ) : (
        <div className="blank-body">
          {all.length === 0 ? (
            <p className="blank-empty">Aucune config ou modèle EDI enregistré pour le moment.
              Enregistrez-en un d'abord depuis la vue Schéma ou EDI, puis amorcez une session à
              partir d'ici.</p>
          ) : (
            <>
              <label className="blank-label">Amorcer les colonnes et règles depuis</label>
              <select value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">— choisir une config ou un modèle EDI —</option>
                {configs.length > 0 && (
                  <optgroup label="Configs">
                    {configs.map((a) => (
                      <option key={a.id} value={a.id}>{a.name} (v{a.latest_version_no})</option>
                    ))}
                  </optgroup>
                )}
                {ediModels.length > 0 && (
                  <optgroup label="Modèles EDI">
                    {ediModels.map((a) => (
                      <option key={a.id} value={a.id}>{a.name} (v{a.latest_version_no})</option>
                    ))}
                  </optgroup>
                )}
              </select>

              {picked && (
                <p className="blank-note">
                  {picked.k === "config"
                    ? "Les règles de colonnes sont incluses — la session est prête à valider immédiatement."
                    : "Le modèle EDI apporte ses noms de colonnes à plat ; les règles de validation restent à ajouter."}
                </p>
              )}

              <label className="blank-label">Démarrer avec</label>
              <div className="blank-rows">
                {[0, 1, 3, 5].map((n) => (
                  <button key={n} className={`blank-chip ${rows === n ? "on" : ""}`}
                          onClick={() => setRows(n)}>
                    {n === 0 ? "aucune ligne" : `${n} ligne${n > 1 ? "s" : ""} vide${n > 1 ? "s" : ""}`}
                  </button>
                ))}
              </div>

              <button className="btn primary blank-go" disabled={!pick}
                      onClick={() => onStart({ artefact_id: pick, rows },
                                             picked?.name ?? "seeded")}>
                <IconPlay size={15} /> Démarrer depuis ce schéma
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
