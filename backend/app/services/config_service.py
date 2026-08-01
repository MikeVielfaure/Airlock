"""
config_service.py
─────────────────
Construction et sérialisation du FileConfig.
Aucune dépendance Streamlit — appelable depuis une API.
"""

import yaml
from app.models import FileConfig, HeaderConfig, FieldConfig


class ConfigService:

    # ──────────────────────────────────────────────────────────────
    # CONSTRUCTION DEPUIS LES VALEURS DE L'UI
    # ──────────────────────────────────────────────────────────────

    def build_header_config(
        self,
        delete_empty_before: bool = False,
        delete_empty_after: bool  = False,
        delete_all_empty: bool    = False,
        delete_unnamed: bool      = False,
        auto_header: bool         = False,
    ) -> HeaderConfig:
        """Construit un HeaderConfig depuis des valeurs brutes (booléens)."""
        return HeaderConfig(
            delete_empty_line_before_header=delete_empty_before,
            delete_empty_line_after_header=delete_empty_after,
            delete_all_empty_line=delete_all_empty,
            delete_unamed_column=delete_unnamed,
            auto_header=auto_header,
        )

    def build_file_config(
        self,
        file_type: str,
        encoding: str | None,
        delimiter: str,
        header_config: HeaderConfig,
        field_configs: dict[str, FieldConfig],
        visible_cols: list[str],
        sheet: str | None = None,
        filters: dict[str, str] | None = None,
        strict_header: bool = False,
        min_header: bool = False,
        variables: dict[str, str] | None = None,
        ref_variables: list[str] | None = None,
        table_marker: str | None = None,
        table_index: int = 0,
        table_header_mode: str = "local",
    ) -> FileConfig:
        """
        Construit un FileConfig complet.
        Seuls les champs visibles sont inclus dans Fields.
        """
        if strict_header and min_header:
            raise ValueError("strict_header et min_header sont mutuellement exclusifs.")
        fields = [
            field_configs[col]
            for col in visible_cols
            if col in field_configs
        ]
        return FileConfig(
            type=file_type,
            encoding=encoding,
            delimiter=delimiter,
            sheet=sheet,
            table_marker=table_marker,
            table_index=table_index,
            table_header_mode=table_header_mode,
            strict_header=strict_header,
            min_header=min_header,
            variables=variables or {},
            ref_variables=ref_variables or [],
            header=header_config,
            Fields=fields,
            filters=filters or {},
        )

    # ──────────────────────────────────────────────────────────────
    # SÉRIALISATION YAML
    # ──────────────────────────────────────────────────────────────

    def to_dict(self, config: FileConfig) -> dict:
        """Convertit un FileConfig en dict sérialisable (valeurs None/False omises)."""
        d: dict = {}

        if config.type:      d["type"]      = config.type
        if config.encoding:  d["encoding"]  = config.encoding
        d["delimiter"] = config.delimiter
        if config.sheet:     d["sheet"]     = config.sheet
        if getattr(config, "table_marker", None):
            d["table_marker"] = config.table_marker
            d["table_index"] = getattr(config, "table_index", 0)
            d["table_header_mode"] = getattr(config, "table_header_mode", "local")
        if getattr(config, "strict_header", False):
            d["strict_header"] = True
        if getattr(config, "min_header", False):
            d["min_header"] = True
        if getattr(config, "variables", None):
            d["variables"] = dict(config.variables)
        if getattr(config, "ref_variables", None):
            d["ref_variables"] = list(config.ref_variables)
        if getattr(config, "filters", None):
            d["filters"] = dict(config.filters)
        if config.delete_char_delimiter:
            d["delete_char_delimiter"] = config.delete_char_delimiter

        if config.header:
            h = config.header
            hd = {k: v for k, v in {
                "delete_empty_line_before_header": h.delete_empty_line_before_header,
                "delete_empty_line_after_header":  h.delete_empty_line_after_header,
                "delete_all_empty_line":           h.delete_all_empty_line,
                "delete_unamed_column":            h.delete_unamed_column,
                "auto_header":                     h.auto_header,
            }.items() if v}
            if hd:
                d["header"] = hd

        d["Fields"] = [
            {k: v for k, v in {
                "name":             f.name,
                "type":             f.type,
                "nullable":         f.nullable,
                "trim":             f.trim if not f.trim else None,  # n'écrire que si False
                "normalize_case":   f.normalize_case,
                "mapping":          f.mapping,
                "rename_output":    None if getattr(f, "rename_output", True) else False,
                "identifier":       True if f.identifiant else None,
                "format":           f.format,
                "format_clean":     f.format_clean,
                "regex":            f.regex,
                "length":           f.length,
                "on_list":          f.on_list,
                "separator_mile":   f.separator_mile or None,
                "separator_decimal":f.separator_decimal or None,
                "delimiteur":       f.delimiteur,
                "auto_date_format": True if f.auto_date_format else None,
                "check_type":       True if f.check_type else None,
                "tco_mapping":      f.tco_mapping,
                "tco_replace":      True if getattr(f, "tco_replace", False) else None,
            }.items() if v is not None}
            for f in config.Fields
        ]
        return d

    def to_yaml(self, config: FileConfig) -> str:
        """Exporte un FileConfig en chaîne YAML."""
        return yaml.dump(
            self.to_dict(config),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    # ──────────────────────────────────────────────────────────────
    # CHARGEMENT YAML
    # ──────────────────────────────────────────────────────────────

    def from_yaml(self, yaml_str: str) -> FileConfig:
        """Charge un FileConfig depuis une chaîne YAML."""
        return self._from_dict(yaml.safe_load(yaml_str))

    def from_yaml_file(self, path: str) -> FileConfig:
        """Charge un FileConfig depuis un fichier YAML."""
        with open(path, "r", encoding="utf-8") as f:
            return self._from_dict(yaml.safe_load(f))

    def _from_dict(self, data: dict) -> FileConfig:
        header = HeaderConfig(**data["header"]) if data.get("header") else None

        def _norm_field(fd: dict) -> dict:
            fd = dict(fd)
            # accept the English YAML key `identifier` (and legacy `identifiant`)
            if "identifier" in fd and "identifiant" not in fd:
                fd["identifiant"] = fd.pop("identifier")
            return fd

        fields = [FieldConfig(**_norm_field(fd)) for fd in data.get("Fields", [])]
        strict_header = bool(data.get("strict_header", False))
        min_header = bool(data.get("min_header", False))
        if strict_header and min_header:
            raise ValueError("strict_header et min_header sont mutuellement exclusifs.")
        return FileConfig(
            type=data.get("type"),
            encoding=data.get("encoding"),
            delimiter=data.get("delimiter", ";"),
            sheet=data.get("sheet"),
            table_marker=data.get("table_marker"),
            table_index=int(data.get("table_index", 0) or 0),
            table_header_mode=data.get("table_header_mode", "local"),
            strict_header=strict_header,
            min_header=min_header,
            variables=data.get("variables") or {},
            ref_variables=data.get("ref_variables") or [],
            delete_char_delimiter=data.get("delete_char_delimiter", False),
            header=header,
            Fields=fields,
            filters=data.get("filters") or {},
        )

    # ──────────────────────────────────────────────────────────────
    # MATCHING YAML → COLONNES RÉELLES
    # ──────────────────────────────────────────────────────────────

    def match_fields_to_columns(
        self,
        file_config: FileConfig,
        columns: list[str],
    ) -> dict:
        """
        Tente de matcher chaque FieldConfig contre les colonnes réelles du fichier.

        Pour chaque field, parcourt field.name[] et cherche une correspondance
        exacte dans columns (insensible à la casse en fallback).

        Retourne :
        {
          "matched":   {col_réelle: FieldConfig},   # matchés automatiquement
          "unmatched": [FieldConfig],                # à résoudre manuellement
          "unused":    [col_réelle],                 # colonnes sans config
        }
        """
        matched:   dict[str, FieldConfig] = {}
        unmatched: list[FieldConfig]      = []
        used_cols: set[str]               = set()

        col_lower = {c.lower(): c for c in columns}  # fallback casse

        # Contexte pour résoudre [TOKEN]/[variable]/fonctions dans les noms
        # candidats (ex. [MOIS_COURT] -> "juin", =LEFT([MOIS_NOM],4)).
        from app.services.compute_service import dynamic_tokens, resolve_name
        ctx = {**dynamic_tokens(), **(getattr(file_config, "variables", None) or {})}
        def _resolve(name: str) -> str:
            return resolve_name(name, ctx) if ("[" in name or name.strip().startswith("=")) else name

        for field in file_config.Fields:
            found = None

            # Les noms candidats : ceux déclarés (name[]) PLUS le nom de
            # renommage (mapping). Ainsi un fichier qui porte déjà le nouveau
            # nom (ex. déjà exporté) est reconnu. Les jetons sont résolus.
            candidates = [_resolve(n) for n in (field.name or [])]
            if field.mapping and field.mapping not in candidates:
                candidates.append(field.mapping)

            for candidate in candidates:
                # Correspondance exacte
                if candidate in columns:
                    found = candidate
                    break
                # Fallback insensible à la casse
                if candidate.lower() in col_lower:
                    found = col_lower[candidate.lower()]
                    break

            if found:
                matched[found] = field
                used_cols.add(found)
            else:
                unmatched.append(field)

        unused = [c for c in columns if c not in used_cols]

        return {
            "matched":   matched,
            "unmatched": unmatched,
            "unused":    unused,
        }

    def apply_manual_mappings(
        self,
        unmatched: list[FieldConfig],
        manual_map: dict[str, str],   # {label_field: col_réelle}
    ) -> dict[str, FieldConfig]:
        """
        Résout les fields non matchés grâce aux choix manuels de l'utilisateur.
        manual_map : {premier nom du field → colonne choisie par l'utilisateur}
        Retourne {col_réelle: FieldConfig}.
        """
        result: dict[str, FieldConfig] = {}
        for field in unmatched:
            label = (field.name or [None])[0]
            col   = manual_map.get(label)
            if col:
                result[col] = field
        return result

