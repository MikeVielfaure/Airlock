// Mirrors the backend data contract (app/models.py).

export type FieldType = "string" | "integer" | "float" | "date" | "boolean";
export type CaseMode = "upper" | "lower" | "title";

export type CellStatus =
  | "OK"
  | "CLEANED"
  | "ERROR"
  | "MAPPING_OK"
  | "MAPPING_KO"
  | "NO_TCO"
  | "COMPUTED"
  | "EDITED";      // client-side: cell changed by hand, pending re-validation

export interface ComputedColumn {
  name: string;
  expression: string;
  // Only meaningful for a SQL block: "replace" (default) overwrites the
  // target column row by row; "fill_empty" only touches blank cells.
  mode?: "replace" | "fill_empty";
}

/** An extra frame attached to a session for cross-source SQL. */
export interface SourceInfo {
  name: string;
  columns: string[];
  row_count: number;
  // The declared join key, if any — lets a plain computed column address
  // this source as [name.field] instead of only through a SQL block.
  join_local?: string | null;
  join_source?: string | null;
}

/** One row's identity, e.g. { code: "1" } or { region: "EST", code: "1" }
 * for a composite key. */
export type DiffKey = Record<string, string>;

export interface DiffSampleRow {
  key: DiffKey;
  status: "added" | "removed" | "changed";
  changes?: Record<string, { was: string; now: string }>;
}

export interface DiffResult {
  keys: string[];
  left_rows: number;
  right_rows: number;
  added: number;
  removed: number;
  changed: number;
  identical: number;
  columns_compared: string[];
  // Confidential columns are never compared — masking both sides equally
  // would make them silently read as "identical" whether or not they
  // actually are, and masking only one side would leak the other's real
  // value. Excluded outright instead, and named here so nothing is hidden
  // silently.
  columns_excluded_sensitive: string[];
  sample: DiffSampleRow[];
  truncated: boolean;
}

// A key protecting one or more confidential columns. Holders are the access
// list itself — no separate role — so a key nobody can act on is destroyed
// rather than orphaned, and adding a holder requires already holding it.
export interface CryptoKeyOut {
  id: string;
  name: string;
  label: string;
  environment: string;
  active: boolean;
  holders: { user_id: string; email: string; display_name: string }[];
  i_hold: boolean;
  created_at: string;
  revoked_at: string;
}

// One reveal, from the audit trail — who looked, at what, when.
export interface RevealEventOut {
  id: string;
  user_email: string;
  key_name: string;
  columns: string[];
  context: string;
  rows: number;
  at: string;
}

/** A référentiel variable pickable from outside the référentiel itself — a
 * calculated column's variable list, or a source-attachment connection
 * select. Secrets never appear here at all. */
export interface AvailableVariable {
  id: string;
  name: string;
  kind: string;
  value: string;
}

/** A known table (BDD externe) or endpoint (API) declared on a connection
 * point — reusable, and checked: attaching or starting a session against a
 * schema name refuses (422) if the result doesn't match. */
export interface VariableSchema {
  name: string;
  columns: { name: string; type: string }[];
  query?: string;
  path?: string;
  method?: string;
  data_path?: string;
}

export interface FieldConfig {
  name?: string[] | null;
  type: FieldType;
  format?: string | null;
  format_clean?: string | null;
  auto_date_format: boolean;
  regex?: string | null;
  length?: number | null;
  nullable: boolean;
  on_list?: (string | number)[] | null;
  separator_mile: boolean;
  separator_decimal: boolean;
  delimiteur?: string | null;
  trim: boolean;
  normalize_case?: CaseMode | null;
  mapping?: string | null;
  rename_output: boolean;
  tco_mapping?: string | null;
  tco_replace?: boolean;
  tco_type?: string | null;
  identifiant: boolean;
  check_type: boolean;
  // Name of the key protecting this column — mirrors the backend field one
  // for one. Set = never stored or shown in the clear to a non-holder.
  sensitive?: string | null;
}

export interface HeaderConfig {
  delete_empty_line_before_header: boolean;
  delete_empty_line_after_header: boolean;
  delete_all_empty_line: boolean;
  delete_unamed_column: boolean;
  auto_header: boolean;
}

export interface TablePreview {
  columns: string[];
  data: string[][];
  total_rows: number;
  shown_rows: number;
  index?: number[];              // df index per row (stable key for edits)
}

export interface FileResponse {
  session_id: string;
  type: string;
  encoding: string;
  delimiter: string;
  sheet?: string | null;
  sheets: string[];
  table_count: number;
  preview: TablePreview;
  seeded_fields?: Record<string, FieldConfig> | null;   // set when seeded from a config
}

