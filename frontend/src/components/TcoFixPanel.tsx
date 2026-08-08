import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo, DatasetInfo, ProcessResponse, TcoSuggestRow } from "../lib/types";
import { IconCheck, IconPlay, IconSave, IconWarn } from "../lib/icons";
import { InfoTip } from "./InfoTip";

type TargetSource = { dataset_id: string; query: string };

/** Split one CSV line into cells, honouring double-quoted values (with ""
 * as an escaped quote) — enough for the short, single-line cells a TCO
 * table holds; embedded newlines inside a quoted cell are not supported. */
function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "", inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else { inQuotes = false; }
      } else cur += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ",") { out.push(cur); cur = ""; }
    else cur += c;
  }
  out.push(cur);
  return out;
}

function csvEscape(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

type TcoRow = { type: string; source: string; target: string };

/** TYPE is only emitted when at least one row uses it, so a plain
 * two-column TCO stays exactly as simple as before. */
function tcoRowsToCsv(rows: TcoRow[]): string {
  const useType = rows.some((r) => r.type.trim());
  const header = useType ? ["TYPE", "SOURCE_VALUE", "TARGET_LABEL"] : ["SOURCE_VALUE", "TARGET_LABEL"];
  const lines = [header.join(",")];
  for (const r of rows) {
    if (!r.source.trim() && !r.target.trim()) continue;
    const cells = useType ? [r.type, r.source, r.target] : [r.source, r.target];
    lines.push(cells.map(csvEscape).join(","));
  }
  return lines.join("\n");
}

interface Props {
  result: ProcessResponse | null;
  fieldTypes: Record<string, string>;
  tcoArtefactId: string;
  editable: boolean;
  notify: (m: string, k?: "ok" | "err" | "info") => void;
  /** The active workbench session, if any — lets a TCO be built from
   * whatever data already loaded there (file, blank, SQL, API...) instead
   * of a second, TCO-specific loading path. */
  sid?: string | null;
  columns?: string[];
  /** Attach a library TCO to the active session so validation actually uses
   * it — building/editing one here only ever touches the library; without
   * this second, explicit step every field configured for it reads as
   * "Sans TCO" (nothing attached), not covered or uncovered. */
  onTcoFromArtefact?: (artefactId: string) => void;
}

/**
 * Everything about a TCO in one place: build one (by hand, from the active
 * session's data, or from a ready-made CSV), and fix one that a run just
 * flagged as incomplete. It used to be split — building lived under Flux —
 * which read as "correspondences" living somewhere other than the tab named
 * for them.
 */
export function TcoFixPanel({ result, fieldTypes, tcoArtefactId, editable, notify, sid, columns = [],
                              onTcoFromArtefact }: Props) {
  const [rows, setRows] = useState<TcoSuggestRow[]>([]);
  const [busy, setBusy] = useState(false);

  const [tcos, setTcos] = useState<ArtefactInfo[]>([]);
  const refresh = useCallback(async () => {
    try { setTcos(await api.listArtefacts("tco")); } catch { /* empty state is fine */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  // tco mini-form
  const [tcoName, setTcoName] = useState("");
  const tcoFileRef = useRef<HTMLInputElement>(null);
  const [tcoMode, setTcoMode] = useState<"build" | "upload" | "session">("build");
  const [tcoTarget, setTcoTarget] = useState("");
  const [tcoRows, setTcoRows] = useState<TcoRow[]>([{ type: "", source: "", target: "" }]);
  const [tcoSourceCol, setTcoSourceCol] = useState("");
  const [tcoTargetCol, setTcoTargetCol] = useState("");
  const [tcoTypeCol, setTcoTypeCol] = useState("");
  const [tcoTypeValue, setTcoTypeValue] = useState("");

  const addTcoRow = () => setTcoRows([...tcoRows, { type: "", source: "", target: "" }]);
  const removeTcoRow = (i: number) => setTcoRows(tcoRows.filter((_, j) => j !== i));
  const setTcoRow = (i: number, patch: Partial<TcoRow>) =>
    setTcoRows(tcoRows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  // ── constrain a type's target labels to a reference list ────────────
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  useEffect(() => { api.listDatasets().then(setDatasets).catch(() => {}); }, []);
  const [tsType, setTsType] = useState("");
  const [tsDataset, setTsDataset] = useState("");
  const [tsQuery, setTsQuery] = useState("");
  const [tsPreview, setTsPreview] = useState<string[] | null>(null);
  const [tsBusy, setTsBusy] = useState(false);

  const testTargetSource = async () => {
    if (!tsDataset || !tsQuery.trim()) return;
    setTsBusy(true);
    try {
      const r = await api.resolveTcoTargetValues(tsDataset, tsQuery);
      setTsPreview(r.values);
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); setTsPreview(null); }
    finally { setTsBusy(false); }
  };

  const saveTargetSource = async () => {
    if (!tcoTarget || !tsType.trim() || !tsDataset || !tsQuery.trim()) return;
    const t = tcos.find((x) => x.id === tcoTarget);
    if (!t) return;
    setTsBusy(true);
    try {
      const v = await api.getArtefactVersion("tco", tcoTarget, t.latest_version_no);
      const csv = String((v.body as { csv?: string }).csv ?? "");
      const existing = (v.body as { target_sources?: Record<string, TargetSource> }).target_sources ?? {};
      const merged = { ...existing, [tsType.trim()]: { dataset_id: tsDataset, query: tsQuery.trim() } };
      await api.addArtefactVersion("tco", tcoTarget, { csv, target_sources: merged });
      notify(`Contrainte enregistrée pour le type « ${tsType.trim()} ».`, "ok");
      setTsType(""); setTsDataset(""); setTsQuery(""); setTsPreview(null);
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setTsBusy(false); }
  };

  /** Picking an existing TCO loads its latest CSV back into rows — editing
   * it and saving adds a version, it never overwrites the one being edited. */
  const loadTcoForEdit = async (id: string) => {
    setTcoTarget(id);
    if (!id) { setTcoRows([{ type: "", source: "", target: "" }]); return; }
    const a = tcos.find((t) => t.id === id);
    if (!a) return;
    try {
      const v = await api.getArtefactVersion("tco", id, a.latest_version_no);
      const csv = String((v.body as { csv?: string }).csv ?? "");
      const lines = csv.split(/\r?\n/).filter((l) => l.trim());
      if (lines.length < 2) { setTcoRows([{ type: "", source: "", target: "" }]); return; }
      const header = parseCsvLine(lines[0]).map((h) => h.trim().toUpperCase());
      const iType = header.indexOf("TYPE");
      const iSrc = header.findIndex((h) => ["SOURCE_VALUE", "SOURCE"].includes(h));
      const iTgt = header.findIndex((h) => ["TARGET_LABEL", "TARGET", "LABEL"].includes(h));
      const parsed = lines.slice(1).map((l) => {
        const cells = parseCsvLine(l);
        return {
          type: iType >= 0 ? (cells[iType] ?? "") : "",
          source: iSrc >= 0 ? (cells[iSrc] ?? "") : "",
          target: iTgt >= 0 ? (cells[iTgt] ?? "") : "",
        };
      });
      setTcoRows(parsed.length ? parsed : [{ type: "", source: "", target: "" }]);
    } catch (e) { notify(e instanceof Error ? e.message : "Échec du chargement du TCO.", "err"); }
  };

  const saveTcoTable = async () => {
    const valid = tcoRows.filter((r) => r.source.trim() && r.target.trim());
    if (!valid.length) { notify("Ajoutez au moins une ligne avec une valeur source et un label cible.", "err"); return; }
    const csv = tcoRowsToCsv(tcoRows);
    try {
      let id = tcoTarget;
      if (tcoTarget) {
        const a = await api.addArtefactVersion("tco", tcoTarget, { csv });
        notify(`TCO mis à jour — nouvelle version v${a.latest_version_no}.`, "ok");
      } else {
        if (!tcoName.trim()) { notify("Donnez un nom à ce TCO.", "err"); return; }
        const a = await api.createArtefact("tco", { name: tcoName.trim(), csv });
        id = a.id;
        notify(`TCO « ${tcoName.trim()} » enregistré dans la bibliothèque.`, "ok");
        setTcoName("");
      }
      setTcoRows([{ type: "", source: "", target: "" }]);
      setTcoTarget("");
      // A TCO just built or updated here is the one you meant to use — attach
      // it to the active session right away instead of leaving it parked in
      // the library for a separate, easy-to-forget "Charger" step.
      if (sid && onTcoFromArtefact) onTcoFromArtefact(id);
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'enregistrement du TCO.", "err"); }
  };

  const saveTco = async () => {
    const file = tcoFileRef.current?.files?.[0];
    if (!tcoName.trim() || !file) { notify("Choisissez un nom et un fichier CSV de TCO.", "err"); return; }
    try {
      const csv = await file.text();
      const a = await api.createArtefact("tco", { name: tcoName.trim(), csv });
      setTcoName(""); if (tcoFileRef.current) tcoFileRef.current.value = "";
      notify("TCO enregistré dans la bibliothèque.", "ok");
      if (sid && onTcoFromArtefact) onTcoFromArtefact(a.id);
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'enregistrement du TCO.", "err"); }
  };

  const saveTcoFromSession = async () => {
    if (!sid || !tcoSourceCol.trim() || !tcoTargetCol.trim()) return;
    if (!tcoTarget && !tcoName.trim()) { notify("Donnez un nom à ce TCO.", "err"); return; }
    try {
      const r = await api.saveSessionAsTco(sid, {
        source_column: tcoSourceCol, target_column: tcoTargetCol,
        type_column: tcoTypeCol || undefined, type_value: tcoTypeCol ? undefined : tcoTypeValue,
        artefact_id: tcoTarget || undefined, name: tcoTarget ? undefined : tcoName.trim(),
      });
      notify(`TCO enregistré — v${r.version_no}, ${r.rows} ligne(s).`, "ok");
      setTcoSourceCol(""); setTcoTargetCol(""); setTcoTypeCol(""); setTcoTypeValue("");
      setTcoName(""); setTcoTarget("");
      if (onTcoFromArtefact) onTcoFromArtefact(r.artefact_id);
      refresh();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'enregistrement du TCO.", "err"); }
  };

  // Once "Proposer les entrées manquantes" lists rows to complete, any TYPE
  // among them that has a target_source gets its allowed values resolved —
  // "Libellé cible" becomes a choice among these instead of free text.
  const [allowedByType, setAllowedByType] = useState<Record<string, string[]>>({});
  const rowTypesKey = Array.from(new Set(rows.map((r) => r.TYPE))).sort().join("|");
  useEffect(() => {
    if (!rowTypesKey || !tcoArtefactId) { setAllowedByType({}); return; }
    const types = rowTypesKey.split("|");
    let cancelled = false;
    (async () => {
      try {
        const art = await api.listArtefacts("tco");
        const t = art.find((a) => a.id === tcoArtefactId);
        if (!t) return;
        const v = await api.getArtefactVersion("tco", tcoArtefactId, t.latest_version_no);
        const sources = (v.body as { target_sources?: Record<string, TargetSource> }).target_sources ?? {};
        const scoped = types.filter((ty) => sources[ty]);
        const entries = await Promise.all(scoped.map(async (ty) => {
          try { return [ty, (await api.resolveTcoTargetValues(sources[ty].dataset_id, sources[ty].query)).values] as const; }
          catch { return [ty, []] as const; }
        }));
        if (!cancelled) setAllowedByType(Object.fromEntries(entries));
      } catch { if (!cancelled) setAllowedByType({}); }
    })();
    return () => { cancelled = true; };
  }, [rowTypesKey, tcoArtefactId]);

  const uncovered = result?.tco_uncovered ?? {};
  // A column can be uncovered for two very different reasons: values missing
  // from an otherwise-loaded table (fix: complete the table), or no TCO
  // table attached to this run at all (fix: attach one first — completing
  // entries makes no sense without a table to add them to).
  const noTcoCols = Object.entries(uncovered)
    .filter(([, rows]) => rows[0]?.reason === "no_tco").map(([col]) => col);
  const realUncovered = Object.fromEntries(
    Object.entries(uncovered).filter(([col]) => !noTcoCols.includes(col)));
  const count = Object.values(realUncovered).reduce((n, v) => n + v.length, 0);

  const info = (
    <InfoTip>
      <p><b>À quoi ça sert</b> — corriger une erreur de <em>correspondance</em> (une valeur absente de la table TCO), sans toucher au fichier lui-même.</p>
      <p><b>Comment faire</b> — « Proposer les entrées manquantes » liste les valeurs non couvertes ; complétez le libellé cible pour chacune, « Ajouter à la table », puis relancez le contrôle.</p>
      <p><b>Ce qu'il faut</b> — une validation déjà lancée dans Données, et une table de référence (TCO) chargée.</p>
    </InfoTip>
  );

  return (
    <div className="tf">
      {/* ── build / save a TCO ─────────────────────────────────────── */}
      <div className="sec-h">
        <h3 style={{ fontSize: 14 }}>Table de correspondance (TCO)</h3>
        <span className="sub">Une valeur source (ex. « M ») mappée vers un label cible (ex. « MASCULIN »), utilisée par le mapping des champs.
          <InfoTip>
            <p><b>À quoi ça sert</b> — un TCO vérifie qu'une valeur de champ correspond bien au label attendu (le mapping configuré sur le champ), au lieu de laisser passer n'importe quelle valeur.</p>
            <p><b>Comment faire</b> — construisez le tableau ligne par ligne (valeur source → label cible), depuis les données d'une session déjà chargée, ou importez un CSV déjà prêt. La colonne « Type » est optionnelle : renseignez-la si une même table sert plusieurs champs (ex. CIVILITE et PAYS dans le même fichier).</p>
            <p><b>Ce qu'il faut</b> — au moins une ligne avec une valeur source et un label cible. Enregistrer ajoute une nouvelle version si vous avez choisi un TCO existant — l'ancienne version reste inchangée.</p>
          </InfoTip>
        </span>
      </div>
      {/* Un contrôle segmenté, pas trois boutons. Le style `primary` signifie
          « l'action principale de cet écran » ; l'employer pour « le mode
          sélectionné » faisait lire ces trois choix comme une action mise en
          avant et deux actions secondaires, alors qu'il n'y a rien à faire
          ici — seulement à choisir d'où viendra le tableau. */}
      <div className="seg" role="tablist" aria-label="Origine du tableau">
        <button role="tab" aria-selected={tcoMode === "build"}
          className={`seg-item ${tcoMode === "build" ? "on" : ""}`} onClick={() => setTcoMode("build")}>
          Construire un tableau
        </button>
        <button role="tab" aria-selected={tcoMode === "session"}
          className={`seg-item ${tcoMode === "session" ? "on" : ""}`} onClick={() => setTcoMode("session")}>
          Depuis la session active
        </button>
        <button role="tab" aria-selected={tcoMode === "upload"}
          className={`seg-item ${tcoMode === "upload" ? "on" : ""}`} onClick={() => setTcoMode("upload")}>
          Importer un fichier CSV
        </button>
      </div>

      {tcoMode === "session" ? (
        <div className="flowform" style={{ marginTop: 8 }}>
          {!sid ? (
            <p className="empty">Chargez d'abord un fichier ou une session (onglet Schéma & Règles) — peu importe le
              moyen, fichier, saisie vierge, source SQL ou API : la session qui en résulte peut devenir un TCO.</p>
          ) : (
            <>
              <div className="frow"><label>Ajouter une version à</label>
                <select value={tcoTarget} onChange={(e) => setTcoTarget(e.target.value)}>
                  <option value="">— nouveau TCO —</option>
                  {tcos.map((t) => <option key={t.id} value={t.id}>{t.name} (v{t.latest_version_no})</option>)}
                </select>
                {tcoTarget && onTcoFromArtefact && (
                  <button className="btn sm" title="Attacher ce TCO à la session en cours, pour la validation"
                          onClick={() => onTcoFromArtefact(tcoTarget)}>
                    Charger pour cette session
                  </button>
                )}</div>
              {!tcoTarget && (
                <div className="frow"><label>Nom du nouveau TCO</label>
                  <input value={tcoName} onChange={(e) => setTcoName(e.target.value)} placeholder="ex. civilites" /></div>
              )}
              <div className="frow"><label>Colonne source</label>
                <select value={tcoSourceCol} onChange={(e) => setTcoSourceCol(e.target.value)}>
                  <option value="">— choisir —</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select></div>
              <div className="frow"><label>Colonne cible</label>
                <select value={tcoTargetCol} onChange={(e) => setTcoTargetCol(e.target.value)}>
                  <option value="">— choisir —</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select></div>
              <div className="frow"><label>Colonne type (optionnel)</label>
                <select value={tcoTypeCol} onChange={(e) => setTcoTypeCol(e.target.value)}>
                  <option value="">— aucune —</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select></div>
              {!tcoTypeCol && (
                <div className="frow"><label>Type fixe (optionnel)</label>
                  <input value={tcoTypeValue} onChange={(e) => setTcoTypeValue(e.target.value)}
                         placeholder="ex. CIVILITE — laisser vide si la table sert tous les champs" /></div>
              )}
              <div className="filterbar" style={{ marginTop: 6 }}>
                <button className="btn primary" disabled={!tcoSourceCol || !tcoTargetCol}
                        onClick={saveTcoFromSession}>
                  {tcoTarget ? "Enregistrer comme nouvelle version" : "Enregistrer le TCO dans la bibliothèque"}
                </button>
              </div>
            </>
          )}
        </div>
      ) : tcoMode === "build" ? (
        <div className="flowform" style={{ marginTop: 8 }}>
          <div className="frow"><label>Ajouter une version à</label>
            <select value={tcoTarget} onChange={(e) => loadTcoForEdit(e.target.value)}>
              <option value="">— nouveau TCO —</option>
              {tcos.map((t) => <option key={t.id} value={t.id}>{t.name} (v{t.latest_version_no})</option>)}
            </select>
            {tcoTarget && onTcoFromArtefact && (
              <button className="btn sm" title="Attacher ce TCO à la session en cours, pour la validation"
                      onClick={() => onTcoFromArtefact(tcoTarget)}>
                Charger pour cette session
              </button>
            )}</div>
          {!tcoTarget && (
            <div className="frow"><label>Nom du nouveau TCO</label>
              <input value={tcoName} onChange={(e) => setTcoName(e.target.value)} placeholder="ex. civilites" /></div>
          )}
          <table className="grid" style={{ marginTop: 8, width: "100%" }}>
            <thead><tr><th>Type (optionnel)</th><th>Valeur source</th><th>Label cible</th><th></th></tr></thead>
            <tbody>
              {tcoRows.map((r, i) => (
                <tr key={i}>
                  <td><input className="mono-input" value={r.type} placeholder="ex. CIVILITE"
                    onChange={(e) => setTcoRow(i, { type: e.target.value })} /></td>
                  <td><input className="mono-input" value={r.source} placeholder="ex. M"
                    onChange={(e) => setTcoRow(i, { source: e.target.value })} /></td>
                  <td><input className="mono-input" value={r.target} placeholder="ex. MASCULIN"
                    onChange={(e) => setTcoRow(i, { target: e.target.value })} /></td>
                  <td><button className="hclear" onClick={() => removeTcoRow(i)}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="filterbar" style={{ marginTop: 6 }}>
            <button className="btn sm" onClick={addTcoRow}>+ ligne</button>
            <button className="btn primary" onClick={saveTcoTable}>
              {tcoTarget ? "Enregistrer comme nouvelle version" : "Enregistrer le TCO dans la bibliothèque"}
            </button>
          </div>
        </div>
      ) : (
        <div className="flowform" style={{ marginTop: 8 }}>
          <div className="frow"><label>Nom du nouveau TCO</label>
            <input value={tcoName} onChange={(e) => setTcoName(e.target.value)} placeholder="ex. civilites" /></div>
          <div className="frow"><label>Fichier CSV</label>
            <input type="file" ref={tcoFileRef} accept=".csv,.txt" /></div>
          <button className="btn" onClick={saveTco}>Enregistrer le TCO dans la bibliothèque</button>
        </div>
      )}

      {/* ── constrain a type's target labels to a reference list ───── */}
      <div className="sec-h gap-lg">
        <h3 style={{ fontSize: 14 }}>Contraindre les valeurs cible</h3>
        <span className="sub">Au lieu d'un libellé cible tapé librement, imposer qu'il vienne d'une liste — par
          exemple tout code de « list_tco_value » où type = job.
          <InfoTip>
            <p><b>À quoi ça sert</b> — sans ça, rien n'empêche un libellé cible qui ne correspond à aucun code réel (une faute de frappe, une valeur inventée) d'entrer dans la table.</p>
            <p><b>Comment faire</b> — choisissez le TCO et le type concernés, la table de référence (une table BDD déjà enregistrée), et une requête qui en extrait les valeurs autorisées — la colonne renvoyée doit s'appeler <code>value</code> (ex. <code>SELECT code AS value FROM list WHERE type='job'</code>, la table de référence est toujours nommée <code>list</code> dans la requête). « Tester » avant d'enregistrer.</p>
            <p><b>Ce qu'il faut</b> — un TCO déjà enregistré (choisi ci-dessus dans « Ajouter une version à »). La contrainte s'applique dès l'enregistrement, y compris si quelqu'un tente de l'contourner par l'API directement.</p>
          </InfoTip>
        </span>
      </div>
      {!tcoTarget ? (
        <p className="empty">Choisissez un TCO existant ci-dessus (« Ajouter une version à ») pour lui configurer une contrainte.</p>
      ) : (
        <div className="flowform">
          <div className="frow"><label>Type concerné</label>
            <input value={tsType} onChange={(e) => setTsType(e.target.value)} placeholder="ex. job" /></div>
          <div className="frow"><label>Table de référence</label>
            <select value={tsDataset} onChange={(e) => setTsDataset(e.target.value)}>
              <option value="">— choisir —</option>
              {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select></div>
          <div className="frow full"><label>Requête (doit renvoyer une colonne <code>value</code>)</label>
            <textarea className="mono-input" rows={2} value={tsQuery}
                      onChange={(e) => setTsQuery(e.target.value)}
                      placeholder="SELECT code AS value FROM list WHERE type='job'" /></div>
          <div className="filterbar" style={{ marginTop: 6 }}>
            <button className="btn sm" disabled={tsBusy || !tsDataset || !tsQuery.trim()} onClick={testTargetSource}>
              Tester
            </button>
            <button className="btn primary" disabled={tsBusy || !tsType.trim() || !tsDataset || !tsQuery.trim()}
                    onClick={saveTargetSource}>
              Enregistrer la contrainte
            </button>
          </div>
          {tsPreview !== null && (
            <p className="csub">{tsPreview.length} valeur(s) autorisée(s) : {tsPreview.slice(0, 20).join(", ")}
              {tsPreview.length > 20 ? "…" : ""}</p>
          )}
        </div>
      )}

      {/* ── fix values a run flagged as uncovered ──────────────────── */}
      <div className="sec-h gap-lg">
        <h3 style={{ fontSize: 14 }}>Corriger les valeurs non couvertes</h3>
      </div>
      {!result ? (
        <p className="empty">Lancez d'abord le contrôle (onglet Données). {info}</p>
      ) : count === 0 && noTcoCols.length === 0 ? (
        <p className="tf-ok"><IconCheck size={14} /> Toutes les valeurs sont couvertes
          par la table de correspondance. {info}</p>
      ) : (
        <>
          {noTcoCols.length > 0 && (
            <p className="tf-warn">
              <IconWarn size={14} /> {noTcoCols.map((c) => <code key={c}>{c}</code>)
                .reduce((a, b) => <>{a}, {b}</>)} : configuré(s) pour une correspondance TCO,
              mais aucune table n'est chargée pour cette session — rien n'a été vérifié.
              Chargez une table (ci-dessus, ou depuis la bibliothèque dans la barre latérale)
              puis relancez le contrôle.
            </p>
          )}
          {count > 0 && (
            <p className="tf-warn">
              <IconWarn size={14} /> {count} valeur(s) absente(s) de la table de correspondance.
              Ce sont des erreurs de <strong>correspondance</strong>, pas des erreurs de
              données : le fichier est probablement correct, c'est la table qui est incomplète.
              {info}
            </p>
          )}

          {count > 0 && (
            <button className="btn" disabled={busy} onClick={async () => {
              setBusy(true);
              try {
                const r = await api.suggestTco(realUncovered as never, fieldTypes);
                setRows(r.rows);
              } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
              finally { setBusy(false); }
            }}><IconPlay size={14} /> Proposer les entrées manquantes</button>
          )}

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
                        {allowedByType[r.TYPE] ? (
                          <select value={r.TARGET_LABEL} disabled={!editable}
                                  onChange={(e) => setRows((rs) => rs.map((x, j) =>
                                    j === i ? { ...x, TARGET_LABEL: e.target.value } : x))}>
                            <option value="">— choisir —</option>
                            {allowedByType[r.TYPE].map((v) => <option key={v} value={v}>{v}</option>)}
                          </select>
                        ) : (
                          <input value={r.TARGET_LABEL} disabled={!editable}
                                 placeholder="ce que ça devrait devenir"
                                 onChange={(e) => setRows((rs) => rs.map((x, j) =>
                                   j === i ? { ...x, TARGET_LABEL: e.target.value } : x))} />
                        )}
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
                            refresh();
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
        </>
      )}
    </div>
  );
}
