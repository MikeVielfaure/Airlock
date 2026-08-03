import { IconSearch } from "../lib/icons";

interface Props {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}

/** The one search box, reused everywhere a list can grow long enough to
 * need one -- same look, same behaviour (plain substring match on name),
 * rather than each panel growing its own slightly different version. */
export function SearchInput({ value, onChange, placeholder = "Rechercher..." }: Props) {
  return (
    <div className="search-input">
      <IconSearch size={14} />
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
      {value && <button type="button" className="search-clear" onClick={() => onChange("")}>&times;</button>}
    </div>
  );
}

/** Case/accent-insensitive substring match -- "tco" should find "TCO-2026". */
export function matchesSearch(name: string, query: string): boolean {
  if (!query.trim()) return true;
  const norm = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
  return norm(name).includes(norm(query));
}
