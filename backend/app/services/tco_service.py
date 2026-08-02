"""
tco_service.py
──────────────
Service TCO (Table de Correspondance).

Un TCO est un CSV de référence global avec au minimum deux colonnes :
  • SOURCE_VALUE  : la valeur à chercher dans le champ du fichier traité
  • TARGET_LABEL  : le label cible attendu (correspond à FieldConfig.mapping)

Exemple TCO :
  SOURCE_VALUE | TARGET_LABEL
  M            | MASCULIN
  F            | FEMININ
  001          | FRANCE
  002          | BELGIQUE

Pour un champ configuré avec mapping="MASCULIN", on vérifie que la valeur
du champ existe dans SOURCE_VALUE ET que TARGET_LABEL == "MASCULIN".
"""

import io
import pandas as pd


# Noms de colonnes attendus dans le TCO (insensibles à la casse)
_COL_TYPE = "TYPE"
_COL_SOURCE = "SOURCE_VALUE"
_COL_TARGET = "TARGET_LABEL"


class TcoService:

    # ──────────────────────────────────────────────────────────────
    # CHARGEMENT
    # ──────────────────────────────────────────────────────────────

    _ENCODINGS = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
    # En-têtes acceptés (insensibles à la casse) -> nom canonique
    # An optional third column. One correspondence table can then serve several
    # fields — CIVILITE and PAYS in the same file — instead of one table per
    # field. Absent, the table applies to everything, so existing TCOs are
    # unaffected.
    _ALIASES_TYPE = {"TYPE", "CHAMP", "FIELD", "RUBRIQUE", "CATEGORIE"}
    _ALIASES_SOURCE = {"SOURCE_VALUE", "SOURCE", "VALEUR_SOURCE", "SOURCE_VAL",
                       "VALUE", "VALEUR", "CODE", "KEY", "CLE"}
    _ALIASES_TARGET = {"TARGET_LABEL", "TARGET", "LABEL", "LIBELLE", "TARGET_VALUE",
                       "CIBLE", "LABEL_CIBLE", "LIBELLE_CIBLE", "VALUE_TARGET"}

    def _detect_encoding(self, raw: bytes) -> str:
        for enc in self._ENCODINGS:
            try:
                raw.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue
        return "utf-8"

    def load_tco(
        self,
        raw: bytes,
        delimiter=None,
        encoding: str = "AUTO",
    ) -> pd.DataFrame:
        """
        Charge un TCO depuis des bytes, avec détection automatique de
        l'encodage et du délimiteur (comme le fichier principal).

        Le TCO doit contenir deux colonnes : SOURCE_VALUE et TARGET_LABEL.
        Des alias courants sont acceptés (source/valeur, target/label/libellé…),
        insensibles à la casse. Sinon une erreur explicite liste l'attendu et
        ce qui a été trouvé.
        """
        enc = encoding if encoding not in (None, "AUTO", "") else self._detect_encoding(raw)
        content = raw.decode(enc, errors="replace")
        buf = io.StringIO(content)

        if delimiter in (None, "AUTO", ""):
            df = pd.read_csv(buf, sep=None, engine="python", dtype=str)
        else:
            df = pd.read_csv(buf, sep=delimiter, engine="python", dtype=str)

        df.columns = df.columns.str.strip().str.upper()
        rename = {}
        for c in df.columns:
            if c in self._ALIASES_TYPE and _COL_TYPE not in rename.values():
                rename[c] = _COL_TYPE
            elif c in self._ALIASES_SOURCE and _COL_SOURCE not in rename.values():
                rename[c] = _COL_SOURCE
            elif c in self._ALIASES_TARGET and _COL_TARGET not in rename.values():
                rename[c] = _COL_TARGET
        df = df.rename(columns=rename).fillna("")

        missing = [c for c in (_COL_SOURCE, _COL_TARGET) if c not in df.columns]
        if missing:
            raise ValueError(
                "Le fichier TCO doit avoir deux colonnes : "
                "SOURCE_VALUE (la valeur à chercher) et TARGET_LABEL (le label cible). "
                f"Colonnes manquantes : {', '.join(missing)}. "
                f"Colonnes trouvées : {', '.join(df.columns.tolist()) or '(aucune)'}."
            )
        return df

    def _scoped(self, tco_df: pd.DataFrame, type_: str | None) -> pd.DataFrame:
        """
        Restrict to rows whose TYPE matches, when the caller names one and the
        table actually carries a TYPE column. One TCO commonly serves several
        fields — a job title and a legal-structure label can resolve to the
        same code by coincidence — so a field that names its type must only
        ever see its own slice, never risk a silent cross-type collision.

        No type named, or no TYPE column at all: the whole table applies, same
        as before this existed — existing single-purpose TCOs are unaffected.
        """
        if not type_ or _COL_TYPE not in tco_df.columns:
            return tco_df
        return tco_df[tco_df[_COL_TYPE].str.strip().str.upper() == type_.strip().upper()]

    def get_available_labels(self, tco_df: pd.DataFrame, type_: str | None = None) -> list[str]:
        """Retourne les valeurs distinctes de TARGET_LABEL disponibles dans le TCO."""
        return sorted(self._scoped(tco_df, type_)[_COL_TARGET].dropna().unique().tolist())

    def as_lookup_map(self, tco_df: pd.DataFrame, type_: str | None = None) -> dict[str, str]:
        """{SOURCE_VALUE: TARGET_LABEL} — backs the LOOKUP() computed function."""
        scoped = self._scoped(tco_df, type_)
        out: dict[str, str] = {}
        for src, tgt in zip(scoped[_COL_SOURCE].tolist(), scoped[_COL_TARGET].tolist()):
            if src is not None and str(src) != "nan":
                out[str(src)] = "" if tgt is None else str(tgt)
        return out

    # ──────────────────────────────────────────────────────────────
    # LOOKUP
    # ──────────────────────────────────────────────────────────────

    def lookup(
        self,
        tco_df: pd.DataFrame,
        source_value: str,
        target_label: str,
        type_: str | None = None,
    ) -> tuple[bool, str]:
        """
        Vérifie qu'une valeur source mappe vers le label cible attendu.

        :param tco_df:       DataFrame TCO chargé
        :param source_value: valeur du champ dans le fichier traité
        :param target_label: label attendu (= FieldConfig.mapping)
        :param type_:        restreint aux lignes de ce TYPE (FieldConfig.tco_type)
        :return: (ok: bool, message: str)
        """
        if not source_value or source_value.strip() == "":
            return False, f"MAPPING KO — valeur source vide"

        tco_df = self._scoped(tco_df, type_)
        matches = tco_df[
            (tco_df[_COL_SOURCE].str.strip() == source_value.strip()) &
            (tco_df[_COL_TARGET].str.strip().str.upper() == target_label.strip().upper())
        ]

        if not matches.empty:
            return True, "MAPPING OK"
        
        # Chercher si la source existe au moins (pour un message plus précis)
        source_exists = not tco_df[
            tco_df[_COL_SOURCE].str.strip() == source_value.strip()
        ].empty

        if source_exists:
            found_labels = tco_df[
                tco_df[_COL_SOURCE].str.strip() == source_value.strip()
            ][_COL_TARGET].tolist()
            return False, (
                f"MAPPING KO — \"{source_value}\" existe mais mappe vers "
                f"{found_labels}, attendu \"{target_label}\""
            )

        return False, f"MAPPING KO — \"{source_value}\" introuvable dans le TCO"

    def lookup_series(
        self,
        tco_df: pd.DataFrame,
        series: pd.Series,
        target_label: str,
        type_: str | None = None,
    ) -> pd.Series:
        """
        Applique lookup() sur toute une colonne.
        Retourne une Series de messages ('MAPPING OK' ou message d'erreur).
        """
        def _check(val):
            _, msg = self.lookup(tco_df, "" if pd.isna(val) else str(val), target_label, type_)
            return msg
        return series.apply(_check)

    def replace_series(
        self,
        tco_df: pd.DataFrame,
        series: pd.Series,
        type_: str | None = None,
    ) -> tuple[pd.Series, pd.Series]:
        """
        Mode REMPLACEMENT : remplace chaque valeur source par son TARGET_LABEL.

        Retourne (valeurs_remplacées, messages) :
          • trouvée   -> valeur = TARGET_LABEL,  message "MAPPING OK — src → label"
          • absente   -> valeur conservée,       message "MAPPING KO — … introuvable"
            (la valeur non couverte ressort donc dans la couverture TCO)
        """
        lm = {str(k).strip(): v for k, v in self.as_lookup_map(tco_df, type_).items()}
        new_vals, msgs = [], []
        for val in series.tolist():
            s = "" if pd.isna(val) else str(val).strip()
            if s == "":
                new_vals.append(val)
                msgs.append("MAPPING KO — valeur source vide")
            elif s in lm:
                new_vals.append(lm[s])
                msgs.append(f"MAPPING OK — {s} → {lm[s]}")
            else:
                new_vals.append(val)
                msgs.append(f'MAPPING KO — "{s}" introuvable dans le TCO')
        return (pd.Series(new_vals, index=series.index),
                pd.Series(msgs, index=series.index))
