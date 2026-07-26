import { useCallback, useEffect, useState } from "react";
import { api, type DatasetWriteBody } from "../lib/api";
import type {
  DatasetInfo, DatasetWriteLog, TablePreview, WriteProblem, WriteResponse,
} from "../lib/types";
import {
  IconCheck, IconList, IconPlay, IconReset, IconSave, IconTable, IconWarn,
} from "../lib/icons";

interface Props {
  sid: string | null;
  /** Columns of the last run — what would be written. */
  columns: string[];
  /** Columns marked as identifiers in the schema: the natural merge key. */
  identifiers: string[];
  hasRun: boolean;
  sourceName: string;
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  /** Jump back to the Data tab to fix rows by hand. */
  onFixRows?: () => void;
  /** Hand a table over to the Data view as a working session. */
  onOpenSession?: (res: import("../lib/types").FileResponse) => void;
}

const MODE_HELP: Record<string, string> = {
  replace: "Vide la table puis réécrit tout. Rejouer deux fois donne le même résultat.",
  append: "Ajoute à la suite, sans clé. Rejouer deux fois duplique.",
  upsert: "Fusionne sur la clé : met à jour ce qui existe, insère le reste.",
};

const POLICY_HELP: Record<string, string> = {
  reject: "Écrit les lignes propres, laisse les autres de côté.",
  block: "Refuse l'écriture tant qu'une ligne est en erreur.",
  all: "Écrit tout, erreurs comprises.",
};

function ProblemCard({ p, onFixRows, onSeeSource }: {
  p: WriteProblem; onFixRows?: () => void; onSeeSource: () => void;
}) {
  const isConfig = p.category === "config";
  return (
    <div className={`ds-problem ${p.severity} ${isConfig ? "config" : "data"}`}>
      <div className="ds-problem-head">
        <span className={`ds-cat ${isConfig ? "config" : "data"}`}>
          {isConfig ? "squelette" : "données"}
        </span>
        <code className="ds-code">{p.code}</code>
        <strong>{p.message}</strong>
      </div>
      <p className="ds-hint">{p.hint}</p>
      {p.columns.length > 0 && (
        <div className="ds-chips">
          {p.columns.slice(0, 12).map((c) => <code key={c}>{c}</code>)}
          {p.columns.length > 12 && <span className="ds-more">+{p.columns.length - 12}</span>}
        </div>
      )}
      {p.count > 0 && p.rows.length > 0 && (
        <div className="ds-chips">
          <span className="ds-more">lignes&nbsp;:</span>
          {p.rows.slice(0, 10).map((r) => <code key={r}>{r}</code>)}
          {p.count > p.rows.length && <span className="ds-more">+{p.count - p.rows.length}</span>}
        </div>
      )}
      <div className="ds-problem-actions">
        {isConfig
          ? <button className="btn sm" onClick={onSeeSource}><IconList size={13} /> Voir la source brute</button>
          : onFixRows && <button className="btn sm" onClick={onFixRows}><IconTable size={13} /> Corriger dans Data</button>}
      </div>
    </div>
  );
}

