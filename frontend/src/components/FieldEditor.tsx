import type { FieldConfig, FieldType, Presets } from "../lib/types";

interface Props {
  col: string;
  field: FieldConfig;
  presets: Presets;
  tcoLabels: string[];
  keysAvailable: boolean;
  availableKeys: { name: string; label: string }[];
  configFields: Record<string, FieldConfig>;
  onChange: (patch: Partial<FieldConfig>) => void;
}

const CASE_LABELS: Record<string, string> = {
  upper: "MAJUSCULES", lower: "minuscules", title: "Titre", "": "Tel quel",
};

// Insertable helpers for the dynamic "Match column by" field.
const NAME_TOKENS = ["MOIS", "MOIS2", "MOIS_NOM", "MOIS_COURT", "JOUR", "JOUR_NOM", "ANNEE", "DATENOW"];
const NAME_FUNCS = ["LEFT(", "RIGHT(", "SUBSTRING(", "CONCAT(", "UPPER(", "LOWER(", "REPLACE("];

export function FieldEditor({ col, field, presets, tcoLabels, keysAvailable, availableKeys, configFields, onChange }: Props) {
  const isDate = field.type === "date";
  const isNum = field.type === "integer" || field.type === "float";
  const renaming = Boolean(field.mapping) && field.rename_output;
  const finalName = renaming ? field.mapping! : col;
  const configNames = Object.keys(configFields);
  const cfMatch = field.mapping ? configFields[field.mapping] : undefined;

  // Apply the rules of a config field to this column (keeps the file column
  // attachment via name, sets the output name to the config field).
  const inherit = () => {
    if (!cfMatch || !field.mapping) return;
    onChange({ ...cfMatch, name: [col], mapping: field.mapping, rename_output: field.rename_output });
  };

  // Append a tag/function into the "Match column by" value.
  const appendName = (text: string) => {
    const cur = (field.name ?? []).join(", ");
    onChange({ name: (cur + text).split(",").map((s) => s.trim()).filter(Boolean) });
  };

  return (
    <div className="editor">
      <div className="editor-h">
        <span className="col">{col}</span>
        {renaming && (
          <>
            <span className="arrow">→</span>
            <span className="col" style={{ color: "var(--accent)" }}>{field.mapping}</span>
          </>
        )}
        <span style={{ marginLeft: "auto" }}>
          <span className={`tchip ${field.type}`}>{field.type}</span>
        </span>
      </div>

      <div className="editor-body">
        <div className="editor-grid">
          {/* IDENTITY */}
          <div className="editor-section-h">Identité</div>

          <div className="frow">
            <label>Type</label>
            <select
              value={field.type}
              onChange={(e) => onChange({ type: e.target.value as FieldType })}
            >
              {presets.field_types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div className="frow full">
            <label>Renommer / associer à un champ de config <span style={{ color: "var(--ink-faint)" }}>(optionnel)</span></label>
            <input
              type="text" className="mono-input" placeholder={col}
              list={configNames.length ? "cfg-fields" : undefined}
              value={field.mapping ?? ""}
              onChange={(e) => onChange({ mapping: e.target.value || null })}
            />
            {configNames.length > 0 && (
              <datalist id="cfg-fields">
                {configNames.map((n) => <option key={n} value={n} />)}
              </datalist>
            )}
            {cfMatch && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
                <button type="button" className="preset" onClick={inherit}>
                  ↧ Hériter des règles du champ de config « {field.mapping} »
                </button>
                <span style={{ color: "var(--ink-faint)", fontSize: 11 }}>type {cfMatch.type}
                  {cfMatch.regex ? " · regex" : ""}{cfMatch.length != null ? ` · long. ${cfMatch.length}` : ""}
                  {cfMatch.tco_mapping ? " · TCO" : ""}</span>
              </div>
            )}
            {field.mapping && (
              <label className="check" style={{ marginTop: 6 }}>
                <input type="checkbox" checked={field.rename_output}
                  onChange={(e) => onChange({ rename_output: e.target.checked })} />
                <span className="ctxt">Afficher ce nom dans le tableau et l'export
                  <div className="csub">Désactivé : les règles s'appliquent mais la colonne garde son nom d'origine « {col} ».</div>
                </span>
              </label>
            )}
          </div>

          <div className="frow full">
            <label>Faire correspondre la colonne par <span style={{ color: "var(--ink-faint)" }}>(avancé — le nom peut varier)</span></label>
            <input
              type="text" className="mono-input"
              value={(field.name ?? []).join(", ")}
              placeholder={col}
              onChange={(e) => onChange({ name: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
            />
            <div className="namehelp">
              <div className="namehelp-row">
                <span className="chiplabel">balises :</span>
                {NAME_TOKENS.map((t) => (
                  <button key={t} type="button" className="microchip"
                    onClick={() => appendName(`[${t}]`)}>[{t}]</button>
                ))}
              </div>
              <div className="namehelp-row">
                <span className="chiplabel">fonctions :</span>
                {NAME_FUNCS.map((f) => (
                  <button key={f} type="button" className="microchip"
                    onClick={() => appendName((field.name?.length ? "" : "=") + f)} title="préfixer le nom entier par = pour utiliser des fonctions">{f}</button>
                ))}
              </div>
              <div className="csub">
                Relie ce champ à une colonne du fichier. Utilise une balise pour un nom qui varie (ex. <code>[MOIS_COURT]</code>),
                ou commence par <code>=</code> pour un nom calculé (ex. <code>=LEFT([MOIS_NOM], 4)</code>).
              </div>
            </div>
          </div>

          <label className="check full">
            <input
              type="checkbox" checked={field.identifiant}
              onChange={(e) => onChange({ identifiant: e.target.checked })}
            />
            <span className="ctxt">Utiliser comme identifiant de ligne
              <div className="csub">Cette colonne identifie les lignes dans le rapport, à la place du numéro de ligne.</div>
            </span>
          </label>

          {/* CLEANING */}
          <div className="editor-section-h">Nettoyage</div>

          <label className="check">
            <input type="checkbox" checked={field.trim} onChange={(e) => onChange({ trim: e.target.checked })} />
            <span className="ctxt">Supprimer les espaces superflus</span>
          </label>

          <div className="frow">
            <label>Normaliser la casse</label>
            <select
              value={field.normalize_case ?? ""}
              onChange={(e) => onChange({ normalize_case: (e.target.value || null) as any })}
            >
              {["", ...presets.case_modes].map((m) => (
                <option key={m || "none"} value={m}>{CASE_LABELS[m] ?? m}</option>
              ))}
            </select>
          </div>

          <div className="frow">
            <label>Retirer un caractère <span style={{ color: "var(--ink-faint)" }}>(ex. €, espaces)</span></label>
            <input
              type="text" className="mono-input" maxLength={3}
              value={field.delimiteur ?? ""}
              onChange={(e) => onChange({ delimiteur: e.target.value || null })}
            />
          </div>

          {isNum && (
            <div className="frow">
              <label>Format des nombres</label>
              <div style={{ display: "flex", gap: 14, paddingTop: 4 }}>
                <label className="check" style={{ padding: 0 }}>
                  <input type="checkbox" checked={field.separator_mile}
                    onChange={(e) => onChange({ separator_mile: e.target.checked })} />
                  <span className="ctxt">Retirer les séparateurs de milliers</span>
                </label>
                <label className="check" style={{ padding: 0 }}>
                  <input type="checkbox" checked={field.separator_decimal}
                    onChange={(e) => onChange({ separator_decimal: e.target.checked })} />
                  <span className="ctxt">Décimale → "."</span>
                </label>
              </div>
            </div>
          )}

          {isDate && (
            <>
              <label className="check full">
                <input type="checkbox" checked={field.auto_date_format}
                  onChange={(e) => onChange({ auto_date_format: e.target.checked })} />
                <span className="ctxt">Détecter automatiquement le format source
                  <div className="csub">Utilise une heuristique jour/mois quand l'ordre est ambigu.</div>
                </span>
              </label>
              {!field.auto_date_format && (
                <div className="frow">
                  <label>Format source</label>
                  <select value={field.format ?? ""} onChange={(e) => onChange({ format: e.target.value || null })}>
                    <option value="">—</option>
                    {presets.date_formats.map((f) => <option key={f} value={f}>{f}</option>)}
                  </select>
                </div>
              )}
              <div className="frow">
                <label>Format cible</label>
                <select value={field.format_clean ?? ""} onChange={(e) => onChange({ format_clean: e.target.value || null })}>
                  <option value="">—</option>
                  {presets.date_formats.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
            </>
          )}

          {/* VALIDATION */}
          <div className="editor-section-h">Validation</div>

          <label className="check">
            <input type="checkbox" checked={!field.nullable}
              onChange={(e) => onChange({ nullable: !e.target.checked })} />
            <span className="ctxt">Obligatoire (aucune valeur vide)</span>
          </label>

          <label className="check">
            <input type="checkbox" checked={field.check_type}
              onChange={(e) => onChange({ check_type: e.target.checked })} />
            <span className="ctxt">Vérifier le type ({field.type})</span>
          </label>

          <div className="frow">
            <label>Longueur maximale</label>
            <input type="number" min={0} placeholder="—"
              value={field.length ?? ""}
              onChange={(e) => onChange({ length: e.target.value ? Number(e.target.value) : null })} />
          </div>

          <div className="frow">
            <label>Valeurs autorisées <span style={{ color: "var(--ink-faint)" }}>(séparées par des virgules)</span></label>
            <input type="text" className="mono-input" placeholder="M, F, X"
              value={(field.on_list ?? []).join(", ")}
              onChange={(e) => {
                const v = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
                onChange({ on_list: v.length ? v : null });
              }} />
          </div>

          <div className="frow full">
            <label>Motif regex</label>
            <input type="text" className="mono-input" placeholder="^[0-9]{14}$"
              value={field.regex ?? ""}
              onChange={(e) => onChange({ regex: e.target.value || null })} />
            <div className="preset-row">
              {Object.entries(presets.regex_presets).map(([label, pat]) => (
                <button key={label} className="preset" type="button"
                  title={pat} onClick={() => onChange({ regex: pat })}>{label}</button>
              ))}
            </div>
          </div>

          {/* CONFIDENTIALITY */}
          <div className="editor-section-h">Confidentialité</div>
          <div className="frow full">
            <label>Clé de chiffrement
              <span style={{ color: "var(--ink-faint)" }}> — la colonne n'est plus jamais stockée ni affichée en clair pour qui ne détient pas la clé</span>
            </label>
            {!keysAvailable ? (
              <div className="csub">Aucune clé maîtresse configurée sur ce serveur — la confidentialité par colonne n'est pas disponible.</div>
            ) : (
              <select value={field.sensitive ?? ""} onChange={(e) => onChange({ sensitive: e.target.value || null })}>
                <option value="">Aucune</option>
                {availableKeys.map((k) => <option key={k.name} value={k.name}>{k.label || k.name}</option>)}
              </select>
            )}
          </div>

          {/* MAPPING */}
          <div className="editor-section-h">Correspondance TCO</div>
          <div className="frow full">
            <label>Mode</label>
            <select
              value={field.tco_replace ? "replace" : (field.tco_mapping ? "validate" : "off")}
              onChange={(e) => {
                const m = e.target.value;
                if (m === "off") onChange({ tco_replace: false, tco_mapping: null });
                else if (m === "replace") onChange({ tco_replace: true, tco_mapping: null });
                else onChange({ tco_replace: false });   // validate
              }}>
              <option value="off">Désactivé</option>
              <option value="validate">Valider — chaque valeur doit correspondre à une étiquette</option>
              <option value="replace">Remplacer — échanger chaque valeur contre son étiquette</option>
            </select>
          </div>

          {(field.tco_replace || field.tco_mapping) && (
            <div className="frow full">
              <label>Type (colonne TYPE de la table)
                <span style={{ color: "var(--ink-faint)" }}> — restreint la recherche aux lignes de ce type ; laisser vide pour chercher dans toute la table</span>
              </label>
              <input type="text" className="mono-input" placeholder="ex. generic_job"
                value={field.tco_type ?? ""}
                onChange={(e) => onChange({ tco_type: e.target.value || null })} />
            </div>
          )}

          {field.tco_replace ? (
            <div className="csub" style={{ marginTop: 2 }}>
              Chaque valeur est recherchée dans la table de référence et remplacée par son <code>TARGET_LABEL</code>
              (ex. <code>M → MASCULIN</code>). Les valeurs inconnues sont conservées telles quelles et signalées —
              elles apparaissent dans la couverture TCO du rapport pour compléter la table.
            </div>
          ) : (
            <div className="frow full">
              <label>Étiquette attendue
                <span style={{ color: "var(--ink-faint)" }}> — vérifie chaque valeur par rapport à la table de référence</span>
              </label>
              {tcoLabels.length > 0 ? (
                <select value={field.tco_mapping ?? ""} onChange={(e) => onChange({ tco_mapping: e.target.value || null })}>
                  <option value="">Aucune correspondance</option>
                  {tcoLabels.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              ) : (
                <input type="text" className="mono-input" placeholder="Charge d'abord un fichier TCO, ou saisis une étiquette"
                  value={field.tco_mapping ?? ""}
                  onChange={(e) => onChange({ tco_mapping: e.target.value || null })} />
              )}
            </div>
          )}
        </div>

        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--line)", fontSize: 12, color: "var(--ink-faint)" }}>
          Validé comme <span className="tchip string" style={{ fontSize: 10 }}>{finalName}</span>
        </div>
      </div>
    </div>
  );
}
