import { Fragment, useCallback, useEffect, useState } from "react";
import { api, setToken } from "../lib/api";
import type { AuthUser, EnvProfile } from "../lib/types";
import {
  IconCheck, IconCode, IconLayers, IconPlay, IconReset, IconSave, IconTable, IconWarn,
} from "../lib/icons";
import { InfoTip } from "./InfoTip";

interface Props {
  me: AuthUser | null;
  notify: (m: string, k?: "ok" | "err" | "info") => void;
  /** Re-read the identity after borrowing one, so the whole app follows. */
  onIdentityChange: () => void;
}

type Tab = "overview" | "sandbox" | "users" | "envs" | "sso";

const ROLES = ["viewer", "operator", "editor", "admin"];

interface Member { user_id: string; email: string; display_name: string;
                   role: string; from_sso: boolean }
interface UserRow { id: string; email: string; display_name: string;
                    is_superadmin: boolean; active: boolean;
                    environments: Record<string, string>; sso: string[];
                    last_login_at?: string }

/**
 * Administration, and a sandbox for trying a role design out.
 *
 * Designing roles blind is how a team ends up with an operator who cannot do
 * their job. So the first tab is deliberately the sandbox: conjure an account,
 * give it a role, borrow its identity, look. Nothing here is a separate
 * mechanism — a test user is a real user, the sandbox is just the short path.
 */
export function AdminPanel({ me, notify, onIdentityChange }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [envs, setEnvs] = useState<string[]>([]);

  const refreshEnvs = useCallback(async () => {
    try { setEnvs((await api.listEnvironments()).environments); } catch { /* ignore */ }
  }, []);
  useEffect(() => { refreshEnvs(); }, [refreshEnvs]);

  if (!me?.is_superadmin && !me?.setup_mode) {
    return (
      <p className="ad-hint">
        <IconWarn size={14} /> Cette section est réservée à l'administrateur général.
      </p>
    );
  }

  return (
    <div className="ad">
      <nav className="ad-tabs">
        {([["overview", "Aperçu"], ["sandbox", "Bac à sable"],
           ["users", "Utilisateurs"], ["envs", "Environnements & modules"],
           ["sso", "SSO"]] as [Tab, string][])
          .map(([k, label]) => (
            <button key={k} className={`tab ${tab === k ? "active" : ""}`}
                    onClick={() => setTab(k)}>{label}</button>
          ))}
      </nav>

      {tab === "overview" && <Overview notify={notify} />}
      {tab === "sandbox" && <Sandbox envs={envs} notify={notify}
                                    onIdentityChange={onIdentityChange} />}
      {tab === "users" && <Users envs={envs} notify={notify} />}
      {tab === "envs" && <Envs envs={envs} notify={notify} refreshEnvs={refreshEnvs}
                              onIdentityChange={onIdentityChange} />}
      {tab === "sso" && <Sso envs={envs} notify={notify} />}
    </div>
  );
}

