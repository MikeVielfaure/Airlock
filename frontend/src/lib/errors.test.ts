import { describe, it, expect } from "vitest";
import { errorDetail, filenameFromDisposition } from "./api";

/**
 * Ce que l'utilisateur lit quand un appel échoue.
 *
 * FastAPI met deux choses différentes dans `detail` : une chaîne pour un
 * `HTTPException(422, "message")`, une *liste* d'objets quand c'est Pydantic
 * qui refuse le corps. Le client ne connaissait que le premier cas, donc
 * toute erreur de validation arrivait à l'écran sous la forme
 * `[object Object]`. Ces tests fixent les deux formes.
 */
describe("errorDetail", () => {
  it("laisse passer une chaîne telle quelle", () => {
    expect(errorDetail("Cette colonne n'existe pas.", "422"))
      .toBe("Cette colonne n'existe pas.");
  });

  it("met en forme une erreur de validation Pydantic", () => {
    // La forme exacte renvoyée par le backend, relevée sur
    // POST /api/auth/login sans mot de passe.
    const detail = [{
      type: "missing",
      loc: ["body", "password"],
      msg: "Field required",
      input: { email: "x@y.fr" },
    }];
    expect(errorDetail(detail, "Unprocessable Entity"))
      .toBe("password : Field required");
  });

  it("joint plusieurs erreurs au lieu d'en perdre", () => {
    const detail = [
      { loc: ["body", "email"], msg: "Field required" },
      { loc: ["body", "role"], msg: "Value error, rôle inconnu" },
    ];
    expect(errorDetail(detail, "422"))
      .toBe("email : Field required — role : Value error, rôle inconnu");
  });

  it("retire body/query/path du chemin, garde le reste", () => {
    /* « fields.0.name » situe l'erreur ; « body.fields.0.name » ajoute un
       détail de transport dont la personne qui remplit le formulaire n'a
       que faire. */
    const detail = [{ loc: ["body", "fields", 0, "name"], msg: "Field required" }];
    expect(errorDetail(detail, "422")).toBe("fields.0.name : Field required");
  });

  it("garde le message seul quand il n'y a pas de champ à nommer", () => {
    expect(errorDetail([{ loc: [], msg: "Corps illisible" }], "422"))
      .toBe("Corps illisible");
  });

  it("retombe sur le statut HTTP plutôt que d'afficher un objet", () => {
    /* Le cœur du défaut corrigé : tout ce qui n'est ni chaîne ni liste
       exploitable doit donner le statut, jamais « [object Object] ». */
    for (const bizarre of [{ erreur: "x" }, null, undefined, [], 42, [{}]]) {
      expect(errorDetail(bizarre, "Bad Gateway")).toBe("Bad Gateway");
    }
  });

  it("ne renvoie jamais une chaîne vide", () => {
    expect(errorDetail("", "Not Found")).toBe("Not Found");
  });
});

/**
 * Le nom sous lequel un export est enregistré.
 *
 * Le motif précédent était gourmand : sur un en-tête portant à la fois
 * `filename=` et `filename*=`, il capturait tout ce qui suivait le premier
 * `=`, second paramètre compris.
 */
describe("filenameFromDisposition", () => {
  it("lit un nom entre guillemets", () => {
    expect(filenameFromDisposition('attachment; filename="export.csv"', "x.csv"))
      .toBe("export.csv");
  });

  it("préfère filename* quand il existe : c'est lui qui porte les accents", () => {
    const d = "attachment; filename=\"resume.csv\"; filename*=UTF-8''r%C3%A9sum%C3%A9%20paie.csv";
    expect(filenameFromDisposition(d, "x.csv")).toBe("résumé paie.csv");
  });

  it("s'arrête au point-virgule sur un nom sans guillemets", () => {
    const d = "attachment; filename=export.csv; filename*=UTF-8''autre.csv";
    // filename* gagne ici ; sans lui, la partie nue ne doit pas déborder.
    expect(filenameFromDisposition("attachment; filename=export.csv; charset=utf-8", "x.csv"))
      .toBe("export.csv");
    expect(filenameFromDisposition(d, "x.csv")).toBe("autre.csv");
  });

  it("retombe sur le nom proposé quand l'en-tête est absent ou vide", () => {
    expect(filenameFromDisposition(null, "extraction.xlsx")).toBe("extraction.xlsx");
    expect(filenameFromDisposition("", "extraction.xlsx")).toBe("extraction.xlsx");
    expect(filenameFromDisposition("attachment", "extraction.xlsx")).toBe("extraction.xlsx");
    expect(filenameFromDisposition('attachment; filename=""', "extraction.xlsx"))
      .toBe("extraction.xlsx");
  });

  it("survit à un pourcent-encodage invalide", () => {
    /* Un `%` isolé fait lever decodeURIComponent : la fonction doit rendre
       un nom, pas propager l'exception au milieu d'un téléchargement. */
    const d = "attachment; filename=\"repli.csv\"; filename*=UTF-8''mauvais%ZZ.csv";
    expect(filenameFromDisposition(d, "x.csv")).toBe("repli.csv");
  });
});
