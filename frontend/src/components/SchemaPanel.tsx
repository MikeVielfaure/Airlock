import { useState } from "react";
import type { FieldConfig, HeaderConfig, Presets, ProcessStats } from "../lib/types";
import { FieldEditor } from "./FieldEditor";
import { IconEdit } from "../lib/icons";
import { InfoTip } from "./InfoTip";

interface Props {
  columns: string[];
  visible: string[];
  setVisible: (v: string[]) => void;
  unmapped: string[];
  header: HeaderConfig;
  setHeader: (h: HeaderConfig) => void;
  applyHeader: () => void;
  fields: Record<string, FieldConfig>;
  setField: (col: string, patch: Partial<FieldConfig>) => void;
  resetFields: () => void;
  addColumn: (name: string) => void;
  removeColumn: (name: string) => void;
  presets: Presets;
  tcoLabels: string[];
  stats: ProcessStats | null;
  configFields: Record<string, FieldConfig>;
  unmatchedConfig: FieldConfig[];
  assignConfigField: (col: string, field: FieldConfig) => void;
  strictHeader: boolean;
  setStrictHeader: (v: boolean) => void;
  minHeader: boolean;
  setMinHeader: (v: boolean) => void;
}

const HEADER_OPTS: { key: keyof HeaderConfig; label: string; sub: string }[] = [
  { key: "auto_header", label: "Promouvoir la première ligne de données en en-tête", sub: "Quand la ligne d'en-tête est vide et que les vrais noms se trouvent en dessous." },
  { key: "delete_empty_line_before_header", label: "Supprimer les lignes vides avant l'en-tête", sub: "" },
  { key: "delete_empty_line_after_header", label: "Supprimer les lignes vides finales", sub: "" },
  { key: "delete_all_empty_line", label: "Supprimer toutes les lignes vides", sub: "N'importe où dans le fichier." },
  { key: "delete_unamed_column", label: "Supprimer les colonnes sans nom", sub: "Colonnes sans nom d'en-tête (conservées seulement si toutes les colonnes sont sans nom)." },
];

function hasRules(f: FieldConfig): boolean {
  return Boolean(
    f.regex || f.length != null || f.on_list?.length || !f.nullable ||
    f.check_type || f.tco_mapping || f.mapping || f.normalize_case ||
    f.delimiteur || f.separator_mile || f.separator_decimal ||
    f.format || f.format_clean || f.auto_date_format || f.identifiant,
  );
}

