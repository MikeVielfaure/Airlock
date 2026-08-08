import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, setToken, getToken, setEnvironment, getEnvironment } from "./api";

/**
 * Le jeton et l'environnement sont l'état le plus sensible du client : le
 * premier *est* l'authentification, le second décide de quel matériel on
 * parle. Ils vivaient dans deux variables de module sans un seul test.
 */

function mockFetch() {
  const f = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
    headers: new Headers(),
  });
  vi.stubGlobal("fetch", f);
  return f;
}

/** L'URL et les options du dernier appel réseau. */
function dernierAppel(f: ReturnType<typeof mockFetch>) {
  const [url, init] = f.mock.calls[f.mock.calls.length - 1];
  return { url: String(url), init: (init ?? {}) as RequestInit };
}

beforeEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
  setToken("");
  setEnvironment("default");
});

describe("le jeton de session", () => {
  it("survit à un rechargement via sessionStorage", () => {
    setToken("jeton-abc");
    expect(sessionStorage.getItem("fx_tok")).toBe("jeton-abc");
  });

  it("part du stockage quand on le vide — une déconnexion doit déconnecter", () => {
    setToken("jeton-abc");
    setToken("");
    expect(sessionStorage.getItem("fx_tok")).toBeNull();
    expect(getToken()).toBe("");
  });

  it("est relu depuis le stockage après un rechargement", () => {
    /* Le cas réel : l'onglet est rechargé, la variable de module repart à
       vide, et l'utilisateur ne doit pas être renvoyé à l'écran de
       connexion alors que sa session est encore valable. */
    sessionStorage.setItem("fx_tok", "jeton-rechargé");
    setToken("");                       // remet la variable de module à vide
    sessionStorage.setItem("fx_tok", "jeton-rechargé");
    expect(getToken()).toBe("jeton-rechargé");
  });

  it("n'ajoute pas d'en-tête Authorization quand il n'y a pas de jeton", async () => {
    /* Envoyer `Bearer ` vide vaudrait un 401 déguisé en bug : l'absence
       d'en-tête laisse le serveur répondre ce qu'il doit pour un anonyme,
       ce dont dépend le mode d'amorçage. */
    const f = mockFetch();
    await api.presets();
    const headers = dernierAppel(f).init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("porte le jeton sur les appels suivants une fois posé", async () => {
    const f = mockFetch();
    setToken("jeton-xyz");
    await api.presets();
    const headers = dernierAppel(f).init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer jeton-xyz");
  });

  it("tolère un stockage indisponible (navigation privée)", () => {
    /* Safari en navigation privée fait lever setItem. Le jeton ne doit pas
       survivre au rechargement — mais la session en cours doit fonctionner
       au lieu de planter à la connexion. */
    const casse = vi.spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => { throw new Error("QuotaExceededError"); });
    expect(() => setToken("jeton-privé")).not.toThrow();
    expect(getToken()).toBe("jeton-privé");
    casse.mockRestore();
  });
});

describe("l'environnement courant", () => {
  it("vaut « default » et se remet à « default » sur une valeur vide", () => {
    expect(getEnvironment()).toBe("default");
    setEnvironment("rh");
    expect(getEnvironment()).toBe("rh");
    setEnvironment("");
    expect(getEnvironment()).toBe("default");
  });

  it("part réellement dans l'URL d'un envoi de fichier", async () => {
    /* Le commentaire d'`upload` prévient qu'omettre `env` fait retomber le
       serveur sur « default » et refuse silencieusement quelqu'un dont
       c'est le seul environnement. Autant le vérifier. */
    const f = mockFetch();
    setEnvironment("compta");
    await api.upload(new File(["a;b"], "t.csv"),
                     { type: "CSV", encoding: "utf-8", delimiter: ";" });
    expect(dernierAppel(f).url).toContain("env=compta");
  });

  it("encode un nom d'environnement qui contient un caractère d'URL", async () => {
    const f = mockFetch();
    setEnvironment("paie & rh");
    await api.upload(new File(["a"], "t.csv"),
                     { type: "CSV", encoding: "utf-8", delimiter: ";" });
    expect(dernierAppel(f).url).toContain("env=paie%20%26%20rh");
  });
});

describe("l'envoi de fichier : les options de table", () => {
  it("n'envoie table_index et table_header_mode qu'avec un marqueur", async () => {
    /* Ces trois champs ne veulent rien dire séparément : un index de table
       sans marqueur désigne une table que le serveur ne sait pas trouver. */
    const f = mockFetch();
    await api.upload(new File(["a"], "t.csv"),
                     { type: "CSV", encoding: "utf-8", delimiter: ";", tableIndex: 2 });
    const fd = dernierAppel(f).init.body as FormData;
    expect(fd.get("table_index")).toBeNull();
    expect(fd.get("table_header_mode")).toBeNull();
  });

  it("envoie les trois ensemble dès qu'un marqueur est donné", async () => {
    const f = mockFetch();
    await api.upload(new File(["a"], "t.csv"), {
      type: "XLSX", encoding: "utf-8", delimiter: ";",
      tableMarker: "TOTAL", tableIndex: 2, tableHeaderMode: "global",
    });
    const fd = dernierAppel(f).init.body as FormData;
    expect(fd.get("table_marker")).toBe("TOTAL");
    expect(fd.get("table_index")).toBe("2");
    expect(fd.get("table_header_mode")).toBe("global");
  });

  it("donne un index 0 et un mode « local » par défaut avec un marqueur", async () => {
    const f = mockFetch();
    await api.upload(new File(["a"], "t.csv"),
                     { type: "XLSX", encoding: "utf-8", delimiter: ";", tableMarker: "TOTAL" });
    const fd = dernierAppel(f).init.body as FormData;
    expect(fd.get("table_index")).toBe("0");
    expect(fd.get("table_header_mode")).toBe("local");
  });
});

describe("le référencement d'un modèle EDI", () => {
  it("passe le YAML en clair quand le modèle vient de l'éditeur", async () => {
    const f = mockFetch();
    await api.ediValidate(new File(["UNB"], "f.edi"), { yaml: "name: test" });
    const fd = dernierAppel(f).init.body as FormData;
    expect(fd.get("model_yaml")).toBe("name: test");
    expect(fd.get("model_id")).toBeNull();
  });

  it("passe une référence quand le modèle vient de la bibliothèque", async () => {
    const f = mockFetch();
    await api.ediValidate(new File(["UNB"], "f.edi"), { id: "mod-7", version: 3 });
    const fd = dernierAppel(f).init.body as FormData;
    expect(fd.get("model_id")).toBe("mod-7");
    expect(fd.get("model_version")).toBe("3");
    expect(fd.get("model_yaml")).toBeNull();
  });

  it("omet la version pour dire « la dernière »", async () => {
    /* Une référence sans version épinglée doit suivre la bibliothèque ;
       envoyer une version vide ferait épingler quelque chose. */
    const f = mockFetch();
    await api.ediValidate(new File(["UNB"], "f.edi"), { id: "mod-7" });
    const fd = dernierAppel(f).init.body as FormData;
    expect(fd.get("model_id")).toBe("mod-7");
    expect(fd.get("model_version")).toBeNull();
  });

  it("traite la version null comme « la dernière », pas comme la version 0", async () => {
    const f = mockFetch();
    await api.ediValidate(new File(["UNB"], "f.edi"), { id: "mod-7", version: null });
    expect((dernierAppel(f).init.body as FormData).get("model_version")).toBeNull();
  });
});
