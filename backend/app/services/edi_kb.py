"""
edi_kb.py
─────────
Embedded knowledge base of common EDIFACT segments, qualifiers and date
formats. Deliberately small: this is NOT the UN/EDIFACT directory — it covers
the segments seen in everyday retail/logistics messages (ORDERS, DESADV,
INVOIC) well enough to (a) decode a parsed file into plain language, (b) name
fields when inferring a model from a sample file, and (c) feed the in-app doc.

Paths are "element.component", both 1-based, matching how people read EDIFACT
specs (element 1 of DTM is the composite C507; its component 1 is the 2005
qualifier, component 2 the value, component 3 the 2379 format code).
"""

# tag -> {name (fr), desc (fr), elements: {path: label}}
SEGMENTS: dict[str, dict] = {
    "UNA": {"name": "Définition des séparateurs", "desc": "Chaîne de service fixe : redéfinit les 6 caractères de service (composant, élément, décimale, échappement, réservé, fin de segment).", "elements": {}},
    "UNB": {"name": "En-tête d'interchange", "desc": "Ouvre l'enveloppe : qui envoie, qui reçoit, quand, sous quelle référence.", "elements": {"1.1": "Identifiant de syntaxe", "1.2": "Version de syntaxe", "2.1": "Expéditeur", "2.2": "Type de code expéditeur", "3.1": "Destinataire", "3.2": "Type de code destinataire", "4.1": "Date (AAMMJJ)", "4.2": "Heure (HHMM)", "5.1": "Référence d'interchange"}},
    "UNH": {"name": "En-tête de message", "desc": "Ouvre un message et annonce son type et sa version de répertoire.", "elements": {"1.1": "Référence du message", "2.1": "Type de message", "2.2": "Version", "2.3": "Révision", "2.4": "Agence de contrôle"}},
    "BGM": {"name": "Début du message", "desc": "Nature et numéro du document (commande, facture, avis d'expédition…).", "elements": {"1.1": "Type de document (code)", "2.1": "Numéro du document", "3.1": "Fonction du message"}},
    "DTM": {"name": "Date / heure / période", "desc": "Une date qualifiée : le premier composant dit LAQUELLE, le deuxième la valeur, le troisième son format.", "elements": {"1.1": "Qualifiant de date", "1.2": "Valeur", "1.3": "Format (102, 203…)"}},
    "NAD": {"name": "Nom et adresse", "desc": "Une partie de l'échange : acheteur, fournisseur, lieu de livraison… identifiée par un code (souvent un GLN).", "elements": {"1.1": "Rôle de la partie", "2.1": "Identifiant (ex. GLN)", "2.3": "Liste de codes (9 = GS1)", "4.1": "Nom", "5.1": "Rue", "6.1": "Ville", "8.1": "Code postal"}},
    "RFF": {"name": "Référence", "desc": "Une référence qualifiée : numéro de commande, de bon de livraison, de contrat…", "elements": {"1.1": "Qualifiant", "1.2": "Valeur"}},
    "CUX": {"name": "Devises", "desc": "Devise de référence du document.", "elements": {"1.1": "Usage", "1.2": "Code devise", "1.3": "Qualifiant"}},
    "CPS": {"name": "Séquence de colisage", "desc": "Niveau d'emballage dans un DESADV (palette, colis…).", "elements": {"1.1": "Numéro de séquence", "2.1": "Séquence parente"}},
    "PAC": {"name": "Emballage", "desc": "Nombre et type d'emballages.", "elements": {"1.1": "Nombre", "3.1": "Type d'emballage"}},
    "LIN": {"name": "Ligne d'article", "desc": "Ouvre une ligne : c'est le segment qui démarre chaque « item ».", "elements": {"1.1": "Numéro de ligne", "3.1": "Code article", "3.2": "Type de code (EN = EAN)"}},
    "PIA": {"name": "Identification produit additionnelle", "desc": "Codes article complémentaires (référence interne, SKU…).", "elements": {"1.1": "Fonction", "2.1": "Code", "2.2": "Type de code"}},
    "IMD": {"name": "Description d'article", "desc": "Libellé ou caractéristiques de l'article.", "elements": {"1.1": "Format", "3.4": "Description"}},
    "QTY": {"name": "Quantité", "desc": "Une quantité qualifiée : commandée, expédiée, facturée…", "elements": {"1.1": "Qualifiant", "1.2": "Valeur", "1.3": "Unité"}},
    "PRI": {"name": "Prix", "desc": "Un prix qualifié (net, brut…).", "elements": {"1.1": "Qualifiant", "1.2": "Valeur", "1.5": "Base"}},
    "MOA": {"name": "Montant monétaire", "desc": "Un montant qualifié (total ligne, total document…).", "elements": {"1.1": "Qualifiant", "1.2": "Valeur"}},
    "TDT": {"name": "Transport", "desc": "Mode et moyen de transport.", "elements": {"1.1": "Étape", "4.1": "Mode"}},
    "FTX": {"name": "Texte libre", "desc": "Commentaire ou mention libre.", "elements": {"1.1": "Sujet", "4.1": "Texte"}},
    "UNS": {"name": "Séparateur de sections", "desc": "Marque la fin du détail : S = début de la section résumé.", "elements": {"1.1": "Code de section"}},
    "CNT": {"name": "Total de contrôle", "desc": "Compteurs de vérification (ex. nombre de lignes).", "elements": {"1.1": "Qualifiant", "1.2": "Valeur"}},
    "UNT": {"name": "Fin de message", "desc": "Clôt le message : porte le NOMBRE DE SEGMENTS du message (UNH et UNT compris) et rappelle sa référence.", "elements": {"1.1": "Nombre de segments", "2.1": "Référence du message"}},
    "UNZ": {"name": "Fin d'interchange", "desc": "Clôt l'enveloppe : porte le NOMBRE DE MESSAGES et rappelle la référence d'interchange.", "elements": {"1.1": "Nombre de messages", "2.1": "Référence d'interchange"}},
}

