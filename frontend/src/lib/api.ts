// Typed client for the File Explorer API. One function per endpoint.

import type {
  ArtefactInfo,
  AuthUser,
  AvailableVariable,
  EnvProfile,
  TcoSuggestRow,
  RunRow,
  VariableRow,
  MappingSuggestion,
  PivotObjectResponse,
  DatasetInfo,
  DatasetWriteLog,
  RowsMutationResponse,
  WriteResponse,
  EdiConvertResponse,
  EdiDownload,
  EdiGenerateResponse,
  EdiInferResponse,
  EdiInspectResponse,
  EdiKb,
  EdiPivotPreview,
  EdiValidateResponse,
  EditCellsResponse,
  FlowInfo,
  RunInfo,
  RunDetail,
  FieldConfig,
  FileResponse,
  HeaderConfig,
  ImportResponse,
  Presets,
  ProcessResponse,
  RowsResponse,
  SourceInfo,
  TablePreview,
  TcoResponse,
  VariableSchema,
} from "./types";

// Body for the YAML export endpoint.
export interface ExportArgsLocal {
  type: string;
  encoding: string | null;
  delimiter: string;
  sheet?: string | null;
  strict_header?: boolean;
  min_header?: boolean;
  variables?: Record<string, string>;
  ref_variables?: string[];
  table_marker?: string | null;
  table_index?: number;
  table_header_mode?: string;
  header: HeaderConfig;
  fields: Record<string, FieldConfig>;
  visible_cols: string[];
  filters?: Record<string, string>;
}

/**
 * How an EDI model is passed to the backend: either inline YAML (the editor)
 * or a reference into the versioned library (id + optional pinned version).
 */
export interface DatasetWriteBody {
  dataset_id?: string | null;
  name?: string;
  description?: string;
  mode: "replace" | "append" | "upsert";
  policy: "reject" | "block" | "all";
  key_fields: string[];
  columns: string[];
  source_name?: string;
}

export type EdiModelRef =
  | { yaml: string }
  | { id: string; version?: number | null };

function applyModel(fd: FormData, m: EdiModelRef, prefix = "model") {
  if ("yaml" in m) { fd.append(`${prefix}_yaml`, m.yaml); return; }
  fd.append(`${prefix}_id`, m.id);
  if (m.version) fd.append(`${prefix}_version`, String(m.version));
}

