"""
db_models.py
────────────
The persistence schema. Four artefact families and a run log.

Core design decision — IMMUTABLE VERSIONS
─────────────────────────────────────────
An *artefact* (a config, a computed set, a TCO) is a named container. Its
*content* lives in versioned rows that are never updated in place: "editing"
an artefact appends a new version. This is what makes a stored run
reproducible — a run records the exact version id of every input it used, so
re-reading it years later reflects the data as it was, even if the artefact has
since moved on.

  Artefact (id, kind, name, latest_version_no)
     └── ArtefactVersion (id, artefact_id, version_no, body, created_at)   ← immutable

A *flow* composes one version of each input (config + optional tco + optional
computed) under a single id. A flow may pin an exact version, or track "latest"
(version_no NULL) and resolve at run time. Either way, a *run* freezes the
resolved version ids, so the flow can evolve without rewriting history.

Bodies are stored as JSON (portable across SQLite and Postgres; on Postgres the
column is JSONB via the variant below). TCO content — potentially large — is
stored as CSV text in the body under "csv", not exploded into rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base

# JSON that becomes JSONB on Postgres and plain JSON on SQLite.
JSONBody = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


KINDS = ("config", "computed", "tco", "edi_model", "mapping", "graph", "function")


class Artefact(Base):
    """A named, versioned container. `kind` is one of config | computed | tco | edi_model."""
    __tablename__ = "artefacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # Which environment owns this artefact (rh, adv, …). "default" is the one
    # everything lands in when nothing is chosen, so an existing install keeps
    # working untouched.
    environment: Mapped[str] = mapped_column(String(64), default="default", index=True)
    # Where this one came from. Recorded because a library of thirty
    # configurations is unreadable without it: knowing that
    # "contrat-partenaireA" descends from "contrat" is the difference between a
    # family and a pile. The exact version is kept too — an ancestor keeps
    # moving, and "derived from v3" is a fact that stays true.
    derived_from: Mapped[str] = mapped_column(String(32), default="", index=True)
    derived_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    latest_version_no: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    versions: Mapped[list["ArtefactVersion"]] = relationship(
        back_populates="artefact", cascade="all, delete-orphan",
        order_by="ArtefactVersion.version_no",
    )

    # A name is unique *within* an environment: RH and ADV may each have their
    # own "clients" config without knowing about each other.
    __table_args__ = (UniqueConstraint("kind", "name", "environment",
                                       name="uq_artefact_kind_name_env"),)


class ArtefactVersion(Base):
    """One immutable snapshot of an artefact's content. Never updated."""
    __tablename__ = "artefact_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    artefact_id: Mapped[str] = mapped_column(ForeignKey("artefacts.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSONBody)         # config YAML-model dict / computed list / {csv: ...}
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    artefact: Mapped["Artefact"] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("artefact_id", "version_no", name="uq_version_no"),)


