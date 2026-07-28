"""
process_service.py
──────────────────
Pipeline de traitement des données.
Gère nettoyage, validation, mapping TCO, check_type, auto_date_format, rapport.
Aucune dépendance Streamlit.
"""

import pandas as pd
from app.models import HeaderConfig, FieldConfig
from app.services.function_service import FunctionService
from app.services.tco_service import TcoService


class ProcessService:

    def __init__(self):
        self._fn  = FunctionService()
        self._tco = TcoService()

    # ──────────────────────────────────────────────────────────────
    # HEADER
    # ──────────────────────────────────────────────────────────────

    def apply_header_config(self, df: pd.DataFrame, config: HeaderConfig) -> pd.DataFrame:
        if config is None:
            return df
        df = df.copy()

        # Noms de colonnes Unnamed (générés par pandas quand la cellule header est vide)
        _UNNAMED_COLS = {c for c in df.columns
                         if str(c).startswith("Unnamed") or str(c).strip() == ""}

        _EMPTY_STRS = ("", "nan", "None", "NaT", "<NA>")

        def _is_empty_val(val) -> bool:
            """True si la valeur est considérée vide."""
            if val is None:
                return True
            try:
                if pd.isna(val):
                    return True
            except (TypeError, ValueError):
                pass
            return str(val).strip() in _EMPTY_STRS

        # Si TOUTES les colonnes sont Unnamed (pas encore de vrai header),
        # on utilise uniquement les valeurs pour décider (sinon tout serait vide).
        _all_cols_unnamed = len(_UNNAMED_COLS) == len(df.columns)

        def _is_empty_row(row) -> bool:
            """
            Une ligne est vide si :
            - toutes ses cellules sont vides (valeur vide ou NaN), OU
            - les seules cellules non vides sont sous des colonnes Unnamed
              ET il existe au moins une colonne nommée dans le df
              (sinon on se baserait uniquement sur les valeurs).
            """
            for col, val in zip(row.index, row):
                if not _all_cols_unnamed and col in _UNNAMED_COLS:
                    continue  # ignorer les Unnamed seulement s'il existe de vraies colonnes
                if not _is_empty_val(val):
                    return False
            return True

        # ── Étape 0 : auto_header ───────────────────────────────────
        # Si toutes les colonnes sont Unnamed, le vrai header est probablement
        # dans les données — on cherche la première ligne non vide et on l'utilise.
        all_unnamed = all(str(c).startswith("Unnamed") or str(c).strip() == ""
                          for c in df.columns)
        if config.auto_header and all_unnamed and len(df) > 0:
            # Trouver la première ligne non vide
            for idx in df.index:
                if not _is_empty_row(df.loc[idx]):
                    new_header = df.loc[idx].tolist()
                    df = df.loc[idx + 1:].reset_index(drop=True)
                    df.columns = [str(h) if str(h) not in ("", "nan", "None") else f"Col_{i}"
                                  for i, h in enumerate(new_header)]
                    break

        # ── Étape 1 : lignes vides AVANT ────────────────────────────
        if config.delete_all_empty_line:
            empty_mask = df.apply(_is_empty_row, axis=1)
            df = df[~empty_mask].reset_index(drop=True)
        else:
            if config.delete_empty_line_before_header:
                empty_mask = df.apply(_is_empty_row, axis=1)
                non_empty = (~empty_mask).idxmax() if (~empty_mask).any() else None
                if non_empty is not None and non_empty > df.index[0]:
                    df = df.loc[non_empty:].reset_index(drop=True)

            if config.delete_empty_line_after_header:
                empty_mask = df.apply(_is_empty_row, axis=1)
                non_empty_rev = (~empty_mask[::-1]).idxmax() if (~empty_mask).any() else None
                if non_empty_rev is not None and non_empty_rev < df.index[-1]:
                    df = df.loc[:non_empty_rev].reset_index(drop=True)

        # ── Étape 2 : colonnes Unnamed ──────────────────────────────
        # Une colonne "Unnamed" est une colonne sans en-tête.
        #  • Si TOUTES les colonnes sont Unnamed, c'est que le vrai header
        #    se trouve plus bas (ligne d'en-tête vide) : on ne supprime rien
        #    ici — c'est le rôle de `auto_header` / la suppression de lignes
        #    vides, sinon on effacerait tout le tableau.
        #  • Sinon (cas partiel), on retire les colonnes sans nom, qu'elles
        #    contiennent des données ou non — ce sont des colonnes parasites.
        if config.delete_unamed_column:
            unnamed_cols = [
                c for c in df.columns
                if str(c).startswith("Unnamed") or str(c).strip() == ""
            ]
            if unnamed_cols and len(unnamed_cols) < len(df.columns):
                df = df.drop(columns=unnamed_cols, errors="ignore")

        return df

    # ──────────────────────────────────────────────────────────────
    # FIELDS
    # ──────────────────────────────────────────────────────────────

    def _resolve_name(self, candidate: str, ctx: dict[str, str]) -> str:
        from app.services.compute_service import resolve_name
        return resolve_name(candidate, ctx)

    def _find_column(self, df: pd.DataFrame, field: FieldConfig,
                     ctx: dict[str, str] | None = None) -> str | None:
        # Résout d'abord les jetons/variables/fonctions dans les noms candidats
        # (ex. [MOIS_COURT] -> "juin", =LEFT([MOIS_NOM],4)) pour retrouver une
        # colonne dont le nom varie. Puis cherche par nom, puis par mapping.
        ctx = ctx or {}
        for n in (field.name or []):
            resolved = self._resolve_name(n, ctx) if ("[" in n or n.strip().startswith("=")) else n
            if resolved in df.columns:
                return resolved
        if field.mapping and field.mapping in df.columns:
            return field.mapping
        return None

    def _clean_column(self, series: pd.Series, field: FieldConfig) -> pd.Series:
        """
        Pipeline de nettoyage dans l'ordre :
          1. Trim (espaces début/fin) — toujours en premier
          2. Normalisation de casse (si configurée)
          3. Supprimer le délimiteur
          4. Séparateur de milliers
          5. Séparateur décimal
          6. Format date (source → cible)
          7. Auto format date (si auto_date_format=True)
        """
        target_fmt = field.format_clean or "%Y-%m-%d"

        fns = [
            ("trim",              field.trim if hasattr(field, "trim") else True),
            ("normalize_case",    field.normalize_case if hasattr(field, "normalize_case") else None),
            ("delimiteur",        field.delimiteur),
            ("separator_mile",    field.separator_mile),
            ("separator_decimal", field.separator_decimal),
            ("format",            (field.format, field.format_clean) if (field.format and not field.auto_date_format) else None),
            ("auto_date_format",  target_fmt if field.auto_date_format else None),
        ]

        def _clean(val):
            if pd.isna(val) or str(val).strip() in ("", "None", "nan"):
                return val
            _, result = self._fn.clean_all_functions_for_an_item(fns, str(val))
            return result

        return series.apply(_clean)

    def _validate_column(self, series: pd.Series, field: FieldConfig) -> pd.Series:
        """
        Pipeline de validation sur la valeur POST-nettoyage :
          nullable → length → on_list → regex → check_type

        check_nullable reçoit field.nullable (bool).
        Si nullable=False et la valeur est vide après nettoyage → erreur.
        check_on_list ignore les valeurs vides (gérées par nullable).
        """
        fns = [
            ("nullable",   field.nullable),
            ("length",     field.length),
            ("on_list",    field.on_list),
            ("regex",      field.regex),
            ("check_type", field.type if field.check_type else None),
        ]

        def _validate(val):
            # Normaliser : NaN, None → chaîne vide pour la validation
            v = "" if (pd.isna(val) if not isinstance(val, str) else False) else str(val).strip()
            return self._fn.check_all_functions_for_an_item(fns, v)

        return series.apply(_validate)

    def apply_field_configs(
        self,
        df: pd.DataFrame,
        fields: list[FieldConfig],
        tco_df: pd.DataFrame | None = None,
        variables: dict[str, str] | None = None,
    ) -> tuple[pd.DataFrame, dict[str, pd.Series], list[str]]:
        df = df.copy()
        validation: dict[str, pd.Series] = {}
        warnings: list[str] = []

        from app.services.compute_service import dynamic_tokens
        ctx = {**dynamic_tokens(), **(variables or {})}

        for field in fields:
            col = self._find_column(df, field, ctx)
            if col is None:
                continue

            # Remplacement TCO → la valeur source est remplacée par son label
            if getattr(field, "tco_replace", False):
                if tco_df is not None:
                    new_series, val_series = self._tco.replace_series(tco_df, df[col])
                    df[col] = new_series
                    validation[col] = val_series
                else:
                    validation[col] = pd.Series(["NO_TCO"] * len(df), index=df.index)
                continue

            # Mapping TCO (validation) → pas de clean/validate classique
            if field.tco_mapping:
                if tco_df is not None:
                    validation[col] = self._tco.lookup_series(
                        tco_df, df[col], field.tco_mapping
                    )
                else:
                    validation[col] = pd.Series(
                        ["NO_TCO"] * len(df), index=df.index
                    )
                continue

            # Nettoyage
            df[col] = self._clean_column(df[col], field)

            # Renommage (seulement si demandé ET activé pour l'affichage).
            # Si la cible existe déjà sous un autre nom, on N'écrase PAS : on
            # garde le nom d'origine et on prévient (sinon doublon de colonnes
            # → plantage). L'utilisateur peut désactiver la colonne en conflit
            # ou choisir un autre nom.
            do_rename = bool(field.mapping) and getattr(field, "rename_output", True)
            final_col = col
            if do_rename and field.mapping != col:
                if field.mapping in df.columns:
                    warnings.append(
                        f"« {col} » n'a pas pu être renommé en « {field.mapping} » : "
                        f"une colonne « {field.mapping} » existe déjà. Nom d'origine conservé."
                    )
                else:
                    df = df.rename(columns={col: field.mapping})
                    final_col = field.mapping
            elif do_rename:
                final_col = field.mapping  # mapping == col, no-op rename

            # Validation
            validation[final_col] = self._validate_column(df[final_col], field)

        return df, validation, warnings

    # ──────────────────────────────────────────────────────────────
    # CLEAN MASK
    # ──────────────────────────────────────────────────────────────

    def compute_clean_mask(
        self,
        df_before: pd.DataFrame,
        df_after: pd.DataFrame,
        fields: list[FieldConfig],
    ) -> dict[str, pd.Series]:
        """
        Une cellule est « nettoyée » quand sa valeur a réellement changé.

        Les valeurs vides (NaN, None, "", "nan") sont normalisées en "" des deux
        côtés AVANT comparaison : sinon `NaN != NaN` (toujours vrai en pandas)
        marquerait à tort toute cellule vide comme nettoyée. Une valeur vide qui
        reste vide n'est donc pas considérée comme nettoyée.
        """
        def _norm(series: pd.Series) -> pd.Series:
            return series.map(
                lambda v: "" if (
                    v is None
                    or (isinstance(v, float) and pd.isna(v))
                    or str(v) in ("nan", "NaT", "None", "<NA>")
                ) else str(v)
            )

        mask: dict[str, pd.Series] = {}
        for field in fields:
            if field.tco_mapping:
                continue
            orig  = next((n for n in (field.name or []) if n in df_before.columns), None)
            if orig is None:
                continue
            final = field.mapping if (field.mapping and getattr(field, "rename_output", True)) else orig
            if orig in df_before.columns and final in df_after.columns:
                before = _norm(df_before[orig]).reindex(df_after.index, fill_value="")
                after  = _norm(df_after[final])
                mask[final] = before != after
        return mask

    # ──────────────────────────────────────────────────────────────
    # STATS
    # ──────────────────────────────────────────────────────────────

    def compute_stats(
        self,
        df: pd.DataFrame,
        validation: dict[str, pd.Series],
        clean_mask: dict[str, pd.Series],
    ) -> dict:
        n = len(df)
        per_col: dict[str, dict] = {}
        for col in df.columns:
            v = validation.get(col, pd.Series(["OK"] * n))
            err   = int((v != "OK").sum())
            clean = int(clean_mask.get(col, pd.Series([False] * n, dtype=bool)).sum())
            per_col[col] = {"errors": err, "cleans": clean}

        rows_err   = int(pd.DataFrame(validation).ne("OK").any(axis=1).sum()) \
                     if validation else 0
        rows_clean = int(pd.DataFrame(clean_mask).any(axis=1).sum()) \
                     if clean_mask else 0

        return {
            "total_rows": n,
            "rows_err":   rows_err,
            "rows_clean": rows_clean,
            "per_col":    per_col,
        }

    # ──────────────────────────────────────────────────────────────
    # RAPPORT
    # ──────────────────────────────────────────────────────────────

    def generate_report(
        self,
        df_before: pd.DataFrame,
        df_after: pd.DataFrame,
        validation: dict[str, pd.Series],
        clean_mask: dict[str, pd.Series],
        fields: list[FieldConfig],
        identifier_fields: list[str] | None = None,
        flagged_only: bool = False,
    ) -> pd.DataFrame:
        """
        Rapport plat : une ligne par cellule traitée.

        Vectorisé : on travaille colonne par colonne sur des listes Python
        (`.tolist()`) plutôt qu'avec des accès `.at[]` cellule par cellule —
        ce qui faisait exploser le temps sur les fichiers larges (400 colonnes).

        flagged_only=True n'émet que les cellules à problème ou modifiées
        (ERROR / CLEANED / MAPPING_* / NO_TCO). Les cellules OK sont déjà
        comptées dans les stats : inutile de les transporter par milliers.

        identifier_fields : un ou plusieurs champs id ; l'id de chaque ligne est
        alors la concaténation de leurs valeurs (« A1 | 2024 »).
        """
        col_map: dict[str, str] = {}
        for f in fields:
            orig = next((n for n in (f.name or []) if n in df_before.columns), None)
            if orig:
                col_map[orig] = f.mapping if (f.mapping and getattr(f, "rename_output", True)) else orig

        idx = df_after.index
        n = len(idx)

        id_cols = [c for c in (identifier_fields or []) if c in df_before.columns]
        if id_cols:
            parts = [df_before[c].reindex(idx).map(lambda v: "" if pd.isna(v) else str(v)).tolist()
                     for c in id_cols]
            ids = [" | ".join(vals) for vals in zip(*parts)]
        else:
            ids = list(idx)

        cols = ["id", "colonne", "valeur_originale", "valeur_finale", "resultat", "statut"]
        records: list[dict] = []

        for orig_col, final_col in col_map.items():
            before = (df_before[orig_col].reindex(idx).astype(str).tolist()
                      if orig_col in df_before.columns else [""] * n)
            after = (df_after[final_col].astype(str).tolist()
                     if final_col in df_after.columns else list(before))

            res_s = validation.get(final_col)
            results = (res_s.reindex(idx, fill_value="OK").astype(str).tolist()
                       if res_s is not None else ["OK"] * n)
            clean_s = clean_mask.get(final_col)
            cleans = (clean_s.reindex(idx, fill_value=False).tolist()
                      if clean_s is not None else [False] * n)

            for i in range(n):
                res = results[i]
                if res.startswith("MAPPING OK"):
                    statut = "MAPPING_OK"
                elif res.startswith("MAPPING KO"):
                    statut = "MAPPING_KO"
                elif res == "NO_TCO":
                    statut = "NO_TCO"
                elif res != "OK":
                    statut = "ERROR"
                elif cleans[i]:
                    statut = "CLEANED"
                else:
                    statut = "OK"

                if flagged_only and statut == "OK":
                    continue

                records.append({
                    "id":               ids[i],
                    "colonne":          final_col,
                    "valeur_originale": before[i],
                    "valeur_finale":    after[i],
                    "resultat":         res,
                    "statut":           statut,
                })

        return pd.DataFrame(records, columns=cols)

    # ──────────────────────────────────────────────────────────────
    # FILTRE LIGNES
    # ──────────────────────────────────────────────────────────────

    def filter_rows(
        self,
        df: pd.DataFrame,
        col: str | None,
        validation: dict[str, pd.Series],
        clean_mask: dict[str, pd.Series],
    ) -> pd.DataFrame:
        if col is None or col not in df.columns:
            return df
        err   = validation.get(col, pd.Series("OK",  index=df.index)) != "OK"
        clean = clean_mask.get(col, pd.Series(False, index=df.index))
        return df[err | clean]

    # ──────────────────────────────────────────────────────────────
    # PIPELINE COMPLET
    # ──────────────────────────────────────────────────────────────

    def run_pipeline(
        self,
        df_edited: pd.DataFrame,
        visible_cols: list[str],
        field_configs: dict[str, FieldConfig],
        tco_df: pd.DataFrame | None = None,
        identifier_fields: list[str] | None = None,
        computed: list[tuple[str, str]] | None = None,
        sql_computed: list[tuple[str, str]] | None = None,
        attached: dict[str, pd.DataFrame] | None = None,
        sensitive_cols: frozenset = frozenset(),
        report_flagged_only: bool = False,
        variables: dict[str, str] | None = None,
    ) -> dict:
        fields = [
            field_configs[c] for c in visible_cols
            if c in field_configs and c in df_edited.columns
        ]
        df_post, validation, warnings = self.apply_field_configs(df_edited.copy(), fields, tco_df, variables=variables)
        clean_mask          = self.compute_clean_mask(df_edited, df_post, fields)

        # Cross-source SQL — the one thing the expression engine below cannot
        # do (it sees exactly one frame). Runs first so a plain computed
        # column can reference a column a SQL block just added.
        computed_names: list[str] = []
        compute_errors: dict[str, str] = {}
        if sql_computed:
            from app.services.duck_compute import run_sql_computed
            df_post, sql_cols, sql_errors = run_sql_computed(
                df_post, attached or {}, sql_computed, sensitive_cols=sensitive_cols)
            computed_names.extend(sql_cols)
            compute_errors.update(sql_errors)

        # Computed columns — evaluated on cleaned values, appended to the frame.
        if computed:
            from app.services.compute_service import ComputeService, ComputeError
            cs = ComputeService()
            # source value -> label map from the TCO, backing the LOOKUP() function
            lookup_map: dict[str, str] = {}
            if tco_df is not None:
                try:
                    lookup_map = self._tco.as_lookup_map(tco_df)
                except Exception:  # noqa: BLE001
                    lookup_map = {}
            for name, expr in computed:
                if not name:
                    continue
                try:
                    df_post[name] = cs.evaluate(df_post, expr, lookup_map=lookup_map, variables=variables or {})
                    computed_names.append(name)
                except ComputeError as e:
                    compute_errors[name] = str(e)

        stats               = self.compute_stats(df_post, validation, clean_mask)
        report              = self.generate_report(
            df_edited, df_post, validation, clean_mask, fields, identifier_fields,
            flagged_only=report_flagged_only,
        )

        # TCO coverage: distinct source values that the TCO does NOT cover,
        # per mapped column, with counts — the actionable "add these" list.
        tco_uncovered: dict[str, list[dict]] = {}
        if len(report):
            ko = report[report["statut"] == "MAPPING_KO"]
            for col, grp in ko.groupby("colonne"):
                vc = grp["valeur_finale"].astype(str).value_counts()
                tco_uncovered[str(col)] = [
                    {"value": str(v), "count": int(c)} for v, c in vc.items()
                ]

        return {
            "df":              df_post,
            "validation":      validation,
            "clean_mask":      clean_mask,
            "stats":           stats,
            "report":          report,
            "computed_names":  computed_names,
            "compute_errors":  compute_errors,
            "tco_uncovered":   tco_uncovered,
            "warnings":        warnings,
        }
