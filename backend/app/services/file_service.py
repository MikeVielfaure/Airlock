import pandas as pd
import io


class FileService:

    ENCODINGS_LIST = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']
    DELIMITERS_LIST = [';', '\t', '|', ',']
    ENCODING_AUTO = 'AUTO'

    def detect_encoding(self, raw_bytes: bytes) -> str:
        for enc in self.ENCODINGS_LIST:
            try:
                raw_bytes.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue
        return 'utf-8'

    def load_csv_raw(self, raw_bytes: bytes, encoding: str, delimiter) -> tuple:
        """
        Charge un CSV depuis bytes.
        Retourne (df, encoding_used, delimiter_used).
        """
        enc = encoding if encoding != self.ENCODING_AUTO else self.detect_encoding(raw_bytes)
        content = raw_bytes.decode(enc)
        buf = io.StringIO(content)

        if delimiter is None:
            df = pd.read_csv(buf, sep=None, engine='python', dtype=str, header=0)
            buf.seek(0)
            sample = buf.read(4096)
            detected_sep = ','
            for sep in self.DELIMITERS_LIST:
                if sep in sample:
                    detected_sep = sep
                    break
            delim_used = detected_sep
        else:
            df = pd.read_csv(buf, sep=delimiter, engine='python', dtype=str, header=0)
            delim_used = delimiter

        return df, enc, delim_used

    def list_sheets(self, raw_bytes: bytes) -> list:
        """Noms des onglets d'un classeur Excel."""
        buf = io.BytesIO(raw_bytes)
        return pd.ExcelFile(buf).sheet_names

    def split_tables(self, raw_bytes: bytes, sheet, marker: str) -> list:
        """
        Découpe un onglet en plusieurs tableaux séparés par une ligne
        « marqueur » : toute ligne contenant le texte `marker` dans N'IMPORTE
        quelle cellule sépare les tableaux (la ligne marqueur est retirée).

        Retourne la liste des tableaux, chacun = liste de lignes (listes de str).
        """
        buf = io.BytesIO(raw_bytes)
        if isinstance(sheet, str) and sheet.strip().lstrip("-").isdigit():
            sheet = int(sheet)
        raw = pd.read_excel(buf, dtype=str, header=None, sheet_name=sheet).fillna("")
        rows = raw.values.tolist()
        mk = marker.strip().lower()

        chunks: list[list[list[str]]] = []
        current: list[list[str]] = []
        for row in rows:
            cells = [("" if c is None else str(c)) for c in row]
            is_marker = mk != "" and any(mk in c.lower() for c in cells)
            if is_marker:
                if current:
                    chunks.append(current)
                current = []
            else:
                # ignore fully-empty rows that lead a chunk
                if any(c.strip() for c in cells) or current:
                    current.append(cells)
        if current:
            chunks.append(current)
        return chunks

    def extract_table(self, raw_bytes: bytes, sheet, marker: str,
                      index: int = 0, header_mode: str = "local") -> pd.DataFrame:
        """
        Construit le DataFrame du tableau choisi (`index`, 0-based) parmi ceux
        séparés par `marker`.

        header_mode:
          • "local"  : l'en-tête est la 1ʳᵉ ligne du tableau choisi.
          • "global" : l'en-tête est la 1ʳᵉ ligne du PREMIER tableau, partagée
                       par tous ; les autres tableaux n'ont pas de ligne d'en-tête.
        """
        chunks = self.split_tables(raw_bytes, sheet, marker)
        if not chunks:
            raise ValueError(f"Aucun tableau trouvé avec le marqueur « {marker} ».")
        if index < 0 or index >= len(chunks):
            raise ValueError(
                f"Tableau n°{index + 1} demandé, mais {len(chunks)} trouvé(s) "
                f"avec le marqueur « {marker} »."
            )

        if header_mode == "global":
            header = chunks[0][0]
            body = chunks[index][1:] if index == 0 else chunks[index]
        else:  # local
            header = chunks[index][0]
            body = chunks[index][1:]

        # Noms de colonnes : non vides, dédupliqués
        cols, seen = [], {}
        for j, h in enumerate(header):
            name = h.strip() or f"Column_{j + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            cols.append(name)

        width = len(cols)
        norm = [(r + [""] * width)[:width] for r in body]   # pad/truncate to header width
        return pd.DataFrame(norm, columns=cols, dtype=str)

    def load_xlsx_raw(self, raw_bytes: bytes, sheet=0) -> pd.DataFrame:
        """
        Charge un onglet XLSX depuis bytes.
        `sheet` peut être un nom (str) ou un index (int / chaîne numérique).
        """
        buf = io.BytesIO(raw_bytes)
        if isinstance(sheet, str) and sheet.strip().lstrip("-").isdigit():
            sheet = int(sheet)
        return pd.read_excel(buf, dtype=str, header=0, sheet_name=sheet)
