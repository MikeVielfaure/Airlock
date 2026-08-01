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
    if (!yaml.trim()) { notify("Rien à enregistrer — construisez d'abord une config.", "err"); return; }
    try {
      if (saveTarget) {
        const a = await api.addArtefactVersion("config", saveTarget, { yaml });
        notify(`Enregistré comme version ${a.latest_version_no} de « ${a.name} ».`, "ok");
      } else {
        if (!saveName.trim()) { notify("Donnez un nom à la config.", "err"); return; }
        await api.createArtefact("config", { name: saveName.trim(), yaml });
        notify(`Config « ${saveName.trim() } » enregistrée dans la bibliothèque.`, "ok");
        setSaveName("");
      }
      refreshLib();
    } catch (e) { notify(e instanceof Error ? e.message : "Échec de l'enregistrement.", "err"); }
  };

  const loadFromLibrary = async (a: ArtefactInfo) => {
    try {
      const v = await api.getConfigYaml(a.id, a.latest_version_no);
      await onImportYaml(v.yaml);
      notify(`Config « ${a.name} » (v${a.latest_version_no}) chargée.`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : "Échec du chargement.", "err"); }
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
        <span className="sub">Les règles déclaratives ci-dessus, en YAML portable.</span>
      </div>
      <div className="yaml-actions">
        <button className="btn sm" onClick={onCopy}><IconCopy size={14} /> Copier</button>
        <button className="btn sm" onClick={download}><IconDownload size={14} /> Télécharger .yaml</button>
      </div>
      {generating ? (
        <div className="banner"><span>Génération…</span></div>
      ) : (
        <pre className="yaml" dangerouslySetInnerHTML={{ __html: highlighted }} />
      )}

      <div className="sec-h" style={{ marginTop: 22 }}>
        <h3>Bibliothèque</h3>
        <span className="sub">Enregistre cette config côté serveur, versionnée — les flux peuvent ensuite l'exécuter par son id.</span>
      </div>
      <div className="flowform">
        <div className="frow"><label>Enregistrer sous</label>
          <select value={saveTarget} onChange={(e) => setSaveTarget(e.target.value)}>
            <option value="">nouvelle config…</option>
            {lib.map((a) => <option key={a.id} value={a.id}>nouvelle version de « {a.name} » (v{a.latest_version_no})</option>)}
          </select></div>
        {saveTarget === "" && (
          <div className="frow"><label>Nom</label>
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="ex. clients-fournisseur-x" /></div>
        )}
        <button className="btn primary" onClick={saveToLibrary}>Enregistrer dans la bibliothèque</button>
      </div>
      {lib.length > 0 && (
        <div className="libcol" style={{ marginTop: 10 }}>
          <div className="libcol-h">Configs enregistrées</div>
          {lib.map((a) => (
            <div key={a.id} className="libitem">
              <span>{a.name} <span className="csub">v{a.latest_version_no}</span></span>
              <button className="btn sm" onClick={() => loadFromLibrary(a)}>Charger</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