export interface ColumnStat {
  errors: number;
  cleans: number;
}

export interface ProcessStats {
  total_rows: number;
  rows_err: number;
  rows_clean: number;
  per_col: Record<string, ColumnStat>;
}

export interface ReportRow {
  id: string | number;
  colonne: string;
  valeur_originale: string;
  valeur_finale: string;
  resultat: string;
  statut: CellStatus;
}

export interface ProcessResponse {
  columns: string[];
  data: string[][];
  status: CellStatus[][];
  // Per-cell style token ("color:x;bold:1;italic:0" or a bare color name),
  // aligned to `data` like `status` — "" means no rule applied.
  styles?: string[][];
  style_errors?: Record<string, string>;
  computed: string[];
  compute_errors: Record<string, string>;
  stats: ProcessStats;
  report: ReportRow[];
  tco_uncovered?: Record<string, { value: string; count: number; reason?: "no_tco" }[]>;
  warnings?: string[];
  index?: number[];              // df index per preview row
}

export interface RowsResponse {
  columns: string[];
  data: string[][];
  status: CellStatus[][];
  styles?: string[][];
  total: number;
  total_all: number;
  offset: number;
  limit: number;
  index: number[];               // df index per row of the page
}

/** How an existing column should look, not what it should contain. */
export interface StyleRule {
  column: string;
  expression: string;
}

export interface TcoResponse {
  rows: number;
  labels: string[];
  artefact_id?: string | null;
}

export interface Presets {
  regex_presets: Record<string, string>;
  date_formats: string[];
  field_types: FieldType[];
  encodings: string[];
  delimiters: Record<string, string | null>;
  case_modes: CaseMode[];
}

export interface MatchInfo {
  matched: Record<string, FieldConfig>;
  unmatched: FieldConfig[];
  unused: string[];
}

export interface ImportResponse {
  file_config: {
    type?: string | null;
    encoding?: string | null;
    delimiter: string;
    sheet?: string | null;
    table_marker?: string | null;
    table_index?: number;
    table_header_mode?: string;
    strict_header?: boolean;
    min_header?: boolean;
    variables?: Record<string, string>;
    ref_variables?: string[];
    header?: HeaderConfig | null;
    Fields: FieldConfig[];
    filters?: Record<string, string>;
  };
  match?: MatchInfo | null;
}

export function defaultField(colName: string): FieldConfig {
  return {
    name: [colName],
    type: "string",
    format: null,
    format_clean: null,
    auto_date_format: false,
    regex: null,
    length: null,
    nullable: true,
    on_list: null,
    separator_mile: false,
    separator_decimal: false,
    delimiteur: null,
    trim: true,
    normalize_case: null,
    mapping: null,
    rename_output: true,
    tco_mapping: null,
    tco_replace: false,
    tco_type: null,
    identifiant: false,
    check_type: false,
  };
}

export function defaultHeader(): HeaderConfig {
  return {
    delete_empty_line_before_header: false,
    delete_empty_line_after_header: false,
    delete_all_empty_line: false,
    delete_unamed_column: false,
    auto_header: false,
  };
}

export interface EditCellsResponse {
  applied: number;
  rejected: { index: number; column: string; reason: string }[];
  edits_total: number;
  stale: boolean;
}

