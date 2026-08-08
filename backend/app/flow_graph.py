"""
The flow graph — bricks wired together.

A flow stopped being a fixed record (one config + one tco + one computed) and
became a *graph document*: nodes that each do one thing, edges that say what
feeds what. Being a document means it is stored as an ordinary versioned
artefact, so it inherits immutable versions, pinning and archiving with no new
table and no migration.

Two properties make it compose rather than merely run:

  * **One wire format.** Every node consumes and produces the canonical pivot
    records the rest of the app already speaks. A node never needs to know which
    kind of node fed it — that is what lets any brick follow any other.

  * **A graph is a node.** A node of type `graph` runs another flow, mapping the
    outer parameters onto the inner ones. A flow that proved useful therefore
    becomes a brick in a bigger flow, parameterised, with no special casing.

Because a graph declares named `params` and yields one output, calling it over
HTTP is not a separate feature: an API route *is* a graph invocation whose
parameters come from the request.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# What a node can be. Kept explicit rather than open-ended: the runner has one
# executor per type, and an unknown type must fail loudly at save time, not
# halfway through a run.
NodeType = Literal[
    # sources — produce records from nothing upstream
    "api", "dataset", "session", "inline", "hotfolder", "external_db", "sftp",
    "log",
    # transforms — records in, records out
    "mapping", "compute", "filter", "validate", "config", "graph",
    "aggregate", "join", "lookup",
    # sinks — records in, a result out
    "response", "dataset_write", "file", "http", "email",
    "external_db_write", "sftp_write",
]

SOURCE_TYPES = {"api", "dataset", "session", "inline", "hotfolder", "external_db", "sftp"}
SINK_TYPES = {"response", "dataset_write", "file", "http", "email",
              "external_db_write", "sftp_write"}


class FlowParam(BaseModel):
    """
    A named input of the flow — and, when the flow is served over HTTP, a field
    of its request body.

    `type` and `description` exist for the contract rather than the execution:
    everything is handled as text internally, but a consumer needs to know that
    `mois` is a number and what it means. Declaring it is what allows an OpenAPI
    document to be generated instead of written by hand and left to drift.
    """
    name: str
    default: str = ""
    required: bool = False
    description: str = ""
    type: Literal["string", "number", "boolean", "date"] = "string"
    example: str = ""

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a parameter needs a name")
        return v


class FlowNode(BaseModel):
    """One brick. `config` is free-form because each type reads its own keys;
    the runner validates what it needs when it runs, and reports which node
    failed rather than dying anonymously."""
    id: str
    type: NodeType
    label: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _ident(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a node needs an id")
        if any(c.isspace() for c in v):
            raise ValueError(f"node id '{v}' must not contain whitespace")
        return v

    @field_validator("config", mode="before")
    @classmethod
    def _string_keys(cls, v):
        """
        YAML 1.1 turns bare `on`, `off`, `yes` and `no` into booleans, so a
        perfectly natural `on: [client]` in a join config arrives as the key
        True and fails validation. Coerce keys back to the words the author
        wrote instead of making every one of them discover this the hard way.
        """
        if not isinstance(v, dict):
            return v
        WORD = {True: "on", False: "off"}
        return {WORD.get(k, str(k)) if not isinstance(k, str) else k: val
                for k, val in v.items()}


class FlowEdge(BaseModel):
    """`from` feeds `to`. Named with aliases because `from` is a Python keyword
    — the YAML stays readable, which matters since humans write these."""
    src: str = Field(alias="from")
    dst: str = Field(alias="to")

    model_config = {"populate_by_name": True}


class FlowGraph(BaseModel):
    """
    A whole flow. Validation here is deliberately strict: a graph that cannot
    run should be rejected when it is *saved*, not when it is called in
    production. Cycles, dangling edges and duplicate ids are all caught now.
    """
    name: str = "flow"
    description: str = ""
    params: List[FlowParam] = Field(default_factory=list)
    nodes: List[FlowNode] = Field(default_factory=list)
    edges: List[FlowEdge] = Field(default_factory=list)
    # Which node's result the flow returns. Defaults to the single terminal node.
    output: str = ""
    # Optional: several terminal nodes to open as separate tabs at once when
    # adopted as a working session (a flow producing more than one related
    # table, rather than one). Empty is the default and changes nothing about
    # `output`/`resolve_output()` — this is additive, read only by `adopt`.
    outputs: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent(self) -> "FlowGraph":
        ids = [n.id for n in self.nodes]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate node id(s): {', '.join(sorted(dupes))}")
        known = set(ids)
        for e in self.edges:
            if e.src not in known:
                raise ValueError(f"edge from unknown node '{e.src}'")
            if e.dst not in known:
                raise ValueError(f"edge to unknown node '{e.dst}'")
            if e.src == e.dst:
                raise ValueError(f"node '{e.src}' cannot feed itself")
        if self.output and self.output not in known:
            raise ValueError(f"output names unknown node '{self.output}'")
        unknown_outputs = [o for o in self.outputs if o not in known]
        if unknown_outputs:
            raise ValueError(f"outputs name unknown node(s): {', '.join(unknown_outputs)}")
        if self.nodes:
            self.topological_order()          # raises on a cycle
        return self

    # -- graph queries ---------------------------------------------------
    def node(self, node_id: str) -> FlowNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def parents(self, node_id: str) -> List[str]:
        return [e.src for e in self.edges if e.dst == node_id]

    def children(self, node_id: str) -> List[str]:
        return [e.dst for e in self.edges if e.src == node_id]

    def terminals(self) -> List[str]:
        """Nodes nothing reads from — the candidates for the flow's output."""
        fed = {e.src for e in self.edges}
        return [n.id for n in self.nodes if n.id not in fed]

    def resolve_output(self) -> str:
        """The node whose result the flow returns. An explicit `output` wins;
        otherwise a single terminal is unambiguous, and several are not."""
        if self.output:
            return self.output
        ends = self.terminals()
        if len(ends) == 1:
            return ends[0]
        if not ends:
            raise ValueError("the graph has no terminal node")
        raise ValueError(
            f"several terminal nodes ({', '.join(sorted(ends))}) — name one in `output`")

    def topological_order(self) -> List[str]:
        """
        Execution order: a node runs only once everything feeding it has run.
        Kahn's algorithm, and the leftover nodes at the end *are* the cycle —
        naming them is far more useful than reporting that one exists.
        """
        indeg = {n.id: 0 for n in self.nodes}
        for e in self.edges:
            indeg[e.dst] = indeg.get(e.dst, 0) + 1
        ready = [i for i, d in indeg.items() if d == 0]
        order: List[str] = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for child in self.children(cur):
                indeg[child] -= 1
                if indeg[child] == 0:
                    ready.append(child)
        if len(order) != len(self.nodes):
            stuck = sorted(set(indeg) - set(order))
            raise ValueError(f"the graph has a cycle through: {', '.join(stuck)}")
        return order


def graph_to_yaml(g: FlowGraph) -> str:
    return yaml.safe_dump(g.model_dump(by_alias=True, exclude_none=True),
                          allow_unicode=True, sort_keys=False)


def graph_from_yaml(text: str) -> FlowGraph:
    data = yaml.safe_load(text) or {}
    return FlowGraph(**data)