/** base64 payload -> browser download, used by every EDI export. */
export function downloadBase64(d: { filename: string; media_type: string; content_base64: string }) {
  const bin = atob(d.content_base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([bytes], { type: d.media_type }));
  a.download = d.filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

const BASE = "/api";

/**
 * The environment every library call is scoped to. Held here rather than passed
 * from each component: forgetting it in one place must not silently show
 * another environment's material.
 */
let CURRENT_ENV = "default";

/**
 * The session token. Sent on every request: the server resolves the caller's
 * environments from it, so the `?env=` parameter only *proposes* while the
 * token decides.
 */
let TOKEN = "";
export const setToken = (t: string) => {
  TOKEN = t || "";
  try { if (t) sessionStorage.setItem("fx_tok", t); else sessionStorage.removeItem("fx_tok"); }
  catch { /* private mode: the token simply does not survive a reload */ }
};
export const getToken = () => {
  if (!TOKEN) { try { TOKEN = sessionStorage.getItem("fx_tok") || ""; } catch { /* ignore */ } }
  return TOKEN;
};
export const setEnvironment = (e: string) => { CURRENT_ENV = e || "default"; };
export const getEnvironment = () => CURRENT_ENV;

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const tok = getToken();
  return tok ? { ...extra, Authorization: `Bearer ${tok}` } : extra;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/** A file download that needs the bearer token, so it can't be a plain
 * `<a href>` navigation (no way to attach a header to that) — fetch it as
 * this user, then hand the browser a blob to save. */
async function downloadBlob(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match ? match[1] : fallbackName;
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export const api = {
  presets: () => fetch(`${BASE}/presets`, { headers: authHeaders() }).then((r) => json<Presets>(r)),

  upload: (file: File, opts: { type: string; encoding: string; delimiter: string; sheet?: string; tableMarker?: string; tableIndex?: number; tableHeaderMode?: string }) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("file_type", opts.type);
    fd.append("encoding", opts.encoding);
    fd.append("delimiter", opts.delimiter);
    if (opts.sheet) fd.append("sheet", opts.sheet);
    if (opts.tableMarker) fd.append("table_marker", opts.tableMarker);
    if (opts.tableMarker) fd.append("table_index", String(opts.tableIndex ?? 0));
    if (opts.tableMarker) fd.append("table_header_mode", opts.tableHeaderMode ?? "local");
    // `env` only *proposes* — membership decides — but omitting it defaults
    // the server to "default", silently refusing anyone whose only
    // environment is something else.
    return fetch(`${BASE}/files?env=${encodeURIComponent(CURRENT_ENV)}`,
      { method: "POST", body: fd, headers: authHeaders() }).then((r) =>
      json<FileResponse>(r),
    );
  },

  applyHeader: (sid: string, header: HeaderConfig) =>
    fetch(`${BASE}/files/${sid}/header`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ header }),
    }).then((r) => json<TablePreview>(r)),

  uploadTco: (sid: string, file: File, delimiter: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("delimiter", delimiter);
    return fetch(`${BASE}/files/${sid}/tco`, { method: "POST", body: fd, headers: authHeaders() }).then((r) =>
      json<TcoResponse>(r),
    );
  },

  /** Attach a TCO artefact from the library, by reference — the shared,
   * admin-maintained table an environment was granted read access to,
   * rather than a raw file the operator has to have handy. */
  attachTcoFromArtefact: (sid: string, artefactId: string, versionNo?: number) =>
    fetch(`${BASE}/files/${sid}/tco/from-artefact`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ artefact_id: artefactId, version_no: versionNo ?? null }),
    }).then((r) => json<TcoResponse>(r)),

  /** Report and export are downloads, not JSON — token goes via `fetch`,
   * never a bare `<a href>` (a plain navigation can't carry a header). */
  downloadReport: (sid: string, qs: string, fallbackName = "report") =>
    downloadBlob(`${BASE}/files/${sid}/report?${qs}&env=${encodeURIComponent(CURRENT_ENV)}`, fallbackName),

  downloadExport: (sid: string, qs: string, fallbackName = "export") =>
    downloadBlob(`${BASE}/files/${sid}/export?${qs}&env=${encodeURIComponent(CURRENT_ENV)}`, fallbackName),

  process: (
    sid: string,
    body: {
      visible_cols: string[];
      fields: Record<string, FieldConfig>;
      identifier_field: string | null;
      computed: { name: string; expression: string }[];
      sql_computed?: { name: string; expression: string }[];
      style_rules?: { column: string; expression: string }[];
      variables?: Record<string, string>;
      ref_variables?: string[];
    },
  ) =>
    fetch(`${BASE}/files/${sid}/process?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<ProcessResponse>(r)),

  /** Extra frames attached to a session for cross-source SQL. */
  listSources: (sid: string) =>
    fetch(`${BASE}/files/${sid}/sources`, { headers: authHeaders() }).then((r) => json<SourceInfo[]>(r)),

  attachDatasetSource: (sid: string, name: string, datasetId: string) =>
    fetch(`${BASE}/files/${sid}/sources/dataset`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, dataset_id: datasetId }),
    }).then((r) => json<SourceInfo>(r)),

  /** Another open tab's working table, attached as a source — same
   * mechanism as a dataset or an external source, a snapshot at attach
   * time. */
  attachSessionSource: (sid: string, name: string, sourceSid: string) =>
    fetch(`${BASE}/files/${sid}/sources/session`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, source_sid: sourceSid }),
    }).then((r) => json<SourceInfo>(r)),

  /** Free the session's row — the multi-tab bar's "close" action. */
  dropSession: (sid: string) =>
    fetch(`${BASE}/files/${sid}`, { method: "DELETE", headers: authHeaders() })
      .then((r) => json<{ dropped: string }>(r)),

  attachUploadSource: (sid: string, name: string, file: File) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("file", file);
    return fetch(`${BASE}/files/${sid}/sources/upload`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<SourceInfo>(r));
  },

  attachExternalDbSource: (sid: string, name: string, connection: string, query: string,
                          params: Record<string, string>, schemaName?: string) =>
    fetch(`${BASE}/files/${sid}/sources/external_db?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, connection, query, params, schema_name: schemaName ?? null }),
    }).then((r) => json<SourceInfo>(r)),

  attachApiSource: (sid: string, name: string, connection: string, path: string, method: string,
                   responseKind: string, dataPath: string, body?: Record<string, unknown>,
                   schemaName?: string) =>
    fetch(`${BASE}/files/${sid}/sources/api?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, connection, path, method, response_kind: responseKind,
                            data_path: dataPath, body: body ?? null, schema_name: schemaName ?? null }),
    }).then((r) => json<SourceInfo>(r)),

  /** Run another stored flow right now and attach its output — the flow
   * needs its own fixed source (source_dataset_id), nothing here uploads a
   * file on its behalf. */
  attachFlowSource: (sid: string, name: string, flowId: string) =>
    fetch(`${BASE}/files/${sid}/sources/flow?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, flow_id: flowId }),
    }).then((r) => json<SourceInfo>(r)),

  detachSource: (sid: string, name: string) =>
    fetch(`${BASE}/files/${sid}/sources/${encodeURIComponent(name)}`,
         { method: "DELETE", headers: authHeaders() }).then((r) => json<{ ok: boolean }>(r)),

  /** A single-column join key on an already-attached source, declared once
   * so a plain computed column can use [name.field] directly instead of
   * only through a SQL block. */
  setSourceKey: (sid: string, name: string, localColumn: string, sourceColumn: string) =>
    fetch(`${BASE}/files/${sid}/sources/${encodeURIComponent(name)}/key`, {
      method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ local_column: localColumn, source_column: sourceColumn }),
    }).then((r) => json<SourceInfo>(r)),

  clearSourceKey: (sid: string, name: string) =>
    fetch(`${BASE}/files/${sid}/sources/${encodeURIComponent(name)}/key`,
         { method: "DELETE", headers: authHeaders() }).then((r) => json<{ ok: boolean }>(r)),

  /** Référentiel variables pickable outside the référentiel itself. */
  listAvailableVariables: (kind = "") =>
    fetch(`${BASE}/variables/available?env=${encodeURIComponent(CURRENT_ENV)}${kind ? `&kind=${encodeURIComponent(kind)}` : ""}`,
         { headers: authHeaders() }).then((r) => json<AvailableVariable[]>(r)),

  /** Known tables (BDD externe) / endpoints (API) declared on a connection. */
  listConnectionSchemas: (variableId: string) =>
    fetch(`${BASE}/variables/${variableId}/schemas?env=${encodeURIComponent(CURRENT_ENV)}`, { headers: authHeaders() })
      .then((r) => json<VariableSchema[]>(r)),

  saveConnectionSchema: (variableId: string, body: VariableSchema) =>
    fetch(`${BASE}/variables/${variableId}/schemas`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<VariableSchema>(r)),

  deleteConnectionSchema: (variableId: string, name: string) =>
    fetch(`${BASE}/variables/${variableId}/schemas/${encodeURIComponent(name)}`,
         { method: "DELETE", headers: authHeaders() }).then((r) => json<{ ok: boolean }>(r)),

  /** Run the query/call once and propose columns + inferred types — filling
   * the schema form, not saving it. */
  detectConnectionSchema: (variableId: string, body: { query?: string; params?: Record<string, string>;
                           path?: string; method?: string; response_kind?: string;
                           data_path?: string; body?: Record<string, unknown> }) =>
    fetch(`${BASE}/variables/${variableId}/detect-schema?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<{ columns: { name: string; type: string }[] }>(r)),

  /** Start a session directly from a BDD externe query / an API answer —
   * the same doorway an upload is, alongside it. */
  createSessionFromExternalDb: (connection: string, query: string, params: Record<string, string>,
                               schemaName?: string) =>
    fetch(`${BASE}/files/from-external-db?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ connection, query, params, schema_name: schemaName ?? null }),
    }).then((r) => json<FileResponse>(r)),

  createSessionFromApi: (connection: string, path: string, method: string, responseKind: string,
                        dataPath: string, schemaName?: string, body?: Record<string, unknown>) =>
    fetch(`${BASE}/files/from-api?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ connection, path, method, response_kind: responseKind,
                            data_path: dataPath, schema_name: schemaName ?? null, body: body ?? null }),
    }).then((r) => json<FileResponse>(r)),

  getRows: (
    sid: string,
    p: { offset: number; limit: number; filters?: string; sortCol?: string; sortDir?: string },
  ) => {
    const qs = new URLSearchParams({ offset: String(p.offset), limit: String(p.limit) });
    if (p.filters) qs.set("filters", p.filters);
    if (p.sortCol) { qs.set("sort_col", p.sortCol); qs.set("sort_dir", p.sortDir ?? "asc"); }
    return fetch(`${BASE}/files/${sid}/rows?${qs.toString()}`, { headers: authHeaders() }).then((r) => json<RowsResponse>(r));
  },

  checkExpression: (expression: string) =>
    fetch(`${BASE}/expression/check`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ expression }),
    }).then((r) => json<{ ok: boolean; error: string | null }>(r)),

  exportYaml: (args: ExportArgsLocal) =>
    fetch(`${BASE}/config/export`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(args),
    }).then((r) => json<{ yaml: string }>(r)),

  editCells: (sid: string, edits: { index: number; column: string; value: string }[]) =>
    fetch(`${BASE}/files/${sid}/cells?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ edits }),
    }).then((r) => json<EditCellsResponse>(r)),

  resetCells: (sid: string) =>
    fetch(`${BASE}/files/${sid}/cells/reset`, { method: "POST" }).then((r) =>
      json<TablePreview>(r),
    ),

  // ── artefact library / flows / runs (v12) ─────────────────────
  listEnvironments: () => fetch(`${BASE}/environments`, { headers: authHeaders() })
    .then((r) => json<{ environments: string[]; default: string }>(r)),

  listArtefacts: (kind: "config" | "computed" | "tco" | "edi_model" | "mapping" | "graph" | "function" | "source",
                  opts?: { env?: string; includeArchived?: boolean }) =>
    fetch(`${BASE}/artefacts/${kind}?env=${encodeURIComponent(opts?.env ?? CURRENT_ENV)}`
         + (opts?.includeArchived ? "&include_archived=true" : ""),
         { headers: authHeaders() }).then((r) => json<ArtefactInfo[]>(r)),

  createArtefact: (kind: "config" | "computed" | "tco" | "edi_model" | "mapping" | "graph" | "function" | "source",
                   body: { name: string; description?: string; note?: string;
                           yaml?: string; computed?: { name: string; expression: string }[];
                           sql_computed?: { name: string; expression: string }[];
                           style_rules?: { column: string; expression: string }[];
                           csv?: string; environment?: string;
                           // tco: {TYPE: {dataset_id, query}} — restricts TARGET_LABEL
                           // for that type to what the query resolves, instead of free text.
                           target_sources?: Record<string, { dataset_id: string; query: string }>;
                           body?: Record<string, unknown> }) =>
    fetch(`${BASE}/artefacts/${kind}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      // The environment travels with every creation, so an artefact always
      // lands where the user is working rather than in the default one.
      body: JSON.stringify({ environment: CURRENT_ENV, ...body }),
    }).then((r) => json<ArtefactInfo>(r)),

  addArtefactVersion: (kind: string, id: string,
                       body: { note?: string; yaml?: string;
                               computed?: { name: string; expression: string }[];
                               sql_computed?: { name: string; expression: string }[];
                               style_rules?: { column: string; expression: string }[]; csv?: string;
                               target_sources?: Record<string, { dataset_id: string; query: string }>;
                               body?: Record<string, unknown> }) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/versions`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<ArtefactInfo>(r)),

  resolveTcoTargetValues: (datasetId: string, query: string) =>
    fetch(`${BASE}/environments/tco/resolve-target-values`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ dataset_id: datasetId, query }),
    }).then((r) => json<{ values: string[] }>(r)),

  getConfigYaml: (id: string, no: number) =>
    fetch(`${BASE}/artefacts/config/${id}/versions/${no}/yaml`, { headers: authHeaders() }).then((r) =>
      json<{ yaml: string; version_no: number }>(r)),

  getArtefactVersion: (kind: string, id: string, no: number) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/versions/${no}`, { headers: authHeaders() }).then((r) =>
      json<{ body: Record<string, unknown>; version_no: number }>(r)),

  archiveArtefact: (kind: string, id: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}`, { method: "DELETE", headers: authHeaders() }).then((r) => json<{ archived: string }>(r)),

  restoreArtefact: (kind: string, id: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/restore`, { method: "POST", headers: authHeaders() })
      .then((r) => json<{ restored: string }>(r)),

  /** Gone for good — every version, no history. Only ever offered once
   * something is already archived. */
  deleteArtefactPermanently: (kind: string, id: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/permanent`, { method: "DELETE", headers: authHeaders() })
      .then((r) => json<{ deleted: string }>(r)),

  getArtefact: (kind: string, id: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}`, { headers: authHeaders() }).then((r) => json<{ body: unknown }>(r)),

  listFlows: (includeArchived = false) =>
    fetch(`${BASE}/flows${includeArchived ? "?include_archived=true" : ""}`, { headers: authHeaders() })
      .then((r) => json<FlowInfo[]>(r)),

  createFlow: (body: {
    name: string; description?: string;
    config_artefact_id: string; config_version_no?: number | null;
    tco_artefact_id?: string | null; computed_artefact_id?: string | null;
    source_artefact_id?: string | null;
    default_export_filename?: string;
    source_dataset_id?: string | null;
  }) =>
    fetch(`${BASE}/flows`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<FlowInfo>(r)),

  archiveFlow: (id: string) =>
    fetch(`${BASE}/flows/${id}`, { method: "DELETE", headers: authHeaders() }).then((r) => json<{ archived: string }>(r)),

  restoreFlow: (id: string) =>
    fetch(`${BASE}/flows/${id}/restore`, { method: "POST", headers: authHeaders() })
      .then((r) => json<{ restored: string }>(r)),

  deleteFlowPermanently: (id: string) =>
    fetch(`${BASE}/flows/${id}/permanent`, { method: "DELETE", headers: authHeaders() })
      .then((r) => json<{ deleted: string }>(r)),

  /** `file` is omitted when the flow has a fixed source table — it is then
   * read fresh, server-side, with no upload at all. */
  runFlow: (id: string, file?: File | null, exportFilename?: string) => {
    const fd = new FormData();
    if (file) fd.append("file", file);
    if (exportFilename) fd.append("export_filename", exportFilename);
    return fetch(`${BASE}/flows/${id}/run`, { method: "POST", body: fd, headers: authHeaders() }).then((r) =>
      json<{ ok: boolean; stage: string; error: string | null; run_id: string;
             stats: { total_rows: number; rows_err: number; rows_clean: number } | null }>(r));
  },

  listRuns: (flowId?: string, limit = 30) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (flowId) qs.set("flow_id", flowId);
    return fetch(`${BASE}/runs?${qs.toString()}`, { headers: authHeaders() }).then((r) => json<RunInfo[]>(r));
  },

  getRun: (id: string) =>
    fetch(`${BASE}/runs/${id}`, { headers: authHeaders() }).then((r) => json<RunDetail>(r)),

  deleteRun: (id: string) =>
    fetch(`${BASE}/runs/${id}`, { method: "DELETE", headers: authHeaders() })
      .then((r) => json<{ deleted: string }>(r)),

  /** Clear a flow's run history — each run keeps its full report and export,
   * which adds up over time. `keepLatest` (default 0) keeps that many of
   * the most recent runs instead of purging everything. */
  purgeFlowRuns: (flowId: string, keepLatest = 0) =>
    fetch(`${BASE}/flows/${flowId}/runs?keep_latest=${keepLatest}`,
         { method: "DELETE", headers: authHeaders() }).then((r) => json<{ purged: number }>(r)),

  // ── EDI module (v13) ──────────────────────────────────────────
  ediKb: () => fetch(`${BASE}/edi/kb`, { headers: authHeaders() }).then((r) => json<EdiKb>(r)),

  ediInspect: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`${BASE}/edi/inspect`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<EdiInspectResponse>(r));
  },

  ediInfer: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return fetch(`${BASE}/edi/models/infer`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<EdiInferResponse>(r));
  },

  ediValidate: (file: File, model: EdiModelRef) => {
    const fd = new FormData();
    fd.append("file", file);
    applyModel(fd, model);
    return fetch(`${BASE}/edi/validate`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<EdiValidateResponse>(r));
  },

  ediPivot: (file: File, model: EdiModelRef, mode: "flat" | "linked",
             target: "preview" | "csv" | "xlsx" | "session") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    fd.append("target", target);
    applyModel(fd, model);
    return fetch(`${BASE}/edi/pivot`, { method: "POST", body: fd, headers: authHeaders() }).then(async (r) => {
      if (target === "session") return json<FileResponse>(r);
      if (target === "preview") return json<EdiPivotPreview>(r);
      return json<{ files: EdiDownload[] }>(r);
    });
  },

  ediGenerate: (file: File, model: EdiModelRef, opts: {
    group_by?: string; sender?: string; recipient?: string; interchange_ref?: string;
  }) => {
    const fd = new FormData();
    fd.append("file", file);
    applyModel(fd, model);
    Object.entries(opts).forEach(([k, v]) => { if (v) fd.append(k, v); });
    return fetch(`${BASE}/edi/generate`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<EdiGenerateResponse>(r));
  },

  ediConvert: (file: File, source: EdiModelRef, target: EdiModelRef,
               mapping: string, sender: string, recipient: string) => {
    const fd = new FormData();
    fd.append("file", file);
    applyModel(fd, source, "source");
    applyModel(fd, target, "target");
    if (mapping.trim()) fd.append("mapping", mapping);
    if (sender) fd.append("sender", sender);
    if (recipient) fd.append("recipient", recipient);
    return fetch(`${BASE}/edi/convert`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<EdiConvertResponse>(r));
  },

  ediModelYaml: (id: string, no: number) =>
    fetch(`${BASE}/edi/models/${id}/versions/${no}/yaml`, { headers: authHeaders() }).then((r) =>
      json<{ name: string; version_no: number; yaml: string }>(r)),

  /** Start a session from a schema instead of a file (v15). */
  createBlankSession: (body: { columns?: string[]; artefact_id?: string;
                               artefact_version?: number | null; rows?: number }) =>
    fetch(`${BASE}/files/blank?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<FileResponse>(r)),

  // ── identity (v24) ────────────────────────────────────────────
  authState: () => fetch(`${BASE}/auth/state`, { headers: authHeaders() })
    .then((r) => json<{ authenticated: boolean; setup_needed: boolean;
                        providers: { name: string; kind: string }[];
                        user: AuthUser | null }>(r)),

  login: (email: string, password: string) =>
    fetch(`${BASE}/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).then((r) => json<{ token: string; user: AuthUser }>(r)),

  signup: (email: string, password: string, display_name = "") =>
    fetch(`${BASE}/auth/signup`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ email, password, display_name }),
    }).then((r) => json<{ id: string; email: string; is_superadmin: boolean }>(r)),

  logout: () => fetch(`${BASE}/auth/logout`, { method: "POST", headers: authHeaders() })
    .then((r) => json<{ ok: boolean }>(r)),

  ssoStart: (name: string, redirectUri: string) =>
    fetch(`${BASE}/auth/sso/${name}/start?redirect_uri=${encodeURIComponent(redirectUri)}`,
          { headers: authHeaders() }).then((r) => json<{ url: string; state: string }>(r)),

  envMembers: (env: string) =>
    fetch(`${BASE}/admin/environments/${env}/members`, { headers: authHeaders() })
      .then((r) => json<{ user_id: string; email: string; display_name: string;
                          role: string; from_sso: boolean }[]>(r)),

  setMember: (env: string, email: string, role: string) =>
    fetch(`${BASE}/admin/environments/${env}/members`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ email, role }),
    }).then((r) => json<unknown>(r)),

  /** A profile button: call a stored flow and get its records back. */
  callFlow: (graphId: string, params: Record<string, string>) =>
    fetch(`${BASE}/graphs/${graphId}/call`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(params),
    }).then((r) => json<{ flow: string; count: number; data: unknown[] }>(r)),

  /** The whole administrative picture in one call. */
  adminOverview: () => fetch(`${BASE}/admin/overview`, { headers: authHeaders() })
    .then((r) => json<never>(r)),

  // ── administration & sandbox (v30) ────────────────────────────
  listUsers: () => fetch(`${BASE}/admin/users`, { headers: authHeaders() })
    .then((r) => json<{ id: string; email: string; display_name: string;
                        is_superadmin: boolean; active: boolean;
                        environments: Record<string, string>; sso: string[] }[]>(r)),

  /** An account and its roles in one call — the sandbox shortcut. */
  quickUser: (email: string, memberships: Record<string, string>,
              password = "motdepasse1") =>
    fetch(`${BASE}/admin/quick-user`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ email, memberships, password }),
    }).then((r) => json<{ id: string; email: string;
                          environments: Record<string, string> }>(r)),

  /** Borrow an identity to see what a role actually shows. */
  impersonate: (email: string) =>
    fetch(`${BASE}/admin/impersonate`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ email }),
    }).then((r) => json<{ token: string; as_email: string; by_email: string }>(r)),

  setUserPassword: (userId: string, password: string) =>
    fetch(`${BASE}/admin/users/${userId}/password`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ password }),
    }).then((r) => json<{ id: string; email: string }>(r)),

  deactivateUser: (userId: string) =>
    fetch(`${BASE}/admin/users/${userId}/deactivate`, { method: "POST", headers: authHeaders() })
      .then((r) => json<{ id: string; email: string; active: boolean }>(r)),

  reactivateUser: (userId: string) =>
    fetch(`${BASE}/admin/users/${userId}/reactivate`, { method: "POST", headers: authHeaders() })
      .then((r) => json<{ id: string; email: string; active: boolean }>(r)),

  deleteUser: (userId: string, confirmEmail: string) =>
    fetch(`${BASE}/admin/users/${userId}`, {
      method: "DELETE", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ confirm_email: confirmEmail }),
    }).then((r) => json<{ deleted: string }>(r)),

  listProviders: () => fetch(`${BASE}/admin/providers`, { headers: authHeaders() })
    .then((r) => json<Record<string, unknown>[]>(r)),

  saveProvider: (body: Record<string, unknown>) =>
    fetch(`${BASE}/admin/providers`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<Record<string, unknown>>(r)),

  resetEnvProfile: (name: string) =>
    fetch(`${BASE}/environments/${name}/profile`,
          { method: "DELETE", headers: authHeaders() }).then((r) => json<unknown>(r)),

  // ── environment profiles (v23) ────────────────────────────────
  envTemplates: () => fetch(`${BASE}/environments/templates`, { headers: authHeaders() })
    .then((r) => json<{ templates: { key: string; label: string; description: string;
                                     modules: string[]; config_locked: boolean }[];
                        modules: string[] }>(r)),

  envProfile: (name: string) => fetch(`${BASE}/environments/${name}/profile`, { headers: authHeaders() })
    .then((r) => json<EnvProfile>(r)),

  saveEnvProfile: (name: string, body: Partial<EnvProfile>) =>
    fetch(`${BASE}/environments/${name}/profile`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<EnvProfile>(r)),

  createEnvironment: (body: { name: string; template: string; label?: string;
                              config_artefact_id?: string; tco_artefact_id?: string }) =>
    fetch(`${BASE}/environments?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<EnvProfile>(r)),

  environmentContent: (name: string, includeArchived = false) =>
    fetch(`${BASE}/environments/${name}/content${includeArchived ? "?include_archived=true" : ""}`,
         { headers: authHeaders() })
      .then((r) => json<{
        artefacts: { id: string; kind: string; name: string; archived: boolean }[];
        datasets: { id: string; name: string; archived: boolean }[];
        keys: { id: string; name: string; active: boolean }[];
      }>(r)),

  deleteEnvironment: (name: string, body: {
    migrate_artefact_ids?: string[]; migrate_dataset_ids?: string[];
    migrate_key_ids?: string[]; target_environment?: string;
    mode: "profile_only" | "cascade"; confirm_name?: string;
  }) =>
    fetch(`${BASE}/environments/${name}`, {
      method: "DELETE", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<{ deleted: string; mode: string }>(r)),

  // ── artefact grants (v37) ──────────────────────────────────────
  listArtefactGrants: (kind: string, id: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/grants`, { headers: authHeaders() })
      .then((r) => json<{ owner_environment: string;
                          grants: { environment: string; permission: string }[] }>(r)),

  setArtefactGrant: (kind: string, id: string, environment: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/grants`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ environment }),
    }).then((r) => json<{ owner_environment: string;
                          grants: { environment: string; permission: string }[] }>(r)),

  removeArtefactGrant: (kind: string, id: string, environment: string) =>
    fetch(`${BASE}/artefacts/${kind}/${id}/grants/${environment}`,
          { method: "DELETE", headers: authHeaders() }).then((r) => json<{ revoked: string }>(r)),

  /** Turn "these values were not mapped" into rows ready to complete. */
  suggestTco: (uncovered: Record<string, { value: string; count: number }[]>,
               fieldTypes: Record<string, string>) =>
    fetch(`${BASE}/environments/tco/suggest`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ uncovered, field_types: fieldTypes }),
    }).then((r) => json<{ rows: TcoSuggestRow[]; total: number }>(r)),

  appendTco: (body: { artefact_id?: string; name?: string;
                      rows: Record<string, string>[] }) =>
    fetch(`${BASE}/environments/tco/append`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ environment: CURRENT_ENV, ...body }),
    }).then((r) => json<{ artefact_id: string; version_no: number;
                          rows: number; added: number }>(r)),

  // ── connection points & the operations table (v22) ────────────
  listVariables: (env = "", graphId = "", kind = "") =>
    fetch(`${BASE}/variables?env=${encodeURIComponent(env || CURRENT_ENV)}&graph_id=${graphId}&kind=${kind}`, { headers: authHeaders() })
      .then((r) => json<VariableRow[]>(r)),

  resolvedVariables: (env = "", graphId = "", nodeId = "") =>
    fetch(`${BASE}/variables/resolved?env=${encodeURIComponent(env || CURRENT_ENV)}`
          + `&graph_id=${graphId}&node_id=${nodeId}`)
      .then((r) => json<{ variables: Record<string, string>; secret_names: string[] }>(r)),

  saveVariable: (v: Partial<VariableRow>) =>
    fetch(`${BASE}/variables`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ environment: CURRENT_ENV, ...v }),
    }).then((r) => json<VariableRow>(r)),

  /** Try a draft connection before it is even saved. */
  testConnection: (kind: string, value: string) =>
    fetch(`${BASE}/variables/test?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ kind, value }),
    }).then((r) => json<{ ok: boolean; message: string }>(r)),

  deleteVariable: (id: string) =>
    fetch(`${BASE}/variables/${id}`, { method: "DELETE", headers: authHeaders() }).then((r) => json<unknown>(r)),

  // ── restricting a global connection point to a handful of environments ──
  listVariableRestrictions: (variableId: string) =>
    fetch(`${BASE}/variables/${variableId}/restrictions`, { headers: authHeaders() })
      .then((r) => json<{ environments: string[] }>(r)),

  setVariableRestriction: (variableId: string, environment: string) =>
    fetch(`${BASE}/variables/${variableId}/restrictions`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ environment }),
    }).then((r) => json<{ environments: string[] }>(r)),

  removeVariableRestriction: (variableId: string, environment: string) =>
    fetch(`${BASE}/variables/${variableId}/restrictions/${environment}`,
          { method: "DELETE", headers: authHeaders() })
      .then((r) => json<{ environments: string[] }>(r)),

  listOpsRuns: (status = "", limit = 50) =>
    fetch(`${BASE}/ops/runs?status=${status}&env=${encodeURIComponent(CURRENT_ENV)}&limit=${limit}`, { headers: authHeaders() })
      .then((r) => json<{ runs: RunRow[]; counts: Record<string, number> }>(r)),

  getOpsRun: (id: string) => fetch(`${BASE}/ops/runs/${id}`, { headers: authHeaders() }).then((r) => json<RunRow>(r)),

  replayRun: (id: string, mode: "same_data" | "refetch") =>
    fetch(`${BASE}/ops/runs/${id}/replay?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mode }),
    }).then((r) => json<{ ok: boolean; run: RunRow }>(r)),

  // ── v21: a hand-made session, made repeatable ─────────────────
  sessionToFlow: (sid: string, body: { name: string; save?: boolean;
                                       source?: string; dataset_name?: string }) =>
    fetch(`${BASE}/files/${sid}/to-flow?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...body, environment: CURRENT_ENV }),
    }).then((r) => json<{ graph: unknown; artefact_id: string | null;
                          skipped: string[]; steps: number }>(r)),

  // ── a session, made a correspondence table — same doorway, alongside it ──
  saveSessionAsTco: (sid: string, body: { source_column: string; target_column: string;
                     type_column?: string; type_value?: string;
                     artefact_id?: string; name?: string; description?: string }) =>
    fetch(`${BASE}/files/${sid}/to-tco?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...body, environment: CURRENT_ENV }),
    }).then((r) => json<{ artefact_id: string; version_no: number; rows: number }>(r)),

  // ── visual flows (v18) ────────────────────────────────────────
  flowBricks: () => fetch(`${BASE}/graphs/bricks`, { headers: authHeaders() })
    .then((r) => json<{ bricks: { type: string; role: "source" | "transform" | "sink" }[] }>(r)),

  validateFlow: (body: { yaml?: string; graph_id?: string }) =>
    fetch(`${BASE}/graphs/validate`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<{ ok: boolean; order: string[]; output: string;
                          unknown_types: string[] }>(r)),

  runGraph: (body: { yaml?: string; graph_id?: string; params?: Record<string, string> }) =>
    fetch(`${BASE}/graphs/run`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...body, environment: CURRENT_ENV }),
    }).then((r) => json<{ ok: boolean; output: string; meta: Record<string, unknown>;
                          trace: { node: string; type: string; ms: number;
                                   records: number; rows: number;
                                   meta?: Record<string, unknown> }[];
                          preview: { columns: string[]; data: string[][];
                                     total_rows: number } }>(r)),

  /** Run a flow and open its output as an ordinary working session — the
   * flow equivalent of `openDataset`, for crossing several sources
   * interactively (a `join`/`lookup`/`compute` with no sink). */
  adoptGraph: (body: { yaml?: string; graph_id?: string; params?: Record<string, string> }) =>
    fetch(`${BASE}/graphs/adopt`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...body, environment: CURRENT_ENV }),
    }).then((r) => json<FileResponse>(r)),

  /** The stored graph document, to reopen on the canvas. */
  loadGraph: (id: string) => fetch(`${BASE}/artefacts/graph/${id}`, { headers: authHeaders() })
    .then((r) => json<{ body: unknown }>(r)).then((d) => d.body),

  // ── mapping & central pivot (v16) ─────────────────────────────
  /** Propose a starting mapping: every source field linked to itself. */
  suggestMapping: (body: { source_kind: "flat" | "edi"; file?: File;
                           session_id?: string; dataset_id?: string;
                           edi_model_yaml?: string; edi_model_id?: string }) => {
    const fd = new FormData();
    fd.append("source_kind", body.source_kind);
    if (body.file) fd.append("file", body.file);
    if (body.session_id) fd.append("session_id", body.session_id);
    if (body.dataset_id) fd.append("dataset_id", body.dataset_id);
    if (body.edi_model_yaml) fd.append("edi_model_yaml", body.edi_model_yaml);
    if (body.edi_model_id) fd.append("edi_model_id", body.edi_model_id);
    return fetch(`${BASE}/mapping/suggest`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<MappingSuggestion>(r));
  },

  /** Turn any source into the canonical pivot object. */
  toPivotObject: (source: "flat" | "edi", fields: Record<string, string | File | undefined>) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => { if (v !== undefined && v !== "") fd.append(k, v as never); });
    return fetch(`${BASE}/pivot/from/${source}`, { method: "POST", body: fd, headers: authHeaders() })
      .then((r) => json<PivotObjectResponse>(r));
  },

  // ── editable rows (v14) ───────────────────────────────────────
  /** The working table as it stands: edits applied, deleted rows excluded. */
  rowsPreview: (sid: string, limit = 150) =>
    fetch(`${BASE}/files/${sid}/preview?limit=${limit}`, { headers: authHeaders() }).then((r) => json<TablePreview>(r)),

  addRows: (sid: string, count = 1, copyFrom?: number) =>
    fetch(`${BASE}/files/${sid}/rows/add?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ count, copy_from: copyFrom ?? null }),
    }).then((r) => json<RowsMutationResponse>(r)),

  deleteRows: (sid: string, indices: number[]) =>
    fetch(`${BASE}/files/${sid}/rows/delete?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ indices }),
    }).then((r) => json<RowsMutationResponse>(r)),

  deleteFilteredRows: (sid: string, filters: Record<string, string>, statuses: string[]) =>
    fetch(`${BASE}/files/${sid}/rows/delete?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ indices: [], all_filtered: true, filters, statuses }),
    }).then((r) => json<RowsMutationResponse>(r)),

  restoreRows: (sid: string, indices: number[] = []) =>
    fetch(`${BASE}/files/${sid}/rows/restore`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ indices }),
    }).then((r) => json<RowsMutationResponse>(r)),

  /** Move a row to sit right after another (or to the start when `after` is
   * null) — a real permutation of the session's row order. */
  reorderRow: (sid: string, index: number, after: number | null) =>
    fetch(`${BASE}/files/${sid}/rows/reorder`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ index, after }),
    }).then((r) => json<TablePreview>(r)),

  // ── datasets ──────────────────────────────────────────────────
  /** Open a stored table as an ordinary working session. */
  openDataset: (id: string) =>
    fetch(`${BASE}/datasets/${id}/open`, { method: "POST", headers: authHeaders() })
      .then((r) => json<FileResponse>(r)),

  listDatasets: () => fetch(`${BASE}/datasets`, { headers: authHeaders() }).then((r) => json<DatasetInfo[]>(r)),

  datasetRows: (id: string, offset = 0, limit = 100) =>
    fetch(`${BASE}/datasets/${id}/rows?offset=${offset}&limit=${limit}`, { headers: authHeaders() })
      .then((r) => json<TablePreview>(r)),

  datasetWrites: (id: string, limit = 20) =>
    fetch(`${BASE}/datasets/${id}/writes?limit=${limit}`, { headers: authHeaders() })
      .then((r) => json<DatasetWriteLog[]>(r)),

  archiveDataset: (id: string) =>
    fetch(`${BASE}/datasets/${id}`, { method: "DELETE", headers: authHeaders() }).then((r) => json<{ archived: string }>(r)),

  restoreDataset: (id: string) =>
    fetch(`${BASE}/datasets/${id}/restore`, { method: "POST", headers: authHeaders() })
      .then((r) => json<{ restored: string }>(r)),

  /** Gone for good — every row, no history. Only ever offered once
   * something is already archived. */
  deleteDatasetPermanently: (id: string) =>
    fetch(`${BASE}/datasets/${id}/permanent`, { method: "DELETE", headers: authHeaders() })
      .then((r) => json<{ deleted: string }>(r)),

  // ── sharing a table (v41) ─────────────────────────────────────
  listDatasetGrants: (id: string) =>
    fetch(`${BASE}/datasets/${id}/grants`, { headers: authHeaders() }).then((r) =>
      json<{ owner_id: string; is_managed: boolean; my_permission: string;
             grants: { subject_kind: string; subject: string; label: string; permission: string }[] }>(r)),

  setDatasetGrant: (id: string, body: { email?: string; role?: string; environment?: string;
                    permission?: string }) =>
    fetch(`${BASE}/datasets/${id}/grants`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<{ grants: { subject_kind: string; subject: string; label: string;
                                    permission: string }[] }>(r)),

  removeDatasetGrant: (id: string, subject: string, kind: string) =>
    fetch(`${BASE}/datasets/${id}/grants/${encodeURIComponent(subject)}?kind=${kind}`,
         { method: "DELETE", headers: authHeaders() }).then((r) => json<{ revoked: string }>(r)),

  /** Dry run: same verdict as a write, without touching anything. */
  preflightDataset: (sid: string, body: DatasetWriteBody) =>
    fetch(`${BASE}/files/${sid}/datasets/preflight`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<WriteResponse>(r)),

  writeDataset: (sid: string, body: DatasetWriteBody) =>
    fetch(`${BASE}/files/${sid}/datasets/write?env=${encodeURIComponent(CURRENT_ENV)}`, {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }).then((r) => json<WriteResponse>(r)),

  /** The file as it was really read — what you look at when the skeleton is wrong. */
  sourcePreview: (sid: string, limit = 100) =>
    fetch(`${BASE}/files/${sid}/source?limit=${limit}`, { headers: authHeaders() }).then((r) => json<TablePreview>(r)),

  importYaml: (yaml: string, columns: string[] | null) =>
    fetch(`${BASE}/config/import`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ yaml, columns }),
    }).then((r) => json<ImportResponse>(r)),
};
