import { useState } from "react";
import { api } from "../lib/api";
import type { ProcessResponse, TcoSuggestRow } from "../lib/types";
import { IconCheck, IconPlay, IconSave, IconWarn } from "../lib/icons";
import { InfoTip } from "./InfoTip";

interface Props {
  result: ProcessResponse | null;
  fieldTypes: Record<string, string>;
  tcoArtefactId: string;
  editable: boolean;
  notify: (m: string, k?: "ok" | "err" | "info") => void;
}

/**
 * The mapping-error path, end to end.
 *
 * A validation failure is not always a data problem: when a value simply is not
 * in the correspondence table, the fix is to extend the table, not to correct
 * the file. This panel turns those unmapped values into rows to complete — with
 * the type already filled from the field's configuration, since it is declared
 * there and asking twice is how two sources of truth start disagreeing.
 */
export function TcoFixPanel({ result, fieldTypes, tcoArtefactId, editable, notify }: Props) {
  const [rows, setRows] = useState<TcoSuggestRow[]>([]);
  const [busy, setBusy] = useState(false);

  const uncovered = result?.tco_uncovered ?? {};
  const count = Object.values(uncovered).reduce((n, v) => n + v.length, 0);

  const info = (
    <InfoTip>
      <p><b>À quoi ça sert</b> — corriger une erreur de <em>correspondance</em> (une valeur absente de la table TCO), sans toucher au fichier lui-même.</p>
      <p><b>Comment faire</b> — « Proposer les entrées manquantes » liste les valeurs non couvertes ; complétez le libellé cible pour chacune, « Ajouter à la table », puis relancez le contrôle.</p>
      <p><b>Ce qu'il faut</b> — une validation déjà lancée dans Données, et une table de référence (TCO) chargée.</p>
    </InfoTip>
  );

  if (!result) return <p className="tf-hint">Lancez d'abord le contrôle. {info}</p>;
  if (count === 0)
    return (
      <p className="tf-ok"><IconCheck size={14} /> Toutes les valeurs sont couvertes
        par la table de correspondance. {info}</p>
    );

  return (
    <div className="tf">
      <p className="tf-warn">
        <IconWarn size={14} /> {count} valeur(s) absente(s) de la table de correspondance.
        Ce sont des erreurs de <strong>correspondance</strong>, pas des erreurs de
        données : le fichier est probablement correct, c'est la table qui est incomplète.
        {info}
      </p>

      <button className="btn" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          const r = await api.suggestTco(uncovered as never, fieldTypes);
          setRows(r.rows);
        } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
        finally { setBusy(false); }
      }}><IconPlay size={14} /> Proposer les entrées manquantes</button>

      {rows.length > 0 && (
        <>
          <table className="tf-table">
            <thead><tr><th>Champ</th><th>Type</th><th>Valeur trouvée</th>
                       <th>Occurrences</th><th>Libellé cible</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{r.column}</td>
                  <td><code>{r.TYPE}</code></td>
                  <td><strong>{r.SOURCE_VALUE}</strong></td>
                  <td>{r.count}</td>
                  <td>
                    <input value={r.TARGET_LABEL} disabled={!editable}
                           placeholder="ce que ça devrait devenir"
                           onChange={(e) => setRows((rs) => rs.map((x, j) =>
                             j === i ? { ...x, TARGET_LABEL: e.target.value } : x))} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {editable ? (
            <button className="btn" disabled={busy || rows.every((r) => !r.TARGET_LABEL.trim())}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        const filled = rows.filter((r) => r.TARGET_LABEL.trim())
                          .map((r) => ({ TYPE: r.TYPE, SOURCE_VALUE: r.SOURCE_VALUE,
                                         TARGET_LABEL: r.TARGET_LABEL }));
                        const res = await api.appendTco({ artefact_id: tcoArtefactId,
                                                          name: "correspondances",
                                                          rows: filled });
                        notify(`${res.added} correspondance(s) ajoutée(s) — version ${res.version_no}. `
                               + `Relancez le contrôle.`, "ok");
                        setRows([]);
                      } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                      finally { setBusy(false); }
                    }}>
              <IconSave size={14} /> Ajouter à la table
            </button>
          ) : (
            <p className="tf-hint">
              Cet environnement ne permet pas de modifier la table de correspondance —
              transmettez ces valeurs à la personne qui la maintient.
            </p>
          )}
        </>
      )}
    </div>
  );
}