export function DatasetPanel({ sid, columns, identifiers, hasRun, sourceName, notify, onFixRows, onOpenSession }: Props) {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [busy, setBusy] = useState("");

  // write form
  const [targetId, setTargetId] = useState("");          // "" = new table
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"replace" | "append" | "upsert">("replace");
  const [policy, setPolicy] = useState<"reject" | "block" | "all">("reject");
  const [keyFields, setKeyFields] = useState<string[]>([]);
  const [verdict, setVerdict] = useState<WriteResponse | null>(null);

  // browsing
  const [openId, setOpenId] = useState("");
  const [rows, setRows] = useState<TablePreview | null>(null);
  const [writes, setWrites] = useState<DatasetWriteLog[]>([]);
  const [source, setSource] = useState<TablePreview | null>(null);

  const target = datasets.find((d) => d.id === targetId) || null;

  const refresh = useCallback(async () => {
    try { setDatasets(await api.listDatasets()); }
    catch (e) { notify(e instanceof Error ? e.message : "Chargement des tables impossible.", "err"); }
  }, [notify]);
  useEffect(() => { refresh(); }, [refresh]);

  // the identifiers declared in the schema are the natural key — pre-filled, editable
  useEffect(() => {
    if (keyFields.length === 0 && identifiers.length > 0) setKeyFields(identifiers);
  }, [identifiers]);   // eslint-disable-line react-hooks/exhaustive-deps

  // picking an existing table adopts its recorded key
  useEffect(() => {
    if (target) { setName(target.name); if (target.key.length) setKeyFields(target.key); }
  }, [targetId]);      // eslint-disable-line react-hooks/exhaustive-deps

  const body = (): DatasetWriteBody => ({
    dataset_id: targetId || null,
    name: targetId ? "" : name.trim(),
    mode, policy, key_fields: keyFields,
    columns, source_name: sourceName,
  });

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try { await fn(); }
    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setBusy(""); }
  };

  const seeSource = () => {
    if (!sid) return;
    run("source", async () => {
      setSource(await api.sourcePreview(sid, 100));
      notify("Voici le fichier tel qu'il a vraiment été lu.", "info");
    });
  };

  if (!hasRun) {
    return (
      <div className="ds">
        <div className="ds-empty">
          <IconWarn size={40} />
          <h3>Lance la validation d'abord</h3>
          <p>
            C'est elle qui distingue les lignes propres des autres. Écrire des données
            non validées en base, ce serait perdre exactement ce que l'outil apporte.
          </p>
        </div>
        <DatasetList datasets={datasets} openId={openId} setOpenId={setOpenId}
                     rows={rows} setRows={setRows} writes={writes} setWrites={setWrites}
                     refresh={refresh} notify={notify} onOpenSession={onOpenSession} />
      </div>
    );
  }

  return (
    <div className="ds">
      <section className="ds-form">
        <h3><IconSave size={16} /> Enregistrer en base</h3>
        <p className="ds-sub">
          Le résultat validé part dans une table, au lieu d'un fichier à ranger quelque part.
          Chaque écriture — y compris chaque refus — est tracée.
        </p>

        <div className="ds-field">
          <label>Table</label>
          <select value={targetId} onChange={(e) => { setTargetId(e.target.value); setVerdict(null); }}>
            <option value="">➕ Nouvelle table…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name} ({d.row_count} lignes)</option>
            ))}
          </select>
          {!targetId && (
            <input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="nom de la table (ex. clients_2026)" />
          )}
          {target && (
            <span className="ds-note">
              Schéma enregistré : {target.columns.length} colonnes
              {target.key.length > 0 && <> · clé <code>{target.key.join(", ")}</code></>}
            </span>
          )}
        </div>

        <div className="ds-field">
          <label>Mode</label>
          <div className="ds-radios">
            {(["replace", "append", "upsert"] as const).map((m) => (
              <label key={m} className={`ds-radio ${mode === m ? "on" : ""}`}>
                <input type="radio" checked={mode === m}
                       onChange={() => { setMode(m); setVerdict(null); }} />
                <span>{m === "replace" ? "Remplacer" : m === "append" ? "Ajouter" : "Fusionner"}</span>
              </label>
            ))}
          </div>
          <span className="ds-note">{MODE_HELP[mode]}</span>
        </div>

        <div className="ds-field">
            <label>Clé{mode === "upsert" ? " de fusion" : " (empreinte)"}</label>
            <div className="ds-chips pick">
              {columns.map((c) => (
                <button key={c}
                        className={`ds-chip ${keyFields.includes(c) ? "on" : ""}`}
                        onClick={() => {
                          setKeyFields(keyFields.includes(c)
                            ? keyFields.filter((k) => k !== c) : [...keyFields, c]);
                          setVerdict(null);
                        }}>{c}</button>
              ))}
            </div>
            <span className="ds-note">
              {identifiers.length > 0
                ? "Pré-remplie depuis les champs marqués « identifiant » dans le schéma. "
                : "Aucun identifiant déclaré dans le schéma : choisis les colonnes de la clé. "}
              {mode !== "upsert" && "Même hors fusion, chaque ligne est marquée avec cette clé : "
                + "c'est ce qui permettra à une fusion ultérieure de la retrouver."}
            </span>
          </div>

        <div className="ds-field">
          <label>Lignes en erreur</label>
          <div className="ds-radios">
            {(["reject", "block", "all"] as const).map((pl) => (
              <label key={pl} className={`ds-radio ${policy === pl ? "on" : ""}`}>
                <input type="radio" checked={policy === pl}
                       onChange={() => { setPolicy(pl); setVerdict(null); }} />
                <span>{pl === "reject" ? "Écarter" : pl === "block" ? "Bloquer" : "Tout écrire"}</span>
              </label>
            ))}
          </div>
          <span className="ds-note">{POLICY_HELP[policy]}</span>
        </div>

        <div className="ds-actions">
          <button className="btn" disabled={!sid || !!busy}
                  onClick={() => run("preflight", async () => {
                    setVerdict(await api.preflightDataset(sid!, body()));
                  })}>
            <IconCheck size={15} /> Vérifier (à blanc)
          </button>
          <button className="btn primary" disabled={!sid || !!busy}
                  onClick={() => run("write", async () => {
                    const r = await api.writeDataset(sid!, body());
                    setVerdict(r);
                    if (r.ok) {
                      notify(`${r.rows_written} insérée(s), ${r.rows_updated} mise(s) à jour, `
                             + `${r.rows_rejected} écartée(s).`, "ok");
                      await refresh();
                    } else {
                      notify(r.blocked_by === "config"
                        ? "Écriture refusée : le squelette ne correspond pas."
                        : "Écriture refusée : des lignes sont en erreur.", "err");
                    }
                  })}>
            <IconPlay size={15} /> Écrire
          </button>
        </div>
      </section>

      {verdict && (
        <section className="ds-verdict">
          <div className={`ds-banner ${verdict.ok ? "ok" : verdict.blocked_by === "config" ? "config" : "data"}`}>
            {verdict.ok ? (
              <><IconCheck size={16} /> <strong>Écriture possible.</strong>{" "}
                {verdict.plan && (
                  <span>
                    {verdict.plan.rows_to_write} ligne(s) à écrire sur {verdict.plan.rows_in}
                    {verdict.plan.rows_rejected > 0 && <> · {verdict.plan.rows_rejected} écartée(s)</>}
                    {verdict.plan.will_delete > 0 && <> · {verdict.plan.will_delete} supprimée(s) d'abord</>}
                    {verdict.plan.creates_dataset && <> · la table sera créée</>}
                  </span>
                )}
              </>
            ) : verdict.blocked_by === "config" ? (
              <><IconWarn size={16} /> <strong>Le squelette ne correspond pas.</strong>{" "}
                Aucune correction ligne à ligne n'y changera rien — c'est la forme du
                tableau qui diverge. Regarde la source, puis ajuste la configuration.</>
            ) : (
              <><IconWarn size={16} /> <strong>Des lignes sont en erreur.</strong>{" "}
                Celles-là se corrigent à la main : va dans Data, filtre les erreurs,
                corrige ou supprime, relance la validation.</>
            )}
          </div>

          {verdict.problems.map((p, i) => (
            <ProblemCard key={i} p={p} onFixRows={onFixRows} onSeeSource={seeSource} />
          ))}
        </section>
      )}

      {source && (
        <section className="ds-source">
          <div className="ds-source-head">
            <h4><IconList size={14} /> La source, telle qu'elle a été lue</h4>
            <button className="btn sm" onClick={() => setSource(null)}><IconReset size={13} /> Fermer</button>
          </div>
          <p className="ds-sub">
            Avant traitement d'en-tête, renommage et édition. Si les colonnes sont
            décalées ou nommées <code>Unnamed: 3</code>, c'est la ligne d'en-tête ou
            le délimiteur qu'il faut corriger dans la configuration.
          </p>
          <MiniGrid p={source} />
        </section>
      )}

      <DatasetList datasets={datasets} openId={openId} setOpenId={setOpenId}
                   rows={rows} setRows={setRows} writes={writes} setWrites={setWrites}
                   refresh={refresh} notify={notify} onOpenSession={onOpenSession} />
    </div>
  );
}