# (tag, qualifier value) -> label (fr)
QUALIFIERS: dict[tuple[str, str], str] = {
    ("BGM", "220"): "Commande", ("BGM", "221"): "Commande ouverte", ("BGM", "351"): "Avis d'expédition", ("BGM", "380"): "Facture commerciale", ("BGM", "381"): "Avoir",
    ("DTM", "137"): "Date du document", ("DTM", "2"): "Date de livraison demandée", ("DTM", "11"): "Date d'expédition", ("DTM", "35"): "Date de livraison effective", ("DTM", "63"): "Livraison au plus tard", ("DTM", "64"): "Livraison au plus tôt", ("DTM", "17"): "Date de livraison estimée",
    ("NAD", "BY"): "Acheteur", ("NAD", "SU"): "Fournisseur", ("NAD", "DP"): "Lieu de livraison", ("NAD", "IV"): "Facturé à", ("NAD", "CN"): "Destinataire", ("NAD", "SH"): "Expéditeur de la marchandise", ("NAD", "SE"): "Vendeur",
    ("QTY", "21"): "Quantité commandée", ("QTY", "12"): "Quantité expédiée", ("QTY", "47"): "Quantité facturée", ("QTY", "113"): "Quantité prévue", ("QTY", "59"): "Quantité par colis",
    ("PRI", "AAA"): "Prix net", ("PRI", "AAB"): "Prix brut",
    ("RFF", "ON"): "Numéro de commande", ("RFF", "DQ"): "Numéro de bon de livraison", ("RFF", "IV"): "Numéro de facture", ("RFF", "CT"): "Numéro de contrat",
    ("MOA", "79"): "Total des lignes", ("MOA", "86"): "Montant total du document", ("MOA", "203"): "Montant de la ligne",
    ("CNT", "2"): "Nombre de lignes",
    ("UNS", "S"): "Début du résumé",
    ("FTX", "AAI"): "Information générale",
    ("CUX", "2"): "Devise de référence",
}

