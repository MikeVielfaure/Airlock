import type { FieldConfig, FieldType, Presets } from "../lib/types";

interface Props {
  col: string;
  field: FieldConfig;
  presets: Presets;
  tcoLabels: string[];
  configFields: Record<string, FieldConfig>;
  onChange: (patch: Partial<FieldConfig>) => void;
}

const CASE_LABELS: Record<string, string> = {
  upper: "UPPER", lower: "lower", title: "Title", "": "As-is",
};

// Insertable helpers for the dynamic "Match column by" field.
const NAME_TOKENS = ["MOIS", "MOIS2", "MOIS_NOM", "MOIS_COURT", "JOUR", "JOUR_NOM", "ANNEE", "DATENOW"];
const NAME_FUNCS = ["LEFT(", "RIGHT(", "SUBSTRING(", "CONCAT(", "UPPER(", "LOWER(", "REPLACE("];

export function FieldEditor({ col, field, presets, tcoLabels, configFields, onChange }: Props) {
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
          <div className="editor-section-h">Identity</div>

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
            <label>Rename to / match a config field <span style={{ color: "var(--ink-faint)" }}>(optional)</span></label>
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
                  ↧ Inherit rules from config field “{field.mapping}”
                </button>
                <span style={{ color: "var(--ink-faint)", fontSize: 11 }}>type {cfMatch.type}
                  {cfMatch.regex ? " · regex" : ""}{cfMatch.length != null ? ` · len ${cfMatch.length}` : ""}
                  {cfMatch.tco_mapping ? " · TCO" : ""}</span>
              </div>
            )}
            {field.mapping && (
              <label className="check" style={{ marginTop: 6 }}>
                <input type="checkbox" checked={field.rename_output}
                  onChange={(e) => onChange({ rename_output: e.target.checked })} />
                <span className="ctxt">Show this name in the table & export
                  <div className="csub">Off: rules apply but the column keeps its original name “{col}”.</div>
                </span>
              </label>
            )}
          </div>

          <div className="frow full">
            <label>Match column by <span style={{ color: "var(--ink-faint)" }}>(advanced — name can vary)</span></label>
            <input
              type="text" className="mono-input"
              value={(field.name ?? []).join(", ")}
              placeholder={col}
              onChange={(e) => onChange({ name: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
            />
            <div className="namehelp">
              <div className="namehelp-row">
                <span className="chiplabel">tags:</span>
                {NAME_TOKENS.map((t) => (
                  <button key={t} type="button" className="microchip"
                    onClick={() => appendName(`[${t}]`)}>[{t}]</button>
                ))}
              </div>
              <div className="namehelp-row">
                <span className="chiplabel">functions:</span>
                {NAME_FUNCS.map((f) => (
                  <button key={f} type="button" className="microchip"
                    onClick={() => appendName((field.name?.length ? "" : "=") + f)} title="prefix the whole name with = to use functions">{f}</button>
                ))}
              </div>
              <div className="csub">
                Binds this field to a file column. Use a tag for a name that changes (e.g. <code>[MOIS_COURT]</code>),
                or start with <code>=</code> for a computed name (e.g. <code>=LEFT([MOIS_NOM], 4)</code>).
              </div>
            </div>
          </div>

          <label className="check full">
            <input
              type="checkbox" checked={field.identifiant}
              onChange={(e) => onChange({ identifiant: e.target.checked })}
            />
            <span className="ctxt">Use as row identifier
              <div className="csub">This column labels rows in the report instead of the row number.</div>
            </span>
          </label>

          {/* CLEANING */}
          <div className="editor-section-h">Cleaning</div>

          <label className="check">
            <input type="checkbox" checked={field.trim} onChange={(e) => onChange({ trim: e.target.checked })} />
            <span className="ctxt">Trim whitespace</span>
          </label>

          <div className="frow">
            <label>Normalize case</label>
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
            <label>Strip character <span style={{ color: "var(--ink-faint)" }}>(e.g. €, spaces)</span></label>
            <input
              type="text" className="mono-input" maxLength={3}
              value={field.delimiteur ?? ""}
              onChange={(e) => onChange({ delimiteur: e.target.value || null })}
            />
          </div>

          {isNum && (
            <div className="frow">
              <label>Number formatting</label>
              <div style={{ display: "flex", gap: 14, paddingTop: 4 }}>
                <label className="check" style={{ padding: 0 }}>
                  <input type="checkbox" checked={field.separator_mile}
                    onChange={(e) => onChange({ separator_mile: e.target.checked })} />
                  <span className="ctxt">Strip thousands</span>
                </label>
                <label className="check" style={{ padding: 0 }}>
                  <input type="checkbox" checked={field.separator_decimal}
                    onChange={(e) => onChange({ separator_decimal: e.target.checked })} />
                  <span className="ctxt">Decimal → "."</span>
                </label>
              </div>
            </div>
          )}

          {isDate && (
            <>
              <label className="check full">
                <input type="checkbox" checked={field.auto_date_format}
                  onChange={(e) => onChange({ auto_date_format: e.target.checked })} />
                <span className="ctxt">Detect source format automatically
                  <div className="csub">Uses a day/month heuristic when the order is ambiguous.</div>
                </span>
              </label>
              {!field.auto_date_format && (
                <div className="frow">
                  <label>Source format</label>
                  <select value={field.format ?? ""} onChange={(e) => onChange({ format: e.target.value || null })}>
                    <option value="">—</option>
                    {presets.date_formats.map((f) => <option key={f} value={f}>{f}</option>)}
                  </select>
                </div>
              )}
              <div className="frow">
                <label>Target format</label>
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
            <span className="ctxt">Required (no empty values)</span>
          </label>

          <label className="check">
            <input type="checkbox" checked={field.check_type}
              onChange={(e) => onChange({ check_type: e.target.checked })} />
            <span className="ctxt">Enforce type ({field.type})</span>
          </label>

          <div className="frow">
            <label>Max length</label>
            <input type="number" min={0} placeholder="—"
              value={field.length ?? ""}
              onChange={(e) => onChange({ length: e.target.value ? Number(e.target.value) : null })} />
          </div>

          <div className="frow">
            <label>Allowed values <span style={{ color: "var(--ink-faint)" }}>(comma-separated)</span></label>
            <input type="text" className="mono-input" placeholder="M, F, X"
              value={(field.on_list ?? []).join(", ")}
              onChange={(e) => {
                const v = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
                onChange({ on_list: v.length ? v : null });
              }} />
          </div>

          <div className="frow full">
            <label>Regex pattern</label>
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

          {/* MAPPING */}
          <div className="editor-section-h">TCO mapping</div>
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
              <option value="off">Off</option>
              <option value="validate">Validate — each value must match a label</option>
              <option value="replace">Replace — swap each value for its label</option>
            </select>
          </div>

          {field.tco_replace ? (
            <div className="csub" style={{ marginTop: 2 }}>
              Each value is looked up in the reference table and replaced by its <code>TARGET_LABEL</code>
              (e.g. <code>M → MASCULIN</code>). Unknown values are kept as-is and flagged — they show up in
              the report’s TCO coverage so you can complete the table.
            </div>
          ) : (
            <div className="frow full">
              <label>Expected label
                <span style={{ color: "var(--ink-faint)" }}> — checks each value against the reference table</span>
              </label>
              {tcoLabels.length > 0 ? (
                <select value={field.tco_mapping ?? ""} onChange={(e) => onChange({ tco_mapping: e.target.value || null })}>
                  <option value="">No mapping</option>
                  {tcoLabels.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              ) : (
                <input type="text" className="mono-input" placeholder="Load a TCO file first, or type a label"
                  value={field.tco_mapping ?? ""}
                  onChange={(e) => onChange({ tco_mapping: e.target.value || null })} />
              )}
            </div>
          )}
        </div>

        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--line)", fontSize: 12, color: "var(--ink-faint)" }}>
          Validates as <span className="tchip string" style={{ fontSize: 10 }}>{finalName}</span>
        </div>
      </div>
    </div>
  );
}
