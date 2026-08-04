import { useState } from "react";
import type { EdiModelRef } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";

/**
 * A model from the library, or pasted inline — shared between EdiPanel
 * (upload-based EDI operations) and any other place that needs to point at
 * an EDI model, so the picker only exists once.
 */
export function useModelRef(models: ArtefactInfo[]) {
  const [id, setId] = useState("");
  const [pinned, setPinned] = useState(false);
  const [yaml, setYaml] = useState("");
  const [inline, setInline] = useState(false);
  const ref = (): EdiModelRef | null => {
    if (inline) return yaml.trim() ? { yaml } : null;
    if (!id) return null;
    const m = models.find((a) => a.id === id);
    return { id, version: pinned && m ? m.latest_version_no : null };
  };
  return { id, setId, pinned, setPinned, yaml, setYaml, inline, setInline, ref };
}

export function ModelPicker({ label, models, ctl }: {
  label: string; models: ArtefactInfo[]; ctl: ReturnType<typeof useModelRef>;
}) {
  return (
    <div className="edi-field">
      <label>{label}</label>
      <div className="edi-row">
        <select value={ctl.inline ? "__inline" : ctl.id}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__inline") { ctl.setInline(true); return; }
                  ctl.setInline(false); ctl.setId(v);
                }}>
          <option value="">— choisir un modèle —</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.name} (v{m.latest_version_no})</option>
          ))}
          <option value="__inline">✎ coller le YAML directement…</option>
        </select>
        {!ctl.inline && ctl.id && (
          <label className="edi-check" title="Figer la version au lieu de suivre la dernière">
            <input type="checkbox" checked={ctl.pinned}
                   onChange={(e) => ctl.setPinned(e.target.checked)} /> figer
          </label>
        )}
      </div>
      {ctl.inline && (
        <textarea className="mono" rows={8} value={ctl.yaml} spellCheck={false}
                  placeholder="name: …&#10;message_type: ORDERS&#10;header: …"
                  onChange={(e) => ctl.setYaml(e.target.value)} />
      )}
    </div>
  );
}
