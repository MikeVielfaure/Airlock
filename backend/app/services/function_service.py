"""
function_service.py
───────────────────
Fonctions atomiques de validation et nettoyage.

Pour ajouter une fonction :
  1. Écrire la méthode check_xxx / clean_xxx
  2. La déclarer dans MAPPING_FUNCTION / CLEANING_FUNCTION
  3. Ajouter le champ dans FieldConfig
  4. Brancher dans ProcessService._validate_column / _clean_column
  5. Ajouter le widget dans field_config_component
  Voir EXTEND.md pour la procédure complète.
"""

import re
from datetime import datetime
from dateutil import parser as dateutil_parser
import pandas as pd


# ── Formats date courants (ordre de tentative) ────────────────────────────────
DATE_FORMATS = [
    "%d/%m/%Y", "%d/%m/%y",
    "%Y-%m-%d", "%y-%m-%d",
    "%d-%m-%Y", "%d-%m-%y",
    "%d.%m.%Y", "%d.%m.%y",
    "%Y%m%d",
    "%d%m%Y",
    "%m/%d/%Y", "%m/%d/%y",
    "%Y/%m/%d",
]

# ── Formats disponibles dans l'UI (sélecteur) ────────────────────────────────
UI_DATE_FORMATS = [
    "%d/%m/%Y", "%d/%m/%y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y%m%d",
    "%d%m%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
]

# ── Modèles regex prédéfinis ──────────────────────────────────────────────────
REGEX_PRESETS = {
    "SIRET (14 chiffres)":          r"^\d{14}$",
    "SIREN (9 chiffres)":           r"^\d{9}$",
    "Code postal FR (5 chiffres)":  r"^\d{5}$",
    "Email":                        r"^[\w\.-]+@[\w\.-]+\.\w{2,}$",
    "Téléphone FR":                 r"^(\+33|0)[1-9](\d{2}){4}$",
    "Alphanumérique":               r"^[A-Za-z0-9]+$",
    "Lettres uniquement":           r"^[A-Za-zÀ-ÿ\s\-']+$",
    "Entier positif":               r"^\d+$",
    "Décimal (point)":              r"^\d+(\.\d+)?$",
    "Code ISO pays (2 lettres)":    r"^[A-Z]{2}$",
    "IBAN":                         r"^[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}$",
}


