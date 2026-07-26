import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ArtefactInfo } from "../lib/types";
import { IconCopy, IconDownload } from "../lib/icons";

interface Props {
  yaml: string;
  generating: boolean;
  onCopy: () => void;
  onImportYaml: (text: string) => void | Promise<void>;
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
}

export function YamlPanel({ yaml, generating, onCopy, onImportYaml, notify }: Props) {
  const [lib, setLib] = useState<ArtefactInfo[]>([]);
  const [saveName, setSaveName] = useState("");
  const [saveTarget, setSaveTarget] = useState("");   // "" = new artefact, else new version of id
  const refreshLib = useCallback(() => { api.listArtefacts("config").then(setLib).catch(() => {}); }, []);
  useEffect(() => { refreshLib(); }, [refreshLib]);

  const saveToLibrary = async () => {
    if (!yaml.trim()) { notify("Nothing to save — build a config first.", "err"); return; }
    try {
      if (saveTarget) {
        const a = await api.addArtefactVersion("config", saveTarget, { yaml });
        notify(`Saved as version ${a.latest_version_no} of « ${a.name} ».`, "ok");
      } else {
        if (!saveName.trim()) { notify("Give the config a name.", "err"); return; }
        await api.createArtefact("config", { name: saveName.trim(), yaml });
        notify(`Config « ${saveName.trim() } » saved to the library.`, "ok");
        setSaveName("");
      }
      refreshLib();
    } catch (e) { notify(e instanceof Error ? e.message : "Save failed.", "err"); }
  };

  const loadFromLibrary = async (a: ArtefactInfo) => {
    try {
      const v = await api.getConfigYaml(a.id, a.latest_version_no);
      await onImportYaml(v.yaml);
      notify(`Config « ${a.name} » (v${a.latest_version_no}) loaded.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Load failed.", "err"); }
  };
  const [highlighted, setHighlighted] = useState("");

  useEffect(() => {
    // Lightweight key highlight — keys at line start get the accent color.
    const html = yaml
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/^(\s*)([\w-]+)(:)/gm, '$1<span class="k">$2</span>$3');
    setHighlighted(html);
  }, [yaml]);

  const download = () => {
    const blob = new Blob([yaml], { type: "text/yaml;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "file-config.yaml";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      <div className="sec-h">
        <h3>Configuration</h3>
        <span className="sub">The declarative rules above, as portable YAML.</span>
      </div>
      <div className="yaml-actions">
        <button className="btn sm" onClick={onCopy}><IconCopy size={14} /> Copy</button>
        <button className="btn sm" onClick={download}><IconDownload size={14} /> Download .yaml</button>
      </div>
      {generating ? (
        <div className="banner"><span>Generating…</span></div>
      ) : (
        <pre className="yaml" dangerouslySetInnerHTML={{ __html: highlighted }} />
      )}

      <div className="sec-h" style={{ marginTop: 22 }}>
        <h3>Library</h3>
        <span className="sub">Store this config server-side, versioned — flows can then run it by id.</span>
      </div>
      <div className="flowform">
        <div className="frow"><label>Save as</label>
          <select value={saveTarget} onChange={(e) => setSaveTarget(e.target.value)}>
            <option value="">new config…</option>
            {lib.map((a) => <option key={a.id} value={a.id}>new version of « {a.name} » (v{a.latest_version_no})</option>)}
          </select></div>
        {saveTarget === "" && (
          <div className="frow"><label>Name</label>
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="e.g. clients-fournisseur-x" /></div>
        )}
        <button className="btn primary" onClick={saveToLibrary}>Save to library</button>
      </div>
      {lib.length > 0 && (
        <div className="libcol" style={{ marginTop: 10 }}>
          <div className="libcol-h">Stored configs</div>
          {lib.map((a) => (
            <div key={a.id} className="libitem">
              <span>{a.name} <span className="csub">v{a.latest_version_no}</span></span>
              <button className="btn sm" onClick={() => loadFromLibrary(a)}>Load</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
