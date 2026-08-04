import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AuthUser, CryptoKeyOut, RevealEventOut } from "../lib/types";
import { IconLock, IconSave, IconWarn } from "../lib/icons";

interface Props {
  me: AuthUser | null;
  env: string;
  notify: (m: string, k?: "ok" | "err" | "info") => void;
  /** A key was created or revoked — the FieldEditor's "Confidentialité"
   * selector holds its own copy of the active-key list and would otherwise
   * stay stale until an unrelated environment switch happened to reload it. */
  onKeysChanged: () => void;
}

type Tab = "keys" | "audit";

const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * Confidentiality: keys, holders, the act of looking.
 *
 * Everything here is a thin skin over routes that already exist and are
 * already tested (`crypto_routes.py`) — this panel adds no new capability,
 * it only makes an existing one reachable without hand-written API calls.
 *
 * Holders are the access list itself, not another role: creating a key
 * needs 'admin', but adding a holder or revoking only needs to already hold
 * the key — an administrator is not automatically able to read salaries.
 */
export function KeysPanel({ me, env, notify, onKeysChanged }: Props) {
  const [tab, setTab] = useState<Tab>("keys");
  const [status, setStatus] = useState<{ available: boolean; reason: string } | null>(null);
  const [keys, setKeys] = useState<CryptoKeyOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [holderDraft, setHolderDraft] = useState<Record<string, string>>({});
  const [revokeDraft, setRevokeDraft] = useState<Record<string, string>>({});
  const [revokeOpen, setRevokeOpen] = useState<Record<string, boolean>>({});
  const [reveals, setReveals] = useState<RevealEventOut[] | null>(null);

  // Same semantics as App.tsx's `may()`: no capability map at all reads as
  // unrestricted, never as refused — an environment without a capabilities
  // list is not one where nothing is allowed.
  const may = useCallback((capability: string) => {
    const caps = me?.capabilities?.[env];
    return !caps || caps.includes(capability);
  }, [me, env]);
  const canCreate = may("keys.create");
  const canAudit = may("keys.audit");

  const refreshKeys = useCallback(async () => {
    try { setKeys(await api.listKeys()); } catch (e) { notify(err(e), "err"); }
  }, [notify]);

  useEffect(() => {
    setLoading(true);
    api.keyStatus()
      .then(async (st) => {
        setStatus(st);
        if (st.available) await refreshKeys();
      })
      .catch((e) => notify(err(e), "err"))
      .finally(() => setLoading(false));
  }, [env]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (tab === "audit" && canAudit && reveals === null) {
      api.listReveals().then(setReveals).catch((e) => notify(err(e), "err"));
    }
  }, [tab, canAudit, reveals, notify]);

  if (loading) return <p className="hint">Chargement…</p>;

  if (!status?.available) {
    return (
      <p className="ad-hint">
        <IconWarn size={14} /> {status?.reason || "La confidentialité par colonne n'est pas disponible sur ce serveur."}
      </p>
    );
  }

  return (
    <div className="ad">
      <nav className="ad-tabs">
        <button className={`tab ${tab === "keys" ? "active" : ""}`} onClick={() => setTab("keys")}>
          <IconLock size={14} /> Clés
        </button>
        {canAudit && (
          <button className={`tab ${tab === "audit" ? "active" : ""}`} onClick={() => setTab("audit")}>
            Audit
          </button>
        )}
      </nav>

      {tab === "keys" && (
        <div className="ad-body">
          {canCreate && (
            <>
              <h4><IconLock size={13} /> Nouvelle clé</h4>
              <div className="ad-form">
                <input value={newName} onChange={(e) => setNewName(e.target.value)}
                       placeholder="nom (ex. paie)" />
                <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)}
                       placeholder="libellé (optionnel)" />
                <button className="btn sm" disabled={!newName.trim()} onClick={async () => {
                  try {
                    await api.createKey(newName.trim(), newLabel.trim());
                    setNewName(""); setNewLabel("");
                    await refreshKeys();
                    onKeysChanged();
                    notify(`Clé « ${newName.trim()} » créée — vous en êtes le premier détenteur.`, "ok");
                  } catch (e) { notify(err(e), "err"); }
                }}><IconSave size={12} /> Créer</button>
              </div>
            </>
          )}

          <h4><IconLock size={13} /> Clés de « {env} »</h4>
          {keys.length === 0 ? (
            <p className="ad-note">Aucune clé dans cet environnement.</p>
          ) : (
            <div className="libcol">
              {keys.map((k) => (
                <div key={k.id} style={{ padding: "8px 0", borderBottom: "1px dashed var(--line)" }}>
                  <div className="libitem" style={{ borderBottom: "none", padding: 0 }}>
                    <span>
                      <code>{k.name}</code> {k.label && <span className="csub">{k.label}</span>}{" "}
                      <span className={`ad-badge ${k.active ? "" : "ad-err"}`}>
                        {k.active ? "active" : "révoquée"}
                      </span>
                    </span>
                  </div>

                  <div className="csub" style={{ marginTop: 4 }}>
                    Détenteurs :{" "}
                    {k.holders.map((h) => (
                      <span key={h.user_id} className="ad-chips" style={{ display: "inline-flex", marginRight: 6 }}>
                        <code>{h.email}</code>
                        {k.i_hold && k.active && k.holders.length > 1 && (
                          <button className="hclear" title="Retirer ce détenteur" onClick={async () => {
                            try {
                              await api.removeHolder(k.id, h.user_id);
                              await refreshKeys();
                              notify(`« ${h.email} » retiré de « ${k.name} ».`, "ok");
                            } catch (e) { notify(err(e), "err"); }
                          }}>×</button>
                        )}
                      </span>
                    ))}
                  </div>

                  {k.i_hold && k.active && (
                    <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6 }}>
                      <input type="email" placeholder="email du nouveau détenteur"
                             value={holderDraft[k.id] ?? ""}
                             onChange={(e) => setHolderDraft({ ...holderDraft, [k.id]: e.target.value })} />
                      <button className="btn sm" disabled={!holderDraft[k.id]?.trim()} onClick={async () => {
                        const email = (holderDraft[k.id] ?? "").trim();
                        try {
                          await api.addHolder(k.id, email);
                          setHolderDraft({ ...holderDraft, [k.id]: "" });
                          await refreshKeys();
                          notify(`« ${email} » ajouté comme détenteur de « ${k.name} ».`, "ok");
                        } catch (e) { notify(err(e), "err"); }
                      }}>Ajouter un détenteur</button>
                    </div>
                  )}

                  {k.i_hold && k.active && (
                    <div style={{ marginTop: 6 }}>
                      {!revokeOpen[k.id] ? (
                        <button className="btn sm" onClick={() => setRevokeOpen({ ...revokeOpen, [k.id]: true })}>
                          Révoquer
                        </button>
                      ) : (
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <span className="csub">Retape « {k.name} » pour confirmer — la donnée qu'elle protège devient définitivement illisible :</span>
                          <input value={revokeDraft[k.id] ?? ""}
                                 onChange={(e) => setRevokeDraft({ ...revokeDraft, [k.id]: e.target.value })} />
                          <button className="btn sm danger" disabled={revokeDraft[k.id] !== k.name} onClick={async () => {
                            try {
                              await api.revokeKey(k.id, revokeDraft[k.id] ?? "");
                              setRevokeOpen({ ...revokeOpen, [k.id]: false });
                              setRevokeDraft({ ...revokeDraft, [k.id]: "" });
                              await refreshKeys();
                              onKeysChanged();
                              notify(`Clé « ${k.name} » révoquée.`, "ok");
                            } catch (e) { notify(err(e), "err"); }
                          }}>Confirmer la révocation</button>
                          <button className="btn sm" onClick={() => setRevokeOpen({ ...revokeOpen, [k.id]: false })}>
                            Annuler
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "audit" && canAudit && (
        <div className="ad-body">
          <h4>Journal des révélations — « {env} »</h4>
          {reveals === null ? (
            <p className="hint">Chargement…</p>
          ) : reveals.length === 0 ? (
            <p className="ad-note">Aucune révélation enregistrée.</p>
          ) : (
            <table className="ad-table">
              <thead>
                <tr><th>Qui</th><th>Clé</th><th>Colonnes</th><th>Contexte</th><th>Lignes</th><th>Quand</th></tr>
              </thead>
              <tbody>
                {reveals.map((r) => (
                  <tr key={r.id}>
                    <td>{r.user_email}</td>
                    <td><code>{r.key_name}</code></td>
                    <td>{r.columns.join(", ")}</td>
                    <td>{r.context}</td>
                    <td>{r.rows}</td>
                    <td>{r.at ? new Date(r.at).toLocaleString() : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