function MiniGrid({ p }: { p: TablePreview }) {
  return (
    <div className="ds-grid-wrap">
      <div className="ds-caption">
        {p.total_rows} ligne(s), {p.columns.length} colonne(s)
        {p.shown_rows < p.total_rows && ` — ${p.shown_rows} affichées`}
      </div>
      <div className="ds-scroll">
        <table className="ds-grid">
          <thead><tr>{p.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
          <tbody>
            {p.data.map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j}>{v || <span className="ds-empty-cell">·</span>}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DatasetList({ datasets, openId, setOpenId, rows, setRows, writes, setWrites, refresh, notify, onOpenSession }: {
  datasets: DatasetInfo[]; openId: string; setOpenId: (s: string) => void;
  rows: TablePreview | null; setRows: (t: TablePreview | null) => void;
  writes: DatasetWriteLog[]; setWrites: (w: DatasetWriteLog[]) => void;
  refresh: () => Promise<void>; notify: Props["notify"];
  onOpenSession?: Props["onOpenSession"];
}) {
  const open = async (d: DatasetInfo) => {
    if (openId === d.id) { setOpenId(""); setRows(null); return; }
    try {
      const [r, w] = await Promise.all([api.datasetRows(d.id), api.datasetWrites(d.id)]);
      setOpenId(d.id); setRows(r); setWrites(w);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  return (
    <section className="ds-list">
      <h4><IconTable size={14} /> Tables <span className="count">{datasets.length}</span></h4>
      {datasets.length === 0 && <p className="ds-sub">Aucune table pour l'instant.</p>}
      {datasets.map((d) => (
        <div key={d.id} className="ds-row">
          <div className="ds-row-head">
            <strong>{d.name}</strong>
            <span className="ds-note">{d.row_count} lignes · {d.columns.length} colonnes</span>
            {d.key.length > 0 && <code className="ds-key">clé : {d.key.join(", ")}</code>}
            <button className="btn sm" onClick={() => open(d)}>
              {openId === d.id ? "Fermer" : "Aperçu"}
            </button>
            {onOpenSession && (
              <button className="btn sm" title="La table devient une session : filtres, tri, correction, réécriture"
                      onClick={async () => {
                        try { onOpenSession(await api.openDataset(d.id)); }
                        catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                      }}>Ouvrir dans Data</button>
            )}
            <button className="btn sm" onClick={async () => {
              await api.archiveDataset(d.id); await refresh();
              notify(`Table « ${d.name} » archivée.`, "ok");
            }}>Archiver</button>
          </div>
          {openId === d.id && rows && (
            <>
              <MiniGrid p={rows} />
              {writes.length > 0 && (
                <table className="ds-writes">
                  <thead>
                    <tr><th>Quand</th><th>Mode</th><th>Verdict</th><th>Entrées</th>
                        <th>Insérées</th><th>MAJ</th><th>Écartées</th></tr>
                  </thead>
                  <tbody>
                    {writes.map((w) => (
                      <tr key={w.id} className={w.ok ? "" : "ko"}>
                        <td>{w.created_at.replace("T", " ").slice(0, 16)}</td>
                        <td>{w.mode}</td>
                        <td>{w.ok ? "écrit" : `refusé (${w.blocked_by === "config" ? "squelette" : "données"})`}</td>
                        <td>{w.rows_in}</td><td>{w.rows_written}</td>
                        <td>{w.rows_updated}</td><td>{w.rows_rejected}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      ))}
    </section>
  );
}