/* ── sandbox ─────────────────────────────────────────────────────── */
function Sandbox({ envs, notify, onIdentityChange }: {
  envs: string[]; notify: Props["notify"]; onIdentityChange: () => void;
}) {
  const [email, setEmail] = useState("essai@test.local");
  const [env, setEnv] = useState("default");
  const [role, setRole] = useState("operator");
  const [made, setMade] = useState<{ email: string; environments: Record<string, string> }[]>([]);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const r = await api.quickUser(email, { [env]: role });
      setMade((m) => [{ email: r.email, environments: r.environments },
                      ...m.filter((x) => x.email !== r.email)]);
      notify(`« ${r.email} » prêt — ${env} : ${role}. Mot de passe : motdepasse1`, "ok");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
    finally { setBusy(false); }
  };

  const borrow = async (mail: string) => {
    try {
      const r = await api.impersonate(mail);
      setToken(r.token);
      onIdentityChange();
      notify(`Vous voyez l'application comme ${mail}. Un bandeau le rappelle.`, "info");
    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  return (
    <div className="ad-body">
      <p className="ad-hint">
        Concevoir des rôles à l'aveugle, c'est se retrouver avec un opérateur qui
        ne peut pas travailler. Ici : on fabrique un compte, on lui donne un rôle,
        on emprunte son identité, on regarde. Un compte d'essai est un vrai
        compte — c'est seulement le chemin qui est court.
        <InfoTip>
          <p><b>À quoi ça sert</b> — créer un compte réel, avec mot de passe, rôle et environnement en une seule fois — pas besoin de préparer l'environnement avant, il se crée de fait par l'appartenance.</p>
          <p><b>Comment faire</b> — email, mot de passe (8 caractères min., « motdepasse1 » par défaut), environnement et rôle, puis « Créer / mettre à jour ». Le même email réutilisé met simplement à jour ce compte.</p>
          <p><b>Ce qu'il faut</b> — être administrateur général pour utiliser ce raccourci.</p>
        </InfoTip>
      </p>

      <div className="ad-form">
        <input value={email} onChange={(e) => setEmail(e.target.value)}
               placeholder="email du compte d'essai" />
        <input value={env} onChange={(e) => setEnv(e.target.value)}
               placeholder="environnement" list="ad-envs" />
        <datalist id="ad-envs">{envs.map((e) => <option key={e} value={e} />)}</datalist>
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button className="btn" disabled={busy || !email.includes("@")} onClick={create}>
          <IconPlay size={14} /> Créer / mettre à jour
        </button>
      </div>
      <p className="ad-note">
        Un environnement qui n'existe pas encore est créé de fait par
        l'appartenance : rien à préparer pour essayer.
      </p>

      {made.length > 0 && (
        <>
          <h4><IconLayers size={13} /> Comptes d'essai de cette session</h4>
          {made.map((u) => (
            <div key={u.email} className="ad-row">
              <strong>{u.email}</strong>
              <span className="ad-chips">
                {Object.entries(u.environments).map(([e, r]) => (
                  <code key={e}>{e} : {r}</code>
                ))}
              </span>
              <button className="btn sm" onClick={() => borrow(u.email)}>
                Prendre son identité
              </button>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/* ── users & roles ───────────────────────────────────────────────── */
function Users({ envs, notify }: { envs: string[]; notify: Props["notify"] }) {
  const [rows, setRows] = useState<UserRow[]>([]);
  const [env, setEnv] = useState(envs[0] || "default");
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("operator");
  const [pwEdits, setPwEdits] = useState<Record<string, string>>({});
  const [delConfirm, setDelConfirm] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try { setRows(await api.listUsers()); } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "err");
    }
  }, [notify]);
  const refreshMembers = useCallback(async () => {
    if (!env) return;
    try { setMembers(await api.envMembers(env)); } catch { setMembers([]); }
  }, [env]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { refreshMembers(); }, [refreshMembers]);
  useEffect(() => { if (!env && envs.length) setEnv(envs[0]); }, [envs]); // eslint-disable-line

  return (
    <div className="ad-body">
      <h4><IconTable size={13} /> Comptes <span className="count">{rows.length}</span></h4>
      <table className="ad-table">
        <thead><tr><th>Compte</th><th>Environnements</th><th>SSO</th><th /></tr></thead>
        <tbody>
          {rows.map((u) => (
            <tr key={u.id}>
              <td>
                <strong>{u.email}</strong>
                {u.is_superadmin && <span className="ad-tag">admin général</span>}
                {!u.active && <span className="ad-tag danger">désactivé</span>}
              </td>
              <td className="ad-chips">
                {Object.entries(u.environments).map(([e, r]) => (
                  <code key={e}>{e} : {r}</code>
                ))}
                {Object.keys(u.environments).length === 0 && <em>aucun</em>}
              </td>
              <td>{u.sso.join(", ") || "—"}</td>
              <td className="ad-chips">
                {!u.is_superadmin && (
                  <button className="btn sm" onClick={async () => {
                    try {
                      const r = await api.impersonate(u.email);
                      setToken(r.token);
                      window.location.reload();
                    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>Voir comme</button>
                )}
                <input type="password" placeholder="nouveau mot de passe" style={{ width: 150 }}
                       value={pwEdits[u.id] ?? ""}
                       onChange={(e) => setPwEdits({ ...pwEdits, [u.id]: e.target.value })} />
                <button className="btn sm" disabled={(pwEdits[u.id] ?? "").length < 8}
                        onClick={async () => {
                          try {
                            await api.setUserPassword(u.id, pwEdits[u.id]);
                            setPwEdits({ ...pwEdits, [u.id]: "" });
                            notify(`Mot de passe changé pour ${u.email}.`, "ok");
                          } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                        }}>Changer le mot de passe</button>
                <div style={{ flexBasis: "100%", height: 0 }} />
                {u.active ? (
                  <button className="btn sm" onClick={async () => {
                    try {
                      await api.deactivateUser(u.id);
                      await refresh();
                      notify(`Compte « ${u.email} » désactivé.`, "ok");
                    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>Désactiver</button>
                ) : (
                  <button className="btn sm" onClick={async () => {
                    try {
                      await api.reactivateUser(u.id);
                      await refresh();
                      notify(`Compte « ${u.email} » réactivé.`, "ok");
                    } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                  }}>Réactiver</button>
                )}
                <input placeholder="email pour confirmer" title={`Tapez « ${u.email} » pour confirmer la suppression.`}
                       style={{ width: 170 }} value={delConfirm[u.id] ?? ""}
                       onChange={(e) => setDelConfirm({ ...delConfirm, [u.id]: e.target.value })} />
                <button className="btn sm danger" disabled={delConfirm[u.id] !== u.email}
                        onClick={async () => {
                          try {
                            await api.deleteUser(u.id, delConfirm[u.id]);
                            setDelConfirm({ ...delConfirm, [u.id]: "" });
                            await refresh();
                            notify(`Compte « ${u.email} » supprimé.`, "ok");
                          } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                        }}>Supprimer</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4><IconCheck size={13} /> Rôles dans un environnement</h4>
      <div className="ad-form">
        <select value={env} onChange={(e) => setEnv(e.target.value)}>
          {envs.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
        <input value={email} onChange={(e) => setEmail(e.target.value)}
               placeholder="email" list="ad-users" />
        <datalist id="ad-users">{rows.map((u) => <option key={u.id} value={u.email} />)}</datalist>
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button className="btn sm" disabled={!email} onClick={async () => {
          try {
            await api.setMember(env, email, role);
            await refreshMembers(); await refresh();
            notify(`${email} : ${role} dans ${env}.`, "ok");
          } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
        }}><IconSave size={12} /> Appliquer</button>
      </div>

      <table className="ad-table">
        <thead><tr><th>Membre</th><th>Rôle</th><th>Origine</th></tr></thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.email}</td>
              <td><span className="ad-badge">{m.role}</span></td>
              <td>{m.from_sso
                ? <span className="ad-note">annuaire — à changer dans l'IdP</span>
                : "manuel"}</td>
            </tr>
          ))}
          {members.length === 0 && (
            <tr><td colSpan={3} className="ad-note">Aucun membre dans « {env} ».</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ── environments, profiles, module list ─────────────────────────── */
interface EnvContent {
  artefacts: { id: string; kind: string; name: string; archived: boolean }[];
  datasets: { id: string; name: string; archived: boolean }[];
  keys: { id: string; name: string; active: boolean }[];
}

function Envs({ envs, notify, refreshEnvs, onIdentityChange }: {
  envs: string[]; notify: Props["notify"]; refreshEnvs: () => Promise<void>;
  onIdentityChange: () => void;
}) {
  const [templates, setTemplates] = useState<{ key: string; label: string;
                                               description: string; modules: string[];
                                               config_locked: boolean }[]>([]);
  const [allModules, setAllModules] = useState<string[]>([]);
  const [sel, setSel] = useState("");
  const [profile, setProfile] = useState<EnvProfile | null>(null);
  const [newName, setNewName] = useState("");
  const [tpl, setTpl] = useState("complet");

  // deletion
  const [content, setContent] = useState<EnvContent | null>(null);
  const [migrateArt, setMigrateArt] = useState<Set<string>>(new Set());
  const [migrateDs, setMigrateDs] = useState<Set<string>>(new Set());
  const [migrateKey, setMigrateKey] = useState<Set<string>>(new Set());
  const [targetEnv, setTargetEnv] = useState("default");
  const [mode, setMode] = useState<"profile_only" | "cascade">("profile_only");
  const [confirmName, setConfirmName] = useState("");

  // grants + library management (archive / restore / delete for good)
  const [grantSrc, setGrantSrc] = useState("default");
  const [srcContent, setSrcContent] = useState<EnvContent | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [picked, setPicked] = useState<{ kind: string; id: string; name: string; archived: boolean } | null>(null);
  const [pickedGrants, setPickedGrants] = useState<
    { environment: string; permission: string }[]>([]);
  const [grantTarget, setGrantTarget] = useState("");

  const refreshSrcContent = useCallback(() => {
    api.environmentContent(grantSrc, showArchived).then(setSrcContent).catch(() => setSrcContent(null));
  }, [grantSrc, showArchived]);

  useEffect(() => {
    api.envTemplates().then((r) => { setTemplates(r.templates); setAllModules(r.modules); })
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!sel) { setProfile(null); setContent(null); return; }
    api.envProfile(sel).then(setProfile).catch(() => setProfile(null));
    api.environmentContent(sel).then(setContent).catch(() => setContent(null));
    setMigrateArt(new Set()); setMigrateDs(new Set()); setMigrateKey(new Set());
    setConfirmName(""); setMode("profile_only");
  }, [sel]);
  useEffect(() => {
    refreshSrcContent();
    setPicked(null); setPickedGrants([]);
  }, [refreshSrcContent]);

  const toggleIn = (set: Set<string>, setSet: (s: Set<string>) => void, id: string) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSet(next);
  };

  const refreshPickedGrants = async (kind: string, id: string) => {
    try { setPickedGrants((await api.listArtefactGrants(kind, id)).grants); }
    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  };

  const toggle = (m: string) => {
    if (!profile) return;
    const has = profile.modules.includes(m);
    setProfile({ ...profile,
                 modules: has ? profile.modules.filter((x) => x !== m)
                              : [...profile.modules, m] });
  };

  return (
    <div className="ad-body">
      <h4><IconLayers size={13} /> Créer un environnement</h4>
      <div className="ad-form">
        <input value={newName} onChange={(e) => setNewName(e.target.value)}
               placeholder="nom (rh, adv, bac-a-sable…)" />
        <label className="ad-note" style={{ marginLeft: 4 }}>Modèle de départ</label>
        <select value={tpl} onChange={(e) => setTpl(e.target.value)}>
          {templates.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <button className="btn sm" disabled={!newName.trim()} onClick={async () => {
          try {
            await api.createEnvironment({ name: newName.trim(), template: tpl });
            await refreshEnvs();
            notify(`Environnement « ${newName} » créé.`, "ok");
            setNewName("");
          } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
        }}><IconPlay size={12} /> Créer</button>
      </div>
      {templates.filter((t) => t.key === tpl).map((t) => (
        <p key={t.key} className="ad-note">
          {t.description} — modules : {t.modules.join(", ")}
          {t.config_locked && " · configuration imposée (il faut en désigner une)"}
        </p>
      ))}

      <h4><IconCode size={13} /> Modules d'un environnement</h4>
      <div className="ad-form">
        <select value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value="">— choisir un environnement —</option>
          {envs.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
      </div>

      {profile && (
        <>
          <p className="ad-note">
            Ce qui n'est pas coché n'apparaît pas pour les personnes travaillant
            dans « {sel} ». Ce qui n'est pas affiché ne peut pas être cassé.
          </p>
          <div className="ad-modules">
            {allModules.map((m) => (
              <label key={m} className={`ad-mod ${profile.modules.includes(m) ? "on" : ""}`}>
                <input type="checkbox" checked={profile.modules.includes(m)}
                       onChange={() => toggle(m)} />
                {m}
              </label>
            ))}
          </div>
          <div className="ad-form">
            <label className="ad-check">
              <input type="checkbox" checked={profile.tco_editable}
                     onChange={(e) => setProfile({ ...profile, tco_editable: e.target.checked })} />
              table de correspondance modifiable
            </label>
            <label className="ad-check">
              Onglets ouverts max. par personne
              <input type="number" min={0} step={1} className="mono-input" style={{ width: 70, marginLeft: 6 }}
                     value={profile.max_open_tabs}
                     onChange={(e) => setProfile({ ...profile, max_open_tabs: Math.max(0, Number(e.target.value) || 0) })} />
              <span className="ad-note" style={{ margin: 0 }}>0 = illimité</span>
            </label>
            <button className="btn" onClick={async () => {
              try {
                const saved = await api.saveEnvProfile(sel, {
                  modules: profile.modules, tco_editable: profile.tco_editable,
                  max_open_tabs: profile.max_open_tabs });
                setProfile(saved);
                notify(`Profil de « ${sel} » enregistré.`, "ok");
                // The workshop reads its own environment's profile once, on
                // mount or on switch — a save here would otherwise sit
                // invisible until an unrelated reload happened to catch it.
                onIdentityChange();
              } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
            }}><IconSave size={14} /> Enregistrer</button>
            <button className="btn sm" onClick={async () => {
              try {
                await api.resetEnvProfile(sel);
                setProfile(await api.envProfile(sel));
                notify("Profil remis à zéro — tout s'affiche à nouveau.", "ok");
                onIdentityChange();
              } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
            }}><IconReset size={12} /> Tout réafficher</button>
          </div>
        </>
      )}

      {sel && content && (
        <>
          <h4><IconWarn size={13} /> Supprimer « {sel} »</h4>
          <p className="ad-note">
            Cochez ce qui doit être migré vers un autre environnement avant la
            suppression. Ce qui reste est soit détaché (l'environnement devient
            orphelin, ses données restent intactes), soit supprimé pour de bon.
          </p>
          {content.artefacts.length === 0 && content.datasets.length === 0
            && content.keys.length === 0 && (
            <p className="ad-note">Cet environnement ne possède aucune donnée.</p>
          )}
          {content.artefacts.length > 0 && (
            <div className="ad-chips">
              {content.artefacts.map((a) => (
                <label key={a.id} className="ad-check">
                  <input type="checkbox" checked={migrateArt.has(a.id)}
                         onChange={() => toggleIn(migrateArt, setMigrateArt, a.id)} />
                  <code>{a.kind}</code> {a.name}
                </label>
              ))}
            </div>
          )}
          {content.datasets.length > 0 && (
            <div className="ad-chips">
              {content.datasets.map((d) => (
                <label key={d.id} className="ad-check">
                  <input type="checkbox" checked={migrateDs.has(d.id)}
                         onChange={() => toggleIn(migrateDs, setMigrateDs, d.id)} />
                  <IconTable size={11} /> {d.name}
                </label>
              ))}
            </div>
          )}
          {content.keys.length > 0 && (
            <div className="ad-chips">
              {content.keys.map((k) => (
                <label key={k.id} className="ad-check">
                  <input type="checkbox" checked={migrateKey.has(k.id)}
                         onChange={() => toggleIn(migrateKey, setMigrateKey, k.id)} />
                  clé « {k.name} »
                </label>
              ))}
            </div>
          )}
          <div className="ad-form">
            <label className="ad-note">Migrer la sélection vers</label>
            <input value={targetEnv} onChange={(e) => setTargetEnv(e.target.value)}
                   placeholder="default" style={{ width: 120 }} />
            <label className="ad-check">
              <input type="radio" name="del-mode" checked={mode === "profile_only"}
                     onChange={() => setMode("profile_only")} />
              détacher seulement (le reste survit, orphelin)
            </label>
            <label className="ad-check">
              <input type="radio" name="del-mode" checked={mode === "cascade"}
                     onChange={() => setMode("cascade")} />
              supprimer en cascade (irréversible)
            </label>
          </div>
          {mode === "cascade" && (
            <div className="ad-form">
              <label className="ad-note">
                Tapez « {sel} » pour confirmer la suppression en cascade
              </label>
              <input value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
            </div>
          )}
          <div className="ad-form">
            <button className="btn sm danger"
                    disabled={mode === "cascade" && confirmName !== sel}
                    onClick={async () => {
              try {
                const res = await api.deleteEnvironment(sel, {
                  migrate_artefact_ids: [...migrateArt],
                  migrate_dataset_ids: [...migrateDs],
                  migrate_key_ids: [...migrateKey],
                  target_environment: targetEnv || "default",
                  mode, confirm_name: confirmName,
                });
                notify(`Environnement « ${res.deleted} » ${
                  res.mode === "cascade" ? "supprimé" : "détaché"}.`, "ok");
                setSel(""); setProfile(null); setContent(null);
                await refreshEnvs();
              } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
            }}><IconWarn size={12} /> Supprimer l'environnement</button>
          </div>
        </>
      )}

      <h4><IconLayers size={13} /> Bibliothèque &amp; droits d'accès</h4>
      <p className="ad-note">
        Les artefacts (configs, TCO, graphes, fonctions…) et les tables d'un
        environnement : partager un artefact en lecture avec un autre,
        archiver — invisible partout, récupérable — ou supprimer pour de bon,
        réservé à ce qui est déjà archivé. Un nom archivé peut être repris
        immédiatement par un nouvel artefact ou une nouvelle table.
      </p>
      <div className="ad-form">
        <label className="ad-note">Environnement source</label>
        <input value={grantSrc} onChange={(e) => setGrantSrc(e.target.value)}
               placeholder="default" style={{ width: 120 }} />
        <label className="ad-check">
          <input type="checkbox" checked={showArchived}
                 onChange={(e) => setShowArchived(e.target.checked)} />
          afficher les archivés
        </label>
      </div>
      {srcContent && (
        <div className="ad-chips">
          {srcContent.artefacts.map((a) => (
            <button key={a.id} className={`btn sm ${picked?.id === a.id ? "active" : ""}`}
                    onClick={() => { setPicked({ kind: a.kind, id: a.id, name: a.name, archived: a.archived });
                                     refreshPickedGrants(a.kind, a.id); }}>
              <code>{a.kind}</code> {a.name}{a.archived ? " · archivé" : ""}
            </button>
          ))}
          {srcContent.datasets.map((d) => (
            <button key={d.id} className={`btn sm ${picked?.id === d.id ? "active" : ""}`}
                    onClick={() => setPicked({ kind: "dataset", id: d.id, name: d.name, archived: d.archived })}>
              <IconTable size={11} /> {d.name}{d.archived ? " · archivé" : ""}
            </button>
          ))}
          {srcContent.artefacts.length === 0 && srcContent.datasets.length === 0 && (
            <em className="ad-note">« {grantSrc} » ne possède rien{showArchived ? "" : " d'actif"}.</em>
          )}
        </div>
      )}
      {picked && (
        <>
          <div className="ad-form">
            <span className="ad-note">
              « {picked.name} »{picked.archived ? " (archivé)" : ""}
            </span>
            {picked.archived ? (
              <>
                <button className="btn sm" onClick={async () => {
                  try {
                    if (picked.kind === "dataset") await api.restoreDataset(picked.id);
                    else await api.restoreArtefact(picked.kind, picked.id);
                    notify(`« ${picked.name} » restauré.`, "ok");
                    setPicked({ ...picked, archived: false });
                    refreshSrcContent();
                  } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                }}><IconReset size={12} /> Restaurer</button>
                <button className="btn sm danger" onClick={async () => {
                  if (!window.confirm(`Supprimer « ${picked.name} » pour de bon ? Irréversible.`)) return;
                  try {
                    if (picked.kind === "dataset") await api.deleteDatasetPermanently(picked.id);
                    else await api.deleteArtefactPermanently(picked.kind, picked.id);
                    notify(`« ${picked.name} » supprimé définitivement.`, "ok");
                    setPicked(null); setPickedGrants([]);
                    refreshSrcContent();
                  } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                }}><IconWarn size={12} /> Supprimer définitivement</button>
              </>
            ) : (
              <button className="btn sm danger" onClick={async () => {
                try {
                  if (picked.kind === "dataset") await api.archiveDataset(picked.id);
                  else await api.archiveArtefact(picked.kind, picked.id);
                  notify(`« ${picked.name} » archivé.`, "ok");
                  setPicked({ ...picked, archived: true });
                  refreshSrcContent();
                } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
              }}>Archiver</button>
            )}
          </div>
          {picked.kind !== "dataset" && (
            <>
              <p className="ad-note">
                Accès accordés pour « {picked.name} » :
              </p>
              <div className="ad-chips">
                {pickedGrants.map((g) => (
                  <code key={g.environment}>
                    {g.environment}
                    <button className="btn sm" onClick={async () => {
                      try {
                        await api.removeArtefactGrant(picked.kind, picked.id, g.environment);
                        await refreshPickedGrants(picked.kind, picked.id);
                        notify(`Accès de « ${g.environment} » révoqué.`, "ok");
                      } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                    }}>×</button>
                  </code>
                ))}
                {pickedGrants.length === 0 && <em className="ad-note">aucun</em>}
              </div>
              <div className="ad-form">
                <select value={grantTarget} onChange={(e) => setGrantTarget(e.target.value)}>
                  <option value="">— accorder à —</option>
                  {envs.filter((e) => e !== grantSrc).map((e) => <option key={e} value={e}>{e}</option>)}
                </select>
                <button className="btn sm" disabled={!grantTarget} onClick={async () => {
                  try {
                    await api.setArtefactGrant(picked.kind, picked.id, grantTarget);
                    await refreshPickedGrants(picked.kind, picked.id);
                    notify(`« ${picked.name} » partagé en lecture avec « ${grantTarget} ».`, "ok");
                  } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                }}><IconSave size={12} /> Accorder</button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

/* ── SSO ─────────────────────────────────────────────────────────── */
function Sso({ envs, notify }: { envs: string[]; notify: Props["notify"] }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [discovery, setDiscovery] = useState("");
  const [maps, setMaps] = useState<{ group: string; environment: string; role: string }[]>(
    [{ group: "", environment: envs[0] || "default", role: "operator" }]);

  const refresh = useCallback(async () => {
    try { setRows(await api.listProviders()); } catch { /* ignore */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="ad-body">
      <p className="ad-hint">
        Les groupes de votre annuaire deviennent des rôles ici, et sont
        <strong> réappliqués à chaque connexion</strong> : retirer quelqu'un d'un
        groupe lui retire réellement l'accès.
      </p>
      <div className="ad-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="nom (ex. corp)" />
        <input value={clientId} onChange={(e) => setClientId(e.target.value)}
               placeholder="client id" />
        <input value={secret} onChange={(e) => setSecret(e.target.value)} type="password"
               placeholder="client secret (vide = inchangé)" />
        <input value={discovery} onChange={(e) => setDiscovery(e.target.value)}
               placeholder="URL de découverte (.well-known/openid-configuration)" />
      </div>

      <h4>Correspondances groupe → rôle</h4>
      {maps.map((m, i) => (
        <div key={i} className="ad-form">
          <input value={m.group} placeholder="groupe dans l'annuaire"
                 onChange={(e) => setMaps((ms) => ms.map((x, j) =>
                   j === i ? { ...x, group: e.target.value } : x))} />
          <input value={m.environment} placeholder="environnement" list="ad-envs2"
                 onChange={(e) => setMaps((ms) => ms.map((x, j) =>
                   j === i ? { ...x, environment: e.target.value } : x))} />
          <datalist id="ad-envs2">{envs.map((e) => <option key={e} value={e} />)}</datalist>
          <select value={m.role} onChange={(e) => setMaps((ms) => ms.map((x, j) =>
            j === i ? { ...x, role: e.target.value } : x))}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      ))}
      <div className="ad-form">
        <button className="btn sm" onClick={() => setMaps((ms) =>
          [...ms, { group: "", environment: envs[0] || "default", role: "operator" }])}>
          + correspondance
        </button>
        <button className="btn" disabled={!name.trim() || !clientId.trim()}
                onClick={async () => {
                  try {
                    await api.saveProvider({
                      name: name.trim(), client_id: clientId, client_secret: secret,
                      discovery_url: discovery,
                      claim_mappings: maps.filter((m) => m.group.trim()) });
                    setSecret(""); await refresh();
                    notify(`Fournisseur « ${name} » enregistré.`, "ok");
                  } catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
                }}><IconSave size={14} /> Enregistrer</button>
      </div>

      {rows.length > 0 && (
        <table className="ad-table">
          <thead><tr><th>Fournisseur</th><th>Client</th><th>Secret</th><th>Groupes</th><th>id_token</th></tr></thead>
          <tbody>
            {rows.map((p, i) => (
              <tr key={i}>
                <td><strong>{String(p.name)}</strong></td>
                <td>{String(p.client_id || "—")}</td>
                <td>{p.has_secret ? "enregistré" : <em>absent</em>}</td>
                <td>{((p.claim_mappings as unknown[]) || []).length}</td>
                <td>{p.jwks_url
                  ? <span className="ad-tag">signature vérifiée</span>
                  : <span className="ad-tag danger" title="Aucune clé de signature connue pour ce fournisseur — relancez la découverte, ou vérifiez que le document OIDC publie bien jwks_uri.">
                      non vérifiée
                    </span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ── the whole picture ───────────────────────────────────────────── */
interface EnvRow {
  name: string; label: string; has_profile: boolean; modules: string[];
  modules_restricted: boolean; config_locked: boolean; tco_editable: boolean;
  actions: number;
  members: { email: string; role: string; from_sso: boolean; last_login_at?: string }[];
  roles: Record<string, number>; artefacts: Record<string, number>;
  tables: number; keys: number; runs_error: number; url: string;
}

/**
 * A console made only of separate screens forces its user to hold the picture in
 * their head. This assembles it once, so the other tabs become places you go to
 * *change* something rather than to find out what the state is.
 */
function Overview({ notify }: { notify: Props["notify"] }) {
  const [data, setData] = useState<{
    environments: EnvRow[]; users: UserRow[]; modules: string[]; roles: string[];
    policy: Record<string, string[]>;
    capabilities: { capability: string; min_role: string; label: string }[];
    providers: { name: string; enabled: boolean; mappings: number }[];
    totals: Record<string, number>;
  } | null>(null);
  const [open, setOpen] = useState("");

  const refresh = useCallback(async () => {
    try { setData(await api.adminOverview()); }
    catch (e) { notify(e instanceof Error ? e.message : String(e), "err"); }
  }, [notify]);
  useEffect(() => { refresh(); }, [refresh]);

  if (!data) return <p className="ad-note">…</p>;

  return (
    <div className="ad-body">
      <div className="ad-totals">
        {[["environnements", data.totals.environments], ["comptes", data.totals.users],
          ["tables", data.totals.tables], ["artefacts", data.totals.artefacts]]
          .map(([label, n]) => (
            <span key={String(label)} className="ad-total">
              <strong>{String(n)}</strong> {String(label)}
            </span>
          ))}
        <button className="btn sm" onClick={refresh}><IconReset size={12} /> Rafraîchir</button>
      </div>

      <h4><IconLayers size={13} /> Environnements</h4>
      <table className="ad-table">
        <thead>
          <tr><th>Environnement</th><th>Adresse</th><th>Modules</th><th>Membres</th>
              <th>Contenu</th><th /></tr>
        </thead>
        <tbody>
          {data.environments.map((e) => (
            <Fragment key={e.name}>
              <tr>
                <td>
                  <strong>{e.label}</strong>
                  {!e.has_profile && <span className="ad-note"> · sans profil</span>}
                  {e.config_locked && <span className="ad-tag">config imposée</span>}
                </td>
                <td>
                  {/* An address to hand out, not a sequence of clicks. */}
                  <a className="ad-link" href={e.url}>{e.url}</a>
                </td>
                <td>
                  {e.modules_restricted
                    ? <span className="ad-badge">{e.modules.length} / {data.modules.length}</span>
                    : <span className="ad-note">tous</span>}
                </td>
                <td>
                  {e.members.length === 0
                    ? <em className="ad-note">personne</em>
                    : (
                      <span className="ad-chips">
                        {Object.entries(e.roles).filter(([, n]) => n > 0)
                          .map(([r, n]) => <code key={r}>{n} {r}</code>)}
                      </span>
                    )}
                </td>
                <td className="ad-note">
                  {Object.entries(e.artefacts).filter(([, n]) => n > 0)
                    .map(([k, n]) => `${n} ${k}`).join(" · ") || "vide"}
                  {e.tables > 0 && ` · ${e.tables} table(s)`}
                  {e.keys > 0 && ` · ${e.keys} clé(s)`}
                  {e.runs_error > 0 && (
                    <span className="ad-err"> · {e.runs_error} exécution(s) en erreur</span>
                  )}
                </td>
                <td>
                  <button className="btn sm"
                          onClick={() => setOpen(open === e.name ? "" : e.name)}>
                    {open === e.name ? "Fermer" : "Détail"}
                  </button>
                </td>
              </tr>
              {open === e.name && (
                <tr className="ad-detail">
                  <td colSpan={6}>
                    <div className="ad-modules">
                      {data.modules.map((m) => (
                        <span key={m} className={`ad-mod ${e.modules.includes(m) ? "on" : ""}`}>
                          {m}
                        </span>
                      ))}
                    </div>
                    <table className="ad-table">
                      <thead><tr><th>Membre</th><th>Rôle</th><th>Origine</th>
                                 <th>Dernière connexion</th></tr></thead>
                      <tbody>
                        {e.members.map((m) => (
                          <tr key={m.email}>
                            <td>{m.email}</td>
                            <td><span className="ad-badge">{m.role}</span></td>
                            <td className="ad-note">{m.from_sso ? "annuaire" : "manuel"}</td>
                            <td className="ad-note">
                              {m.last_login_at
                                ? m.last_login_at.replace("T", " ").slice(0, 16)
                                : "jamais"}
                            </td>
                          </tr>
                        ))}
                        {e.members.length === 0 && (
                          <tr><td colSpan={4} className="ad-note">
                            Aucun membre — personne ne peut y entrer.
                          </td></tr>
                        )}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>

      <h4><IconCheck size={13} /> Ce que chaque rôle autorise</h4>
      <p className="ad-note">
        La politique du serveur, telle quelle : un nom de rôle n'a pas à être
        interprété.
      </p>
      <table className="ad-table">
        <thead><tr><th>Droit</th>{data.roles.map((r) => <th key={r}>{r}</th>)}</tr></thead>
        <tbody>
          {data.capabilities.map((c) => (
            <tr key={c.capability}>
              <td>{c.label} <code className="ad-cap">{c.capability}</code></td>
              {data.roles.map((r) => (
                <td key={r} className="ad-cell">
                  {(data.policy[r] || []).includes(c.capability)
                    ? <span className="ad-yes">oui</span>
                    : <span className="ad-no">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <h4><IconTable size={13} /> Comptes</h4>
      <table className="ad-table">
        <thead><tr><th>Compte</th><th>Environnements</th><th>SSO</th><th>Dernière connexion</th></tr></thead>
        <tbody>
          {data.users.map((u) => (
            <tr key={u.id}>
              <td>
                <strong>{u.email}</strong>
                {u.is_superadmin && <span className="ad-tag">admin général</span>}
              </td>
              <td className="ad-chips">
                {Object.entries(u.environments).map(([e, r]) => <code key={e}>{e} : {r}</code>)}
                {Object.keys(u.environments).length === 0 && <em className="ad-note">aucun</em>}
              </td>
              <td className="ad-note">{u.sso.join(", ") || "—"}</td>
              <td className="ad-note">
                {u.last_login_at ? u.last_login_at.replace("T", " ").slice(0, 16) : "jamais"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.providers.length > 0 && (
        <>
          <h4>SSO</h4>
          <table className="ad-table">
            <thead><tr><th>Fournisseur</th><th>Actif</th><th>Correspondances</th></tr></thead>
            <tbody>
              {data.providers.map((p) => (
                <tr key={p.name}>
                  <td><strong>{p.name}</strong></td>
                  <td>{p.enabled ? "oui" : "non"}</td>
                  <td>{p.mappings}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