export function SchemaPanel(p: Props) {
  const [edit, setEdit] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hideInactive, setHideInactive] = useState(false);
  const [newCol, setNewCol] = useState("");

  // File columns + columns the user declared in the schema (not in the file).
  const declared = Object.keys(p.fields).filter((k) => !p.columns.includes(k));
  const allColumns = [...p.columns, ...declared];

  const editing = edit && allColumns.includes(edit) ? edit : null;
  const many = allColumns.length > 18;

  const toggle = (c: string) =>
    p.setVisible(
      p.visible.includes(c)
        ? p.visible.filter((x) => x !== c)
        : allColumns.filter((x) => p.visible.includes(x) || x === c),
    );

  const addColumn = () => {
    const name = newCol.trim();
    if (!name || allColumns.includes(name)) { setNewCol(""); return; }
    p.addColumn(name);
    setNewCol("");
  };

  const shown = (query
    ? allColumns.filter((c) => c.toLowerCase().includes(query.toLowerCase()))
    : allColumns
  ).filter((c) => !hideInactive || p.visible.includes(c));

  return (
    <div>
      {/* structure / header */}
      <div className="sec">
        <div className="sec-h">
          <h3>Structure</h3>
          <span className="sub">Nettoyez la structure du fichier avant d'appliquer les règles de champs.</span>
          <InfoTip>
            <p><b>À quoi ça sert</b> — retirer les lignes parasites autour de l'en-tête (vides, doublons) avant de définir les champs.</p>
            <p><b>Comment faire</b> — cochez les options utiles, puis « Appliquer la structure ». En-tête strict/minimum contrôlent que les colonnes du fichier correspondent à la config.</p>
            <p><b>Ce qu'il faut</b> — un fichier chargé ; ces options sont facultatives, à false par défaut.</p>
          </InfoTip>
          <button className="btn sm" style={{ marginLeft: "auto" }} onClick={p.applyHeader}>Appliquer la structure</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px", marginTop: 8 }}>
          {HEADER_OPTS.map((o) => (
            <label className="check" key={o.key}>
              <input type="checkbox" checked={p.header[o.key]}
                onChange={(e) => p.setHeader({ ...p.header, [o.key]: e.target.checked })} />
              <span className="ctxt">{o.label}{o.sub && <div className="csub">{o.sub}</div>}</span>
            </label>
          ))}
        </div>
        <label className="check" style={{ marginTop: 8 }}>
          <input type="checkbox" checked={p.strictHeader} onChange={(e) => p.setStrictHeader(e.target.checked)} />
          <span className="ctxt">En-tête strict
            <div className="csub">Les colonnes du fichier doivent correspondre exactement à la config — sinon signalées ci-dessous.</div>
          </span>
        </label>
        <label className="check" style={{ marginTop: 4 }}>
          <input type="checkbox" checked={p.minHeader} onChange={(e) => p.setMinHeader(e.target.checked)} />
          <span className="ctxt">En-tête minimum
            <div className="csub">Le fichier doit contenir au moins les colonnes de la config — les colonnes en plus sont tolérées et ignorées.</div>
          </span>
        </label>
        {((p.strictHeader && (p.unmatchedConfig.length > 0 || p.unmapped.length > 0)) ||
          (p.minHeader && p.unmatchedConfig.length > 0)) && (
          <div className="banner err" style={{ marginTop: 8 }}>
            <span>
              <strong>{p.strictHeader ? "En-tête strict non respecté." : "En-tête minimum non respecté."}</strong>{" "}
              {p.unmatchedConfig.length > 0 && `${p.unmatchedConfig.length} champ(s) de config manquant(s) dans le fichier. `}
              {p.strictHeader && p.unmapped.length > 0 && `${p.unmapped.length} colonne(s) du fichier absente(s) de la config. `}
              Résolvez ci-dessous{p.strictHeader ? ", ou décochez en-tête strict." : ", ou décochez en-tête minimum."}
            </span>
          </div>
        )}
      </div>

      {/* fields */}
      <div className="sec">
        <div className="sec-h">
          <h3>Champs</h3>
          <span className="sub">
            {p.visible.length}/{allColumns.length} actif(s) · cliquer pour activer/désactiver, crayon pour configurer
          </span>
          <InfoTip>
            <p><b>À quoi ça sert</b> — décider quelles colonnes sont traitées, et les règles qui s'appliquent à chacune (type, nettoyage, correspondance TCO).</p>
            <p><b>Comment faire</b> — cliquez un champ pour l'activer/désactiver ; le crayon ouvre son détail (type, regex, longueur, renommage…).</p>
            <p><b>Ce qu'il faut</b> — un fichier chargé pour voir les colonnes réelles ; sans fichier, seule la config existante s'affiche.</p>
          </InfoTip>
          <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
            <label className="check" style={{ padding: 0, marginRight: 4 }}>
              <input type="checkbox" checked={hideInactive} onChange={(e) => setHideInactive(e.target.checked)} />
              <span className="ctxt" style={{ fontSize: 12 }}>Masquer les inactifs</span>
            </label>
            <button className="btn sm" onClick={() => p.setVisible([...allColumns])}>Tout activer</button>
            <button className="btn sm" onClick={() => p.setVisible([])}>Tout désactiver</button>
            <button className="btn sm" onClick={p.resetFields}>Réinitialiser les règles</button>
          </span>
        </div>

        <div className="addcol">
          <input type="text" className="mono-input" placeholder="déclarer une colonne (ex. un nom de mois)…"
            value={newCol} onChange={(e) => setNewCol(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") addColumn(); }} />
          <button className="btn sm" onClick={addColumn} disabled={!newCol.trim()}>+ Ajouter une colonne</button>
          <span className="csub">Les colonnes déclarées sont enregistrées dans la config ; elles s'appliquent à tout futur fichier qui les contient.</span>
        </div>

        {p.unmapped.length > 0 && (
          <div className="banner" style={{ marginTop: 4 }}>
            <span>
              Config importée. <strong>{p.visible.length}</strong> colonne{p.visible.length > 1 ? "s" : ""} trouvée(s)
              et activée(s) ; <strong>{p.unmapped.length}</strong> laissée(s) inactive(s) (grisée(s)). Cliquez sur l'une d'elles pour l'inclure.
            </span>
          </div>
        )}

        {p.unmatchedConfig.length > 0 && (
          <div className="orphans">
            <div className="orphans-h">
              {p.unmatchedConfig.length} champ{p.unmatchedConfig.length > 1 ? "s" : ""} de config sans colonne correspondante — associer à une colonne du fichier
            </div>
            {p.unmatchedConfig.map((f, i) => {
              const label = f.mapping || (f.name && f.name[0]) || `field_${i}`;
              return (
                <div className="orphan-row" key={label + i}>
                  <span className={`tchip ${f.type ?? "string"}`}>{(f.type ?? "string").slice(0, 3)}</span>
                  <span className="orphan-name">{label}</span>
                  <span className="orphan-arrow">←</span>
                  <select defaultValue="" onChange={(e) => { if (e.target.value) p.assignConfigField(e.target.value, f); }}>
                    <option value="">choisir une colonne…</option>
                    {p.unmapped.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              );
            })}
          </div>
        )}

        {many && (
          <input type="text" placeholder={`Filtrer ${p.columns.length} champs…`}
            value={query} onChange={(e) => setQuery(e.target.value)}
            style={{ maxWidth: 280, marginTop: 6, marginBottom: 4 }} />
        )}

        <div className={`fieldchips ${many ? "scroll" : ""}`}>
          {shown.map((c) => {
            const f = p.fields[c];
            const on = p.visible.includes(c);
            const isDeclared = !p.columns.includes(c);
            const cstat = p.stats?.per_col?.[f?.mapping || c];
            return (
              <div key={c}
                className={`fieldchip ${on ? "on" : "off"} ${editing === c ? "editing" : ""} ${isDeclared ? "declared" : ""}`}
                onClick={() => toggle(c)}
                title={isDeclared ? "Colonne déclarée (absente du fichier chargé)" : (on ? "Cliquer pour désactiver" : "Cliquer pour activer")}>
                <span className={`tchip ${f?.type ?? "string"}`}>{(f?.type ?? "string").slice(0, 3)}</span>
                <span className="fname">{c}</span>
                {isDeclared && <span className="declared-badge" title="Absente du fichier chargé">déclarée</span>}
                {cstat?.errors ? <span className="dot err" title={`${cstat.errors} erreur(s)`} /> : null}
                {cstat?.cleans ? <span className="dot clean" title={`${cstat.cleans} nettoyée(s)`} /> : null}
                {!cstat && f && hasRules(f) ? <span className="dot rule" title="a des règles" /> : null}
                <button className="chip-edit" title="Configurer le champ"
                  onClick={(e) => { e.stopPropagation(); setEdit(editing === c ? null : c); }}>
                  <IconEdit size={13} />
                </button>
                {isDeclared && (
                  <button className="chip-edit" title="Retirer la colonne déclarée"
                    onClick={(e) => { e.stopPropagation(); if (editing === c) setEdit(null); p.removeColumn(c); }}>×</button>
                )}
              </div>
            );
          })}
          {shown.length === 0 && <span style={{ color: "var(--ink-faint)", fontSize: 12 }}>Aucun champ ne correspond à « {query} ».</span>}
        </div>

        {editing && p.fields[editing] && (
          <div style={{ marginTop: 14 }}>
            <FieldEditor
              col={editing}
              field={p.fields[editing]}
              presets={p.presets}
              tcoLabels={p.tcoLabels}
              configFields={p.configFields}
              onChange={(patch) => p.setField(editing, patch)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
