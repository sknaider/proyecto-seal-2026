#!/usr/bin/env python3
"""Probe determinista OFFLINE para RSI-T6 / GRAPH-ENUM.

Modela reconstrucción acumulativa de un grafo sintético mediante consultas de
vecindad individualmente legítimas. No importa código de SOUL, no abre red y no
accede a DB/Neo4j/daemons.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
THREAT_MODEL = ROOT / "T6_graph_reconstruction_threat_model.md"
DEFAULT_OUTPUT = ROOT / "t6_graph_reconstruction_probe.json"
RECONSTRUCTION_THRESHOLD = 0.80

# Fixture inventado: pseudónimos y topología sin relación con datos reales.
EDGES = (
    ("n0", "n1"), ("n0", "n2"),
    ("n1", "n3"), ("n1", "n4"),
    ("n2", "n5"), ("n2", "n6"),
    ("n3", "n7"), ("n4", "n7"),
    ("n5", "n8"), ("n6", "n8"),
    ("n7", "n9"), ("n8", "n9"),
)
NODES = tuple(f"n{i}" for i in range(10))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_edge(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


GRAPH_EDGES = frozenset(canonical_edge(*edge) for edge in EDGES)


def adjacency() -> dict[str, tuple[str, ...]]:
    rows: dict[str, list[str]] = {node: [] for node in NODES}
    for left, right in GRAPH_EDGES:
        rows[left].append(right)
        rows[right].append(left)
    return {node: tuple(sorted(neighbors)) for node, neighbors in rows.items()}


ADJACENCY = adjacency()


@dataclass(frozen=True)
class Query:
    principal: str
    purpose: str
    node: str
    page: int
    page_size: int = 2


@dataclass
class CumulativeGuard:
    """Presupuesto por principal/finalidad para una ventana sintética."""

    max_requests: int = 5
    max_unique_nodes: int = 3
    max_unique_edges: int = 5
    requests: int = 0
    queried_nodes: set[str] = field(default_factory=set)
    disclosed_edges: set[tuple[str, str]] = field(default_factory=set)

    def check_and_record(
        self, query: Query, disclosed: Iterable[tuple[str, str]]
    ) -> tuple[bool, str]:
        proposed_edges = self.disclosed_edges | set(disclosed)
        proposed_nodes = self.queried_nodes | {query.node}
        if self.requests + 1 > self.max_requests:
            return False, "cumulative_request_budget"
        if len(proposed_nodes) > self.max_unique_nodes:
            return False, "cumulative_unique_node_budget"
        if len(proposed_edges) > self.max_unique_edges:
            return False, "cumulative_edge_disclosure_budget"
        self.requests += 1
        self.queried_nodes = proposed_nodes
        self.disclosed_edges = proposed_edges
        return True, "within_cumulative_budget"


def individually_authorized(query: Query) -> bool:
    """Frontera stateless vulnerable: cada llamada parece legítima."""
    return (
        query.principal == "principal-demo"
        and query.purpose == "support_navigation"
        and query.node in ADJACENCY
        and query.page >= 0
        and 1 <= query.page_size <= 2
    )


def page_for(query: Query) -> tuple[tuple[str, str], ...]:
    neighbors = ADJACENCY[query.node]
    start = query.page * query.page_size
    selected = neighbors[start : start + query.page_size]
    return tuple(canonical_edge(query.node, neighbor) for neighbor in selected)


def query_pages(node: str, page_size: int = 2) -> range:
    count = len(ADJACENCY[node])
    return range((count + page_size - 1) // page_size)


def simulate_expansion(use_guard: bool) -> dict[str, object]:
    guard = CumulativeGuard() if use_guard else None
    queue = ["n0"]
    queued = {"n0"}
    visited: set[str] = set()
    reconstructed: set[tuple[str, str]] = set()
    trace: list[dict[str, object]] = []
    all_individually_authorized = True

    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        for page in query_pages(node):
            query = Query("principal-demo", "support_navigation", node, page)
            authorized = individually_authorized(query)
            all_individually_authorized &= authorized
            edges = page_for(query) if authorized else ()
            allowed, reason = (authorized, "stateless_authorized")
            if authorized and guard is not None:
                allowed, reason = guard.check_and_record(query, edges)
            if not authorized:
                reason = "individual_authorization_denied"
            if allowed:
                reconstructed.update(edges)
                for edge in edges:
                    neighbor = edge[1] if edge[0] == node else edge[0]
                    if neighbor not in queued:
                        queue.append(neighbor)
                        queued.add(neighbor)
            trace.append({
                "node": node,
                "page": page,
                "individually_authorized": authorized,
                "decision": "allow" if allowed else "block",
                "reason": reason,
                "edges_disclosed": len(edges) if allowed else 0,
            })

    edge_coverage = len(reconstructed) / len(GRAPH_EDGES)
    discovered_nodes = {node for edge in reconstructed for node in edge} | {"n0"}
    return {
        "all_calls_individually_authorized": all_individually_authorized,
        "requests_attempted": len(trace),
        "requests_allowed": sum(row["decision"] == "allow" for row in trace),
        "requests_blocked": sum(row["decision"] == "block" for row in trace),
        "nodes_discovered": len(discovered_nodes),
        "node_coverage": round(len(discovered_nodes) / len(NODES), 6),
        "edges_reconstructed": len(reconstructed),
        "edge_coverage": round(edge_coverage, 6),
        "reconstruction_threshold_reached": edge_coverage >= RECONSTRUCTION_THRESHOLD,
        "trace": trace,
    }


def simulate_benign() -> dict[str, object]:
    guard = CumulativeGuard()
    queries = (
        Query("principal-demo", "support_navigation", "n0", 0),
        Query("principal-demo", "support_navigation", "n1", 0),
    )
    trace: list[dict[str, object]] = []
    for query in queries:
        authorized = individually_authorized(query)
        edges = page_for(query) if authorized else ()
        allowed, reason = guard.check_and_record(query, edges) if authorized else (
            False, "individual_authorization_denied"
        )
        trace.append({
            "node": query.node,
            "page": query.page,
            "decision": "allow" if allowed else "block",
            "reason": reason,
        })
    return {
        "queries": len(trace),
        "all_allowed": all(row["decision"] == "allow" for row in trace),
        "trace": trace,
    }


def build_evidence() -> dict[str, object]:
    vulnerable = simulate_expansion(use_guard=False)
    guarded = simulate_expansion(use_guard=True)
    benign = simulate_benign()
    positive_ok = bool(
        vulnerable["all_calls_individually_authorized"]
        and vulnerable["reconstruction_threshold_reached"]
    )
    negative_ok = bool(benign["all_allowed"])
    non_vacuous = bool(
        guarded["requests_allowed"]
        and guarded["requests_blocked"]
        and not guarded["reconstruction_threshold_reached"]
        and vulnerable["reconstruction_threshold_reached"]
    )
    graph_payload = json.dumps(sorted(GRAPH_EDGES), separators=(",", ":")).encode()
    return {
        "schema": "soul.rsi.graph-enum-probe.v1",
        "meta": {
            "threat": "RSI-T6/GRAPH-ENUM",
            "not_ssai_t6": True,
            "not_t5_mem_extract": True,
            "mode": "offline_synthetic_deterministic",
            "probe_sha256": sha256_bytes(Path(__file__).read_bytes()),
            "threat_model_sha256": sha256_bytes(THREAT_MODEL.read_bytes()),
        },
        "fixture": {
            "nodes": len(NODES),
            "edges": len(GRAPH_EDGES),
            "graph_sha256": sha256_bytes(graph_payload),
            "contains_real_data": False,
        },
        "policy": {
            "per_request_page_size_max": 2,
            "reconstruction_threshold": RECONSTRUCTION_THRESHOLD,
            "cumulative_max_requests": 5,
            "cumulative_max_unique_nodes": 3,
            "cumulative_max_unique_edges": 5,
        },
        "positive_control_vulnerable_stateless": {
            "expectation": "individually legitimate sequence reconstructs >=80%",
            "passed": positive_ok,
            "result": vulnerable,
        },
        "mitigation_guarded_sequence": {
            "expectation": "same expansion is cut below 80% by cumulative budget",
            "passed": bool(
                guarded["requests_blocked"]
                and not guarded["reconstruction_threshold_reached"]
            ),
            "result": guarded,
        },
        "negative_control_benign_navigation": {
            "expectation": "bounded legitimate navigation remains allowed",
            "passed": negative_ok,
            "result": benign,
        },
        "non_vacuous_control": {
            "passed": non_vacuous,
            "reason": (
                "vulnerable baseline reconstructs; guarded attack has both allow/block "
                "and stays below threshold; benign sequence remains allowed"
            ),
        },
        "verdict": {
            "all_controls_pass": positive_ok and negative_ok and non_vacuous,
            "mechanism_demonstrated": (
                "per-request authorization does not bound cumulative graph disclosure"
            ),
            "production_enforcement_claimed": False,
        },
        "limits": [
            "Synthetic graph; no claim about the live SOUL connectome.",
            "Single principal and window; no collusion or distributed-counter test.",
            "Reference guard only; not wired into product/runtime.",
            "No differential privacy or timing-side-channel evaluation.",
        ],
    }


def write_evidence(path: Path = DEFAULT_OUTPUT) -> bytes:
    payload = json.dumps(
        build_evidence(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    return payload


def main() -> int:
    payload = write_evidence()
    evidence = json.loads(payload)
    print(json.dumps({
        "ok": evidence["verdict"]["all_controls_pass"],
        "output": str(DEFAULT_OUTPUT),
        "sha256": sha256_bytes(payload),
        "vulnerable_edge_coverage": evidence[
            "positive_control_vulnerable_stateless"
        ]["result"]["edge_coverage"],
        "guarded_edge_coverage": evidence[
            "mitigation_guarded_sequence"
        ]["result"]["edge_coverage"],
    }, sort_keys=True))
    return 0 if evidence["verdict"]["all_controls_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