class FunctionService:

    MAPPING_FUNCTION = {
        "length":     "check_length",
        "nullable":   "check_nullable",
        "on_list":    "check_on_list",
        "regex":      "check_regex",
        "check_type": "check_type_value",
    }

    CLEANING_FUNCTION = {
        "trim":              "clean_trim",
        "normalize_case":    "clean_normalize_case",
        "delimiteur":        "clean_delimiteur",
        "separator_mile":    "clean_separator_mile",
        "separator_decimal": "clean_separator_decimal",
        "format":            "clean_format",
        "auto_date_format":  "clean_auto_date_format",
    }

    def __init__(self):
        pass

    # ──────────────────────────────────────────────────────────────
    # ORCHESTRATEURS
    # ──────────────────────────────────────────────────────────────

    def check_all_functions_for_an_item(
        self, functions: list[tuple], item: str
    ) -> str:
        """
        Applique toutes les fonctions de validation en séquence.
        S'arrête à la première erreur.
        :return: "OK" ou message d'erreur détaillé
        """
        for key, value in functions:
            method_name = self.MAPPING_FUNCTION.get(key)
            if method_name and value is not None and value != "":
                if not getattr(self, method_name)(value, item):
                    return f'{key} KO — "{item}"'
        return "OK"

    def clean_all_functions_for_an_item(
        self, functions: list[tuple], item: str
    ) -> tuple[str, str]:
        """
        Applique toutes les fonctions de nettoyage en chaîne.
        La valeur peut être un tuple (arg1, arg2) pour les fonctions multi-params.
        :return: (message, valeur_nettoyée)
        """
        current = item
        for key, value in functions:
            method_name = self.CLEANING_FUNCTION.get(key)
            if method_name and value not in (None, "", False):
                fn = getattr(self, method_name)
                if isinstance(value, tuple):
                    result = fn(*value, current)
                else:
                    result = fn(value, current)
                if result is not None and result != current:
                    current = result
        changed = current != item
        return (f'"{item}" → "{current}"' if changed else ""), current

    # ──────────────────────────────────────────────────────────────
    # VALIDATION
    # ──────────────────────────────────────────────────────────────

    def check_length(self, length: int, item: str) -> bool:
        if length is None:
            return True
        return len(str(item)) <= length

    def check_nullable(self, nullable: bool, item: str) -> bool:
        if nullable:
            return True
        return item not in (None, "", "None", "nan", "NaN")

    def check_on_list(self, list_item: list, item: str) -> bool:
        # Les valeurs vides sont gérées par check_nullable — on les laisse passer ici
        if str(item).strip() in ("", "None", "nan", "NaN"):
            return True
        return str(item) in [str(v) for v in list_item]

    def check_regex(self, regex: str, item: str) -> bool:
        try:
            return bool(re.match(regex, str(item)))
        except re.error:
            return False

    def check_type_value(self, field_type: str, item: str) -> bool:
        """
        Vérifie que la valeur correspond au type déclaré dans FieldConfig.
        field_type : "integer" | "float" | "date" | "boolean" | "string"
        string accepte tout → toujours True
        """
        if not item or str(item).strip() in ("", "None", "nan"):
            return True   # vide → géré par check_nullable
        val = str(item).strip()

        if field_type == "integer":
            try:
                int(val.replace(" ", "").replace(",", "").replace(".", ""))
                return True
            except ValueError:
                # Tenter avec séparateurs
                try:
                    float(val.replace(",", "."))
                    return float(val.replace(",", ".")).is_integer()
                except ValueError:
                    return False

        if field_type == "float":
            try:
                float(val.replace(",", ".").replace(" ", ""))
                return True
            except ValueError:
                return False

        if field_type == "date":
            return self._parse_date(val) is not None

        if field_type == "boolean":
            return val.lower() in (
                "true", "false", "1", "0", "oui", "non",
                "yes", "no", "vrai", "faux",
            )

        return True  # string

    # ──────────────────────────────────────────────────────────────
    # NETTOYAGE
    # ──────────────────────────────────────────────────────────────

    def clean_trim(self, _: bool, item: str) -> str:
        """Supprime les espaces en début et fin de valeur."""
        return str(item).strip()

    def clean_normalize_case(self, mode: str, item: str) -> str:
        """
        Normalise la casse d'une valeur.
        mode : "upper" | "lower" | "title" | "strip_only"
        """
        s = str(item)
        if mode == "upper":  return s.upper()
        if mode == "lower":  return s.lower()
        if mode == "title":  return s.title()
        return s

    def clean_delimiteur(self, deli: str, item: str) -> str:
        return str(item).replace(deli, "")

    def clean_separator_mile(self, sam, item: str) -> str:
        return self.parse_number(item=item, mille=sam)

    def clean_separator_decimal(self, sad, item: str) -> str:
        return self.parse_number(item=item, decimal=sad)

    def clean_format(self, fmt_source: str, fmt_target: str | None, item: str) -> str:
        """
        Convertit item depuis fmt_source vers fmt_target (ou fmt_source si non spécifié).

        NOTE: la valeur (`item`) est en dernière position pour respecter la
        convention de l'orchestrateur, qui appelle toujours `fn(*params, item)`.
        fmt_source : format de la date en entrée  (ex: %d/%m/%Y)
        fmt_target : format de la date en sortie  (ex: %Y-%m-%d)
        Si fmt_target est None, on reformate dans le même format source.
        """
        if not item or str(item).strip() in ("", "None", "nan"):
            return item
        val    = str(item).strip()
        target = fmt_target or fmt_source

        if isinstance(item, datetime):
            return item.strftime(target)

        # Tentative avec le format source explicite
        try:
            return datetime.strptime(val, fmt_source).strftime(target)
        except ValueError:
            pass

        # Fallback parsing automatique → reformatage vers la cible
        dt = self._parse_date(val)
        if dt:
            return dt.strftime(target)
        return item

    def clean_auto_date_format(self, target_fmt: str, item: str) -> str:
        """
        Détecte automatiquement le format source de la date,
        puis la convertit vers target_fmt.
        Utilise l'heuristique jour/mois pour éviter les confusions JJ↔MM.
        Appelé quand auto_date_format=True — target_fmt = format_clean ou "%Y-%m-%d".
        """
        if not item or str(item).strip() in ("", "None", "nan"):
            return item
        val = str(item).strip()

        dt = self._parse_date_smart(val)
        if dt:
            return dt.strftime(target_fmt)
        return item

    # ──────────────────────────────────────────────────────────────
    # DÉTECTION DATE (heuristique JJ vs MM)
    # ──────────────────────────────────────────────────────────────

    def _parse_date(self, val: str) -> datetime | None:
        """
        Tente de parser une date avec les formats courants.
        Retourne un objet datetime ou None.
        """
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        # Fallback dateutil
        try:
            return dateutil_parser.parse(val, dayfirst=True)
        except Exception:
            return None

    def _parse_date_smart(self, val: str) -> datetime | None:
        """
        Parsing intelligent : extrait les composants numériques et
        utilise l'heuristique "valeur > 12 = forcément le jour" pour
        déterminer l'ordre JJ/MM quand ambigu.
        """
        # Extraire les parties numériques
        parts = re.split(r"[-/.\s]", val.strip())
        nums  = [p for p in parts if p.isdigit()]

        if len(nums) == 3:
            a, b, c = [int(x) for x in nums]
            raw     = [nums[0], nums[1], nums[2]]

            # Identifier l'année (4 chiffres ou > 31)
            year_idx = next(
                (i for i, x in enumerate([a, b, c]) if x > 31 or len(raw[i]) == 4),
                None,
            )

            remaining = [(i, v) for i, v in enumerate([a, b, c]) if i != year_idx]

            if year_idx is not None and len(remaining) == 2:
                (i1, v1), (i2, v2) = remaining
                # Heuristique : valeur > 12 → c'est le jour
                if v1 > 12 and v2 <= 12:
                    day, month = v1, v2
                elif v2 > 12 and v1 <= 12:
                    day, month = v2, v1
                else:
                    # Ambigu → on suppose JJ/MM (convention française)
                    day, month = v1, v2

                year_val = [a, b, c][year_idx]
                if year_val < 100:
                    year_val += 2000

                try:
                    return datetime(year_val, month, day)
                except ValueError:
                    pass

        # Fallback dateutil dayfirst=True
        try:
            return dateutil_parser.parse(val, dayfirst=True)
        except Exception:
            return None

    def detect_date_format(self, series: "pd.Series") -> str | None:
        """
        Détecte le format date le plus probable d'une colonne entière.
        Teste chaque format sur toutes les valeurs non vides.
        Retourne le premier format qui matche au moins 80% des valeurs.
        """
        values = series.dropna().astype(str)
        values = values[~values.isin(["", "None", "nan", "NaT"])]
        if len(values) == 0:
            return None

        threshold = max(1, int(len(values) * 0.8))

        for fmt in DATE_FORMATS:
            matches = 0
            for v in values:
                try:
                    datetime.strptime(v.strip(), fmt)
                    matches += 1
                except ValueError:
                    continue
            if matches >= threshold:
                return fmt
        return None

    # ──────────────────────────────────────────────────────────────
    # UTILITAIRES NUMÉRIQUES
    # ──────────────────────────────────────────────────────────────

    def detect_number_separators(self, item: str) -> tuple[str | None, str | None]:
        """
        Détecte le séparateur de milliers et décimal.
        :return: (sep_milliers, sep_décimal)
        """
        item = item.strip().replace(";", "")
        if not item:
            return None, None
        dot, comma = item.rfind("."), item.rfind(",")
        if dot != -1 and comma != -1:
            return (",", ".") if dot > comma else (".", ",")
        if dot != -1:
            return (None, ".") if len(item) - dot <= 3 else (".", None)
        if comma != -1:
            return (None, ",") if len(item) - comma <= 3 else (",", None)
        return None, None

    def parse_number(self, item: str, mille=None, decimal=None) -> str | None:
        if not item or str(item).strip() == "":
            return None
        item = str(item).strip()
        # Les espaces (normal et insécable) sont des séparateurs de milliers
        # courants en notation française — on les retire si mille est demandé.
        if mille:
            item = item.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
        sep_mile, sep_dec = self.detect_number_separators(item)
        if sep_mile and mille:
            item = item.replace(sep_mile, "")
        if sep_dec and decimal:
            item = item.replace(sep_dec, ".")
        return item