class Flow(Base):
    """A named pipeline: a config version + optional tco + optional computed.

    Each *_version_no NULL means 'track latest' — resolved when the flow runs.
    A concrete number pins that exact version forever.
    """
    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    config_artefact_id: Mapped[str] = mapped_column(ForeignKey("artefacts.id"), index=True)
    config_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)   # NULL = latest

    tco_artefact_id: Mapped[str | None] = mapped_column(ForeignKey("artefacts.id"), nullable=True)
    tco_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    computed_artefact_id: Mapped[str | None] = mapped_column(ForeignKey("artefacts.id"), nullable=True)
    computed_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    default_export_filename: Mapped[str] = mapped_column(String(200), default="export")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Run(Base):
    """A stored execution. Freezes the resolved version ids so it's reproducible,
    and keeps the report + a summary. The export payload is kept for CSV/XLSX
    downloads (small in practice; move to object storage if it ever isn't)."""
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    flow_id: Mapped[str | None] = mapped_column(ForeignKey("flows.id"), nullable=True, index=True)
    flow_name: Mapped[str] = mapped_column(String(200), default="")          # denormalised for listing
    source_name: Mapped[str] = mapped_column(String(300), default="")        # uploaded file name

    ok: Mapped[bool] = mapped_column(Boolean, index=True)
    stage: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Frozen input version ids — the reproducibility anchor.
    config_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tco_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    computed_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_error: Mapped[int] = mapped_column(Integer, default=0)
    rows_cleaned: Mapped[int] = mapped_column(Integer, default=0)

    summary: Mapped[dict] = mapped_column(JSONBody, default=dict)     # structure + stats + counts
    report: Mapped[dict] = mapped_column(JSONBody, default=dict)      # {rows: [...grouped by id...]}
    export_b64: Mapped[str | None] = mapped_column(Text, nullable=True)   # base64 export, if produced
    export_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    export_format: Mapped[str | None] = mapped_column(String(8), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


# ══════════════════════════════════════════════════════════════════════
# Datasets — cleaned data landing in the database (v14)
# ══════════════════════════════════════════════════════════════════════
# Design decision: NO RUNTIME DDL.
#
# Letting an upload create its own physical table looks convenient and rots
# fast: column names are arbitrary (accents, spaces, reserved words, dupes) so
# they need mangling, which breaks the link with the file the user is looking
# at; and schema drift on the next file forces an ALTER-or-refuse decision at
# the worst moment. Two schema authorities in one database — Alembic and
# runtime DDL — is the actual failure mode.
#
# So: one physical table, rows as JSON. Arbitrary columns come for free, the
# schema stays Alembic's business, and it works identically on SQLite and
# Postgres (where the column is JSONB and can be indexed with GIN).
#
# The dataset records the schema it was created with. That turns drift from a
# silent corruption into an explicit, catchable event.


class Dataset(Base):
    """A named table of cleaned rows, with the schema of its first write."""
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), index=True)
    environment: Mapped[str] = mapped_column(String(64), default="default", index=True)
    # Who created it. An owner always keeps full rights on their own table —
    # otherwise someone could build a table and lose access to it.
    owner_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    # A business table is maintained for everyone and defaults to closed; a
    # personal one defaults to its owner only. The flag exists so the default is
    # a decision rather than an accident.
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Schema recorded at creation: {"columns": [...], "types": {col: type},
    # "key": [col, ...]}. Compared on every later write.
    schema_json: Mapped[dict] = mapped_column(JSONBody, default=dict)

    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("name", "environment",
                                       name="uq_dataset_name_env"),)

    rows: Mapped[list["DatasetRow"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True)


class DatasetRow(Base):
    """One row. `key_hash` is the digest of the key columns — NULL when the
    dataset has no key, which is why it cannot carry a UNIQUE constraint and
    why upsert resolves duplicates in the writer instead."""
    __tablename__ = "dataset_rows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    # Position in the table. The primary key is a random uuid and a bulk insert
    # shares one timestamp, so neither can order a read-back: without this the
    # rows come out shuffled. An upsert keeps the ordinal it already had.
    ordinal: Mapped[int] = mapped_column(Integer, default=0, index=True)
    key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSONBody)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    dataset: Mapped["Dataset"] = relationship(back_populates="rows")


class DatasetWrite(Base):
    """Audit of one write attempt — including refusals, which are the
    interesting ones: they say what diverged and what to do about it."""
    __tablename__ = "dataset_writes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True)
    dataset_name: Mapped[str] = mapped_column(String(200), default="")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    mode: Mapped[str] = mapped_column(String(16))          # replace | append | upsert
    key_fields: Mapped[dict] = mapped_column(JSONBody, default=list)
    source_name: Mapped[str] = mapped_column(String(300), default="")

    ok: Mapped[bool] = mapped_column(Boolean, index=True)
    blocked_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # config | data
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    problems: Mapped[dict] = mapped_column(JSONBody, default=list)

    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    rows_deleted: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Variable(Base):
    """
    A reusable value — a connection point, a base URL, a threshold, a key.

    Scope is what makes it useful rather than merely convenient. The same name
    resolves differently depending on where it is read, most specific winning:

        brick  →  flow  →  environment  →  global

    So `api_base` can be global by default, overridden for the RH environment,
    overridden again for one flow that talks to a sandbox, and once more for a
    single brick. Nothing has to be renamed to be specialised.

    `secret` masks the value everywhere it is read back — listings, run logs,
    error messages. The value still resolves at execution; it just never travels
    to a screen or a journal.
    """
    __tablename__ = "variables"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(16), default="environment", index=True)
    environment: Mapped[str] = mapped_column(String(64), default="", index=True)
    graph_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    node_id: Mapped[str] = mapped_column(String(64), default="")
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)

    __table_args__ = (UniqueConstraint("name", "scope", "environment", "graph_id",
                                       "node_id", name="uq_variable_scope"),)


