import { useEffect, useState } from "react";
import { api, setToken } from "../lib/api";
import type { AuthUser } from "../lib/types";
import { IconCheck, IconGrid, IconPlay } from "../lib/icons";

interface Props { onReady: (user: AuthUser | null) => void }

/**
 * The door.
 *
 * A fresh install is *open*, and the first account created becomes superadmin —
 * shipping locked behind a default password would be worse, since default
 * credentials outlive the deployment that set them. The moment an account
 * exists the application closes by itself.
 */
export function LoginGate({ onReady }: Props) {
  const [state, setState] = useState<{ setup: boolean; providers: { name: string }[] } | null>(null);
  const [email, setEmail] = useState("");
  const [pwd, setPwd] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authState().then((s) => {
      if (s.authenticated && s.user) { onReady(s.user); return; }
      // A fresh install does not let anyone through: it asks for the
      // administrator account to be created, then for a sign-in. Shipping with
      // a default password would be worse — those outlive the deployment that
      // set them — but walking straight in is not the alternative.
      setState({ setup: s.setup_needed, providers: s.providers });
    }).catch(() => setState({ setup: false, providers: [] }));
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!state) return <div className="gate"><p>…</p></div>;

  const submit = async () => {
    setBusy(true); setErr("");
    try {
      if (state.setup) {
        await api.signup(email, pwd);      // the first account is superadmin
      }
      const r = await api.login(email, pwd);
      setToken(r.token);
      onReady(r.user);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <div className="gate">
      <div className="gate-card">
        <div className="gate-brand">
          <span className="brand-mark"><IconGrid size={17} /></span>
          <div>
            <div className="brand-name">File Explorer</div>
            <div className="brand-sub">atelier de schéma · csv / xlsx</div>
          </div>
        </div>
        <h2>{state.setup ? "Créer le compte administrateur" : "Connexion"}</h2>
        {state.setup && (
          <p className="gate-note">
            Aucun compte n'existe encore. Celui-ci sera l'administrateur général
            — c'est lui qui créera ensuite les environnements et les autres
            comptes. Huit caractères minimum.
          </p>
        )}
        <input placeholder="email" value={email} autoFocus
               onChange={(e) => setEmail(e.target.value)} />
        <input placeholder="mot de passe" type="password" value={pwd}
               onChange={(e) => setPwd(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        {err && <p className="gate-err">{err}</p>}
        <button className="btn primary" disabled={busy || !email || pwd.length < 8}
                onClick={submit}>
          {state.setup ? <><IconCheck size={14} /> Créer le compte</>
                       : <><IconPlay size={14} /> Se connecter</>}
        </button>

        {state.providers.length > 0 && (
          <>
            <div className="gate-sep"><span>ou</span></div>
            {state.providers.map((p) => (
              <button key={p.name} className="btn" onClick={async () => {
                try {
                  const r = await api.ssoStart(p.name, window.location.origin + "/sso");
                  window.location.href = r.url;
                } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
              }}>Se connecter avec {p.name}</button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