// ── artefact library / flows / runs (v12) ───────────────────────────
export interface ArtefactInfo {
  id: string;
  environment?: string;
  kind: "config" | "computed" | "tco" | "edi_model" | "mapping" | "source";
  name: string;
  description: string;
  latest_version_no: number;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface FlowInfo {
  id: string;
  name: string;
  description: string;
  archived: boolean;
  config_artefact_id: string;
  config_version_no: number | null;
  tco_artefact_id: string | null;
  tco_version_no: number | null;
  computed_artefact_id: string | null;
  computed_version_no: number | null;
  // An extra named frame, resolved fresh on every run, for the computed
  // artefact's own sql_computed blocks to join against — never the flow's
  // input, just another source available to a join.
  source_artefact_id: string | null;
  source_version_no: number | null;
  default_export_filename: string;
  // A fixed source table, read fresh on every run — set means "Lancer"
  // needs no uploaded file; unset means the file stays required, as before.
  source_dataset_id: string | null;
}

export interface RunInfo {
  id: string;
  flow_id: string | null;
  flow_name: string;
  source_name: string;
  ok: boolean;
  stage: string;
  error: string | null;
  rows_total: number;
  rows_error: number;
  rows_cleaned: number;
  created_at: string;
}

export interface RunReportError { column: string; value: string; status: string; message: string }
export interface RunReportGroup { id: string | number; errors: RunReportError[] }

export interface RunDetail extends RunInfo {
  config_version_id: string | null;
  tco_version_id: string | null;
  computed_version_id: string | null;
  summary: Record<string, unknown>;
  report: { rows?: RunReportGroup[] };
  has_export: boolean;
  export_name: string | null;
  export_format: string | null;
}


// ── EDI module ────────────────────────────────────────────────────────
export interface EdiKb {
  segments: { tag: string; name: string; desc: string;
              elements: { path: string; label: string }[] }[];
  qualifiers: { tag: string; code: string; label: string }[];
  date_formats: { code: string; label: string }[];
  separators: { role: string; default: string; label: string; desc: string }[];
}

export interface EdiError {
  code: string;
  message: string;
  segment_pos: number;
  tag: string;
  message_no: number;
  zone: string;
  path: string;
}

export interface EdiDecodedSegment {
  pos: number;
  tag: string;
  label: string;                 // "Nom et adresse"
  qualifier_label: string;       // "Acheteur" — what NAD+BY actually means
  raw: string;
  elements: { path: string; label: string; value: string }[];
}

export interface EdiDecodedMessage {
  ref: string;
  type: string;
  directory: string;
  segment_count: number;
  segments: EdiDecodedSegment[];
}

export interface EdiInspectResponse {
  format: string;
  had_una: boolean;
  filename: string;
  syntax_errors: EdiError[];
  truncated: boolean;
  total_segments: number;
  separators: Record<string, string>;
  interchanges: {
    sender: string; recipient: string; ref: string; implicit: boolean;
    messages: EdiDecodedMessage[];
  }[];
}

export interface EdiValidateResponse {
  model_name: string;
  syntax_errors: EdiError[];
  model_errors: EdiError[];
  stats: { messages: number; items: number; errors: number };
  ok: boolean;
}

export interface EdiPivotPreview {
  mode: "flat" | "linked";
  flat?: TablePreview;
  heads?: TablePreview;
  items?: TablePreview;
}

export interface EdiDownload {
  filename: string;
  media_type: string;
  content_base64: string;
}

export interface EdiGenerateResponse {
  messages: number;
  items: number;
  file: EdiDownload;
  preview: string;
}

export interface EdiConvertResponse {
  messages: number;
  source: string;
  target: string;
  file: EdiDownload;
  preview: string;
}

export interface EdiInferResponse {
  yaml: string;
  model: Record<string, unknown>;
  notes: string[];
}


// ── datasets: landing cleaned data in the database (v14) ──────────────
export interface DatasetInfo {
  id: string;
  name: string;
  description: string;
  columns: string[];
  key: string[];
  types: Record<string, string>;
  row_count: number;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * A problem found before writing. `category` is the whole point: "config" means
 * the shape is wrong and no hand-editing will help — go look at the source;
 * "data" means specific rows are wrong and can be fixed in the Data view.
 */
export interface WriteProblem {
  severity: "error" | "warning";
  category: "config" | "data";
  code: string;
  message: string;
  hint: string;
  columns: string[];
  rows: number[];
  count: number;
}

export interface WritePlan {
  mode: string;
  policy: string;
  key_fields: string[];
  columns: string[];
  rows_in: number;
  rows_to_write: number;
  rows_rejected: number;
  existing_rows: number;
  will_delete: number;
  creates_dataset: boolean;
}

export interface WriteResponse {
  ok: boolean;
  blocked_by: "config" | "data" | null;
  problems: WriteProblem[];
  plan: WritePlan | null;
  dataset: DatasetInfo | null;
  rows_written: number;
  rows_updated: number;
  rows_rejected: number;
  rows_deleted: number;
  write_id: string;
}

export interface DatasetWriteLog {
  id: string;
  dataset_name: string;
  mode: string;
  ok: boolean;
  blocked_by: string | null;
  error: string | null;
  rows_in: number;
  rows_written: number;
  rows_updated: number;
  rows_rejected: number;
  rows_deleted: number;
  created_at: string;
}

export interface RowsMutationResponse {
  added: number;
  deleted: number;
  restored: number;
  total_rows: number;
  deleted_total: number;
  added_total: number;
  stale: boolean;              // a run happened before this change
  new_indices: number[];       // stable index of each row just added, in order
}


// ── mapping & the central pivot (v16) ────────────────────────────────
/**
 * One correspondence. A field has exactly one origin — `source` (read) or
 * `expr` (computed) — plus optional `rules`, the same constraints a cleaning
 * config declares. Mapping, computing and validating stopped being three
 * features: they are one field with two possible origins and its checks.
 */
export interface MappingLink {
  pivot: string;
  source?: string;
  expr?: string | null;
  scope: "head" | "item";
  default?: string | null;
  rules?: Record<string, unknown> | null;
}

export interface MappingDoc {
  name: string;
  source_kind: "flat" | "edi";
  source_ref?: string | null;
  description?: string;
  links: MappingLink[];
}

export interface RuleProblem {
  field: string;
  code: string;
  row: number;
  value: string;
  message: string;
}

export interface PivotChecks {
  ok: boolean;
  checked: number;
  problems: RuleProblem[];
}

export interface PivotPreview {
  columns: string[];
  data: string[][];
  total_rows: number;
  shown_rows: number;
  documents?: number;
}

export interface PivotObjectResponse {
  documents: number;
  preview: PivotPreview;
  checks: PivotChecks;
  session_id?: string;
}

export interface PivotConvertResponse {
  documents: number;
  format: "csv" | "edi";
  checks: PivotChecks;
  preview: string | PivotPreview;
  content_base64: string;
  filename: string;
  media_type: string;
}

export interface MappingSuggestion {
  yaml: string;
  mapping: MappingDoc;
  fields: string[];
}


// ── connection points & operations (v22) ─────────────────────────────
export interface VariableRow {
  id: string;
  name: string;
  value: string;                 // "••••••" when secret; JSON text when kind != "value"
  scope: "global" | "environment" | "flow" | "brick";
  environment: string;
  graph_id: string;
  node_id: string;
  secret: boolean;
  description: string;
  kind: "value" | "hotfolder" | "smtp" | "external_db" | "sftp" | "api";
}

/** Parsed shape of a "hotfolder" kind connection's JSON value. */
export interface HotfolderConnection {
  path: string;
  archive_dir: string;
  error_dir: string;
}

/** Parsed shape of an "smtp" kind connection's JSON value. Only `host` is
 * required to save; the rest default at execution time. */
export interface SmtpConnection {
  host: string;
  port?: number;
  user?: string;
  password?: string;
  use_tls?: boolean;
  from?: string;
}

/** Parsed shape of an "external_db" kind connection's JSON value — a full
 * SQLAlchemy DSN, no dialect hard-coded. */
export interface ExternalDbConnection {
  url: string;
}

/** Parsed shape of an "sftp" kind connection's JSON value — the remote
 * sibling of hotfolder's archive/error pair. */
export interface SftpConnection {
  host: string;
  port?: number;
  user: string;
  password?: string;
  private_key?: string;
  remote_dir: string;
  archive_dir: string;
  error_dir: string;
}

/** Parsed shape of an "api" kind connection's JSON value — a shared,
 * maskable base url and optional bearer/token credential. */
export interface ApiConnection {
  base_url: string;
  auth_header?: string;
  token?: string;
}

export interface RunStep {
  ordinal: number;
  node_id: string;
  type: string;
  label: string;
  status: string;
  ms: number;
  records: number;
  rows: number;
  message: string;
  meta: Record<string, unknown>;
}

export interface RunRow {
  id: string;
  graph_id: string;
  graph_name: string;
  environment: string;
  status: "running" | "success" | "error";
  ms: number;
  rows_out: number;
  error: string;
  error_node: string;
  params: Record<string, string>;
  messages: { node: string; level: string; text: string }[];
  replay_of: string;
  replay_mode: string;
  started_at: string;
  finished_at: string;
  /** Whether the exact input is still available — decides which replay modes apply. */
  has_snapshot: boolean;
  steps?: RunStep[];
}


// ── environment profiles (v23) ───────────────────────────────────────
/** What an environment exposes: modules, an imposed config, buttons. */
export interface EnvProfile {
  name: string;
  label: string;
  description: string;
  modules: string[];
  config_artefact_id: string;
  config_version_no: number | null;
  config_locked: boolean;
  tco_artefact_id: string;
  tco_editable: boolean;
  actions: { label: string; graph_id: string; params?: Record<string, string>;
             confirm?: boolean }[];
  max_open_tabs: number;
}

export interface TcoSuggestRow {
  TYPE: string;
  SOURCE_VALUE: string;
  TARGET_LABEL: string;
  count: number;
  column: string;
}


// ── identity (v24) ───────────────────────────────────────────────────
export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  is_superadmin: boolean;
  /** environment → role. What this person may do, where. */
  environments: Record<string, string>;
  /** environment -> capabilities. The UI asks instead of guessing from a role. */
  capabilities?: Record<string, string[]>;
  setup_mode: boolean;
  /** Set when a superadmin is borrowing this identity. */
  impersonated_by?: string;
}