class FlowRun(Base):
    """
    One execution of a flow, kept whether it succeeded or not.

    `snapshot_json` holds what each *source* brick produced. That is what makes
    the two replay modes meaningfully different: replaying with fresh data calls
    the API again and may well behave differently, while replaying the same data
    reproduces the exact run that failed — which is what you want when
    diagnosing, and what you want again once the bug is fixed and the original
    payload is gone.
    """
    __tablename__ = "flow_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    graph_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    graph_name: Mapped[str] = mapped_column(String(200), default="")
    environment: Mapped[str] = mapped_column(String(64), default="default", index=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    params_json: Mapped[dict] = mapped_column(JSONBody, default=dict)
    graph_json: Mapped[dict] = mapped_column(JSONBody, default=dict)
    snapshot_json: Mapped[dict] = mapped_column(JSONBody, default=dict)
    messages_json: Mapped[list] = mapped_column(JSONBody, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    error_node: Mapped[str] = mapped_column(String(64), default="")
    rows_out: Mapped[int] = mapped_column(Integer, default=0)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    replay_of: Mapped[str] = mapped_column(String(32), default="", index=True)
    replay_mode: Mapped[str] = mapped_column(String(16), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)

    steps: Mapped[list["FlowRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="FlowRunStep.ordinal")


class FlowRunStep(Base):
    """One brick's execution inside a run — what the operations table shows."""
    __tablename__ = "flow_run_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id", ondelete="CASCADE"),
                                        index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    node_id: Mapped[str] = mapped_column(String(64), default="")
    node_type: Mapped[str] = mapped_column(String(32), default="")
    label: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    ms: Mapped[int] = mapped_column(Integer, default=0)
    records: Mapped[int] = mapped_column(Integer, default=0)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[dict] = mapped_column(JSONBody, default=dict)

    run: Mapped["FlowRun"] = relationship(back_populates="steps")


class EnvironmentProfile(Base):
    """
    What an environment *is* to the people who work in it.

    Scoping the library (v20) decided what an environment owns. This decides
    what it exposes: which modules appear, which configuration is imposed, what
    may still be edited, and which buttons are offered.

    The distinction matters for deployment. A data team wants every module and
    freedom over the config; an HR team wants one screen, one pinned
    configuration they cannot break, and a couple of buttons. Both are the same
    application — only the profile differs.
    """
    __tablename__ = "environment_profiles"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Modules visible here. Empty means "everything", so an environment created
    # before profiles existed keeps behaving exactly as it did.
    modules_json: Mapped[list] = mapped_column(JSONBody, default=list)
    # A pinned configuration. When `config_locked` is true the schema is imposed:
    # the file is checked against it and nothing about it can be changed.
    config_artefact_id: Mapped[str] = mapped_column(String(32), default="")
    config_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # The correspondence table the environment maintains, and whether it may.
    tco_artefact_id: Mapped[str] = mapped_column(String(32), default="")
    tco_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    # Buttons: [{label, graph_id, params, confirm}] — each one calls a flow.
    actions_json: Mapped[list] = mapped_column(JSONBody, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class User(Base):
    """
    One person, one record — whatever proves who they are.

    The mistake this design avoids: building internal accounts, then bolting SSO
    on beside them. That yields two login paths, two ways of granting roles, and
    a hole in whichever is tested less. Here a user exists once; a password and
    an SSO identity are two *proofs* attached to the same account, and roles hang
    off the account either way.
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    # Internal proof. Null when the account only ever signs in through SSO —
    # which is the point: no dormant password to leak.
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)

    identities: Mapped[list["UserIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")
    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


class UserIdentity(Base):
    """An external proof: (provider, subject) → user. A person may hold several
    — company SSO today, another IdP after a merger — without duplicating the
    account or losing its roles."""
    __tablename__ = "user_identities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="identities")

    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_identity"),)


class Membership(Base):
    """
    What a person may do, *in one environment*.

    Roles are per environment rather than global because that is how the work is
    actually organised: the same person can run files in RH and merely read in
    ADV. A global role would force the coarser of the two everywhere.
    """
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    # admin (manages this environment's members) | editor | operator | viewer
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    # True when the membership came from an SSO claim rather than a human
    # decision: it is refreshed at each login and must not be hand-edited, or the
    # next login would silently undo the edit.
    from_sso: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="memberships")

    __table_args__ = (UniqueConstraint("user_id", "environment", name="uq_membership"),)


class AuthProvider(Base):
    """
    An SSO connection, configured by the company itself.

    Stored rather than read from environment variables so a customer can add
    their own IdP without a redeploy — and `claim_mappings` is what lets them
    keep managing access in their directory: a group in the IdP becomes a role
    in an environment here.
    """
    __tablename__ = "auth_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="oidc")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[str] = mapped_column(String(200), default="")
    client_secret: Mapped[str] = mapped_column(Text, default="")
    discovery_url: Mapped[str] = mapped_column(Text, default="")
    authorize_url: Mapped[str] = mapped_column(Text, default="")
    token_url: Mapped[str] = mapped_column(Text, default="")
    jwks_url: Mapped[str] = mapped_column(Text, default="")
    issuer: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[str] = mapped_column(String(200), default="openid email profile")
    # Which claim carries the groups, and how a group maps to (environment, role).
    groups_claim: Mapped[str] = mapped_column(String(64), default="groups")
    claim_mappings: Mapped[list] = mapped_column(JSONBody, default=list)
    # Create the account on first successful sign-in, or require it to exist.
    auto_provision: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class AuthSession(Base):
    """
    A signed-in session, server-side.

    The token is a random opaque string and the *server* holds what it means:
    revoking is then a delete, not a wait for expiry. That matters here because
    the session is what proves which environments a request may touch.
    """
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    # Set when a superadmin is borrowing this identity to see what someone else
    # sees. Recorded on the session rather than inferred, so every request knows
    # it is acting on someone's behalf and the UI can never quietly forget.
    impersonated_by: Mapped[str] = mapped_column(String(32), default="")


class CryptoKey(Base):
    """
    A key protecting one or more columns.

    `wrapped_key` is the data key encrypted by the master key held *outside* the
    database: a dump of this table is useless on its own. `holders` is the access
    list itself rather than yet another role — knowing who, by name, can read
    salaries is both easier to reason about and easier to explain to a DPO than
    a permission diluted across a role hierarchy.

    Destroying a key destroys what it protected. That is deliberate: it answers
    an erasure request cleanly, and configurations depending on it then refuse to
    run instead of silently emitting gibberish.
    """
    __tablename__ = "crypto_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    environment: Mapped[str] = mapped_column(String(64), default="default", index=True)
    wrapped_key: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)

    holders: Mapped[list["KeyHolder"]] = relationship(
        back_populates="key", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("name", "environment", name="uq_key_name_env"),)


class KeyHolder(Base):
    """Who may reveal what this key protects."""
    __tablename__ = "key_holders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    key_id: Mapped[str] = mapped_column(ForeignKey("crypto_keys.id", ondelete="CASCADE"),
                                        index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    key: Mapped["CryptoKey"] = relationship(back_populates="holders")

    __table_args__ = (UniqueConstraint("key_id", "user_id", name="uq_key_holder"),)


class RevealEvent(Base):
    """
    Someone looked.

    On pay data the legal question is rarely only *who may*: it is *who did*.
    Revealing is therefore an event with a trace, not a mode that stays on — a
    mode left open stays open.
    """
    __tablename__ = "reveal_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    user_email: Mapped[str] = mapped_column(String(320), default="")
    key_name: Mapped[str] = mapped_column(String(64), index=True)
    environment: Mapped[str] = mapped_column(String(64), default="")
    columns: Mapped[list] = mapped_column(JSONBody, default=list)
    context: Mapped[str] = mapped_column(String(200), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 index=True)


class DatasetGrant(Base):
    """
    Who may do what on *one* table.

    Environment roles say what someone may do in general; this says what they
    may do here. The two are needed because tables are not interchangeable: a
    business reference table maintained by one team, and a scratch table someone
    built for themselves, cannot sensibly share a permission.

    `subject_kind` is "user" or "role", so a grant reaches one person or everyone
    holding a role in the environment — the second scales, the first handles the
    exception every real deployment eventually needs.
    """
    __tablename__ = "dataset_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(8), default="user")
    subject: Mapped[str] = mapped_column(String(64), index=True)
    # read | write | manage. manage = grant to others, and delete the table.
    permission: Mapped[str] = mapped_column(String(8), default="read")
    granted_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("dataset_id", "subject_kind", "subject",
                                       name="uq_dataset_grant"),)


class ArtefactGrant(Base):
    """
    An environment reading an artefact it does not own.

    Ownership (Artefact.environment) stays the one and only source of who may
    create a new version — grants only ever add read access, never write:
    handing out write here would split "who may version this" across two
    tables. v1 keeps `subject_kind` to "environment" (an artefact has no
    individual owner the way a dataset can) and `permission` to "read", but
    both columns stay free text rather than a SQL enum so a future kind
    doesn't need a migration to exist.
    """
    __tablename__ = "artefact_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    artefact_id: Mapped[str] = mapped_column(
        ForeignKey("artefacts.id", ondelete="CASCADE"), index=True)
    subject_kind: Mapped[str] = mapped_column(String(8), default="environment")
    subject: Mapped[str] = mapped_column(String(64), index=True)
    permission: Mapped[str] = mapped_column(String(8), default="read")
    granted_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("artefact_id", "subject_kind", "subject",
                                       name="uq_artefact_grant"),)
