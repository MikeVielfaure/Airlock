/**
 * Role names ("admin", "editor", "operator", "viewer") are the actual
 * policy identifiers — stored, sent to the API, compared server-side.
 * Renaming them there would touch the whole permission system for a
 * display-only concern. This is purely the French label shown to a
 * person; the underlying value never changes.
 */
const ROLE_LABELS: Record<string, string> = {
  admin: "Administrateur",
  editor: "Éditeur",
  operator: "Opérateur",
  viewer: "Lecteur",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}