# base names used when inferring a model (tag, qualifier) -> canonical field stem
FIELD_STEMS: dict[tuple[str, str], str] = {
    ("BGM", ""): "document", ("DTM", "137"): "date_document", ("DTM", "2"): "date_livraison_demandee",
    ("DTM", "11"): "date_expedition", ("DTM", "35"): "date_livraison",
    ("NAD", "BY"): "acheteur", ("NAD", "SU"): "fournisseur", ("NAD", "DP"): "lieu_livraison",
    ("NAD", "IV"): "facture_a", ("NAD", "CN"): "destinataire", ("NAD", "SH"): "expediteur",
    ("QTY", "21"): "quantite_commandee", ("QTY", "12"): "quantite_expediee", ("QTY", "47"): "quantite_facturee",
    ("PRI", "AAA"): "prix_net", ("PRI", "AAB"): "prix_brut",
    ("RFF", "ON"): "ref_commande", ("RFF", "DQ"): "ref_bon_livraison", ("RFF", "IV"): "ref_facture",
    ("MOA", "203"): "montant_ligne", ("MOA", "86"): "montant_total",
    ("CNT", "2"): "nombre_lignes",
    ("LIN", ""): "ligne", ("CPS", ""): "colisage", ("IMD", ""): "description", ("PIA", ""): "code_additionnel",
}

# EDIFACT 2379 date format code -> (human label, regex)
DATE_FORMATS: dict[str, tuple[str, str]] = {
    "102": ("AAAAMMJJ", r"^\d{8}$"),
    "203": ("AAAAMMJJHHMM", r"^\d{12}$"),
    "610": ("AAAAMM", r"^\d{6}$"),
    "616": ("AAAASS (année-semaine)", r"^\d{6}$"),
}


def segment_label(tag: str) -> str:
    info = SEGMENTS.get(tag)
    return info["name"] if info else ""


def qualifier_label(tag: str, value: str) -> str:
    return QUALIFIERS.get((tag, value), "")


def element_label(tag: str, path: str) -> str:
    info = SEGMENTS.get(tag)
    return (info or {}).get("elements", {}).get(path, "")


def kb_payload() -> dict:
    """Everything the frontend doc page needs, in one JSON."""
    return {
        "segments": [
            {"tag": t, "name": v["name"], "desc": v["desc"],
             "elements": [{"path": p, "label": l} for p, l in v["elements"].items()]}
            for t, v in SEGMENTS.items()
        ],
        "qualifiers": [
            {"tag": t, "code": c, "label": l} for (t, c), l in sorted(QUALIFIERS.items())
        ],
        "date_formats": [
            {"code": c, "label": l} for c, (l, _) in DATE_FORMATS.items()
        ],
        "separators": [
            {"role": "component", "default": ":",
             "label": "Séparateur de composants",
             "desc": "Sépare les composants à l'intérieur d'un élément (NAD+BY+GLN::9)."},
            {"role": "element", "default": "+",
             "label": "Séparateur d'éléments",
             "desc": "Sépare les éléments de données du segment (BGM+220+PO12345)."},
            {"role": "decimal", "default": ".",
             "label": "Marque décimale",
             "desc": "Point ou virgule selon l'émetteur : à lire dans UNA, jamais à supposer."},
            {"role": "release", "default": "?",
             "label": "Caractère de libération",
             "desc": "Neutralise le caractère suivant : ?+ vaut un '+' de donnée, pas un séparateur."},
            {"role": "reserved", "default": " ",
             "label": "Réservé",
             "desc": "Position réservée par la norme, toujours un espace."},
            {"role": "segment", "default": "'",
             "label": "Fin de segment",
             "desc": "Termine chaque segment ; les retours à la ligne autour sont ignorés."},
        ],
    }
