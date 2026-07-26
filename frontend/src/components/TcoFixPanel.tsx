import { useState } from "react";
import { api } from "../lib/api";
import type { ProcessResponse, TcoSuggestRow } from "../lib/types";
import { IconCheck, IconPlay, IconSave, IconWarn } from "../lib/icons";

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

  if (!result) return <p className="tf-hint">Run the check first.</p>;
  if (count === 0)
    return (
      <p className="tf-ok"><IconCheck size={14} /> Every value is covered by the
        correspondence table.</p>
    );

  return (
    <div className="tf">
      <p className="tf-warn">
        <IconWarn size={14} /> {count} value(s) are not in the correspondence table.
        These are <strong>mapping</strong> errors, not data errors: the file is
        probably fine, the table is incomplete.
      </p>

      <button className="btn" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          const r = await api.suggestTco(uncovered as never, fieldTypes);
          setRows(r.rows);
        } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
        finally { setBusy(false); }
      }}><IconPlay size={14} /> Propose the missing entries</button>

      {rows.length > 0 && (
        <>
          <table className="tf-table">
            <thead><tr><th>Field</th><th>Type</th><th>Value found</th>
                       <th>Occurrences</th><th>Target label</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{r.column}</td>
                  <td><code>{r.TYPE}</code></td>
                  <td><strong>{r.SOURCE_VALUE}</strong></td>
                  <td>{r.count}</td>
                  <td>
                    <input value={r.TARGET_LABEL} disabled={!editable}
                           placeholder="what it should become"
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
                        notify(`${res.added} correspondence(s) added — version ${res.version_no}. `
                               + `Run the check again.`, "ok");
                        setRows([]);
                      } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                      finally { setBusy(false); }
                    }}>
              <IconSave size={14} /> Add to the table
            </button>
          ) : (
            <p className="tf-hint">
              This environment does not allow editing the correspondence table —
              pass these values on to whoever maintains it.
            </p>
          )}
        </>
      )}
    </div>
  );
}
