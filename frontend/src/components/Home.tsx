import type { AuthUser, EnvProfile } from "../lib/types";
import {
  IconCheck, IconCode, IconGrid, IconLayers, IconList, IconPlay, IconTable,
} from "../lib/icons";

interface Props {
  me: AuthUser | null;
  env: string;
  profile: EnvProfile | null;
  /** Which modules this environment exposes. */
  shows: (module: string) => boolean;
  go: (tab: string) => void;
}

const MODULES: {
  key: string; label: string; blurb: string; icon: JSX.Element; primary?: boolean;
}[] = [
  { key: "schema", label: "Contrôler un fichier", primary: true,
    blurb: "Déposer un CSV ou un XLSX, le contrôler contre une configuration, "
         + "corriger, puis exporter ou enregistrer en base.",
    icon: <IconList size={20} /> },
  { key: "datasets", label: "Tables de données",
    blurb: "Consulter les tables enregistrées, y écrire le résultat d'un "
         + "contrôle, gérer qui peut les lire et les alimenter.",
    icon: <IconTable size={20} /> },
  { key: "edi", label: "EDI",
    blurb: "Décoder un fichier EDIFACT, le contrôler contre un modèle, "
         + "le mettre à plat ou le régénérer.",
    icon: <IconGrid size={20} /> },
  { key: "canvas", label: "Studio Flux",
    blurb: "Assembler des briques : lire une source, transformer, contrôler, "
         + "notifier (Email/HTTP), écrire — et appeler le tout comme une API.",
    icon: <IconLayers size={20} /> },
  { key: "mapping", label: "Correspondances",
    blurb: "Relier les champs d'une source aux champs canoniques, avec "
         + "expressions et contraintes.",
    icon: <IconCode size={20} /> },
  { key: "ops", label: "Exploitation & Audit",
    blurb: "Ce qui a tourné, ce qui a échoué, les logs — et rejouer une "
         + "exécution à l'identique.",
    icon: <IconPlay size={20} /> },
  { key: "functions", label: "Fonctions",
    blurb: "Des expressions nommées, réutilisables partout où un calcul est "
         + "possible.",
    icon: <IconCheck size={20} /> },
];

export function Home({ me, env, profile, shows, go }: Props) {
  const available = MODULES.filter((m) => shows(m.key));
  const role = me?.environments?.[env];

  return (
    <div className="home">
      <div className="home-head">
        <h1>{profile?.label && profile.label !== env ? profile.label : "Bienvenue dans Airlock"}</h1>
        <p>
          Environnement <strong>{env}</strong>
          {role && <> · Rôle : <strong>{role}</strong></>}
          {profile?.config_locked && <> · Configuration imposée</>}
        </p>
        {profile?.description && <p className="home-desc">{profile.description}</p>}
      </div>

      {/* KPI Dashboard Cards */}
      <div className="home-kpi-grid">
        <div className="kpi-card">
          <span className="kpi-label">Moteur de Calcul</span>
          <strong className="kpi-val ok">DuckDB + Pandas</strong>
          <span className="kpi-sub">Cross-source SQL & Fenêtrage réactifs</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Sécurité des Ingestion</span>
          <strong className="kpi-val">Fail-Closed</strong>
          <span className="kpi-sub">Immuabilité & Taint tracking RGPD</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Modules Ouverts</span>
          <strong className="kpi-val">{available.length} / {MODULES.length}</strong>
          <span className="kpi-sub">Portée d'environnement active</span>
        </div>
      </div>

      {available.length === 0 ? (
        <p className="home-empty">
          Aucun module n'est ouvert dans cet environnement. Demandez à votre
          administrateur d'en ouvrir au moins un.
        </p>
      ) : (
        <div className="home-cards">
          {available.map((m) => (
            <button key={m.key} className={`home-card ${m.primary ? "first" : ""}`}
                    onClick={() => go(m.key)}>
              <span className="home-icon">{m.icon}</span>
              <strong>{m.label}</strong>
              <span className="home-blurb">{m.blurb}</span>
            </button>
          ))}
        </div>
      )}

      {(me?.is_superadmin || me?.setup_mode) && (
        <div className="home-admin">
          <button className="btn" onClick={() => go("admin")}>
            <IconLayers size={15} /> Administration du système
          </button>
          <span className="home-blurb">
            Environnements, comptes, rôles, modules, SSO — et un bac à sable pour
            essayer un rôle avant de le confier.
          </span>
        </div>
      )}
    </div>
  );
}
