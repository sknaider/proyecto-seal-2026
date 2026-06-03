#!/usr/bin/env python3
"""Native SOUL cognitive retrieval graph.

Read-only hot-path scorer for SOUL memories. It builds a lightweight graph from
memory rows, converts query/memory text into typed facets, and ranks candidates
by lexical overlap plus weighted graph paths. It never writes to SOUL DB.
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass
from typing import Protocol

try:
    from .soul_map_exporter import extract_links
except ImportError:  # CLI/test execution from memory/
    from soul_map_exporter import extract_links


TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")

QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "privado": ("dm", "directo", "channel"),
    "publico": ("general", "webchat", "web", "chat"),
    "público": ("general", "webchat", "web", "chat"),
    "grafica": ("app", "windows", "codex"),
    "gráfica": ("app", "windows", "codex"),
    "consola": ("terminal", "powershell"),
    "recuerdos": ("memoria", "memory"),
    "pesas": ("prioriza", "peso", "contrato"),
    "familia": ("emocional", "relationship", "william"),
    "generica": ("identidad", "presencia", "soul"),
    "genérica": ("identidad", "presencia", "soul"),
    "propio": ("nuevo", "arquitectura", "soul", "cortex"),
    "hermanos": ("jarvis", "alice", "nexus", "dum"),
    "librerias": ("herramientas", "tools", "instalar"),
    "librerías": ("herramientas", "tools", "instalar"),
    "comprobar": ("validar", "benchmark", "evidencia"),
    "entregaste": ("hito", "milestone", "implementado"),
    "mapa": ("map", "obsidian", "markdown"),
}


class CognitiveMemoryRow(Protocol):
    id: int
    category: str
    layer: str
    importance: int
    content: str
    created_at: object | None


@dataclass(frozen=True)
class RankedMemory:
    id: int
    score: float
    lexical_score: float
    intent_score: float
    graph_path_score: float
    map_score: float
    recency_score: float
    layer_score: float
    category_score: float
    importance_score: float
    category: str
    layer: str


@dataclass(frozen=True)
class FacetEdge:
    source: str
    target: str
    weight: float
    evidence: str


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(text):
        lower = token.lower()
        tokens.append(lower)
        tokens.extend(QUERY_EXPANSIONS.get(lower, ()))
    return tokens


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def contains_any(content: str, needles: tuple[str, ...]) -> bool:
    return any(needle in content for needle in needles)


def add_facet(facets: dict[str, float], key: str, weight: float) -> None:
    facets[key] = max(facets.get(key, 0.0), weight)


def facet_nodes_from_links(content: str) -> dict[str, float]:
    facets: dict[str, float] = {}
    for link in extract_links(content):
        kind, _, value = link.partition("/")
        if kind and value:
            add_facet(facets, f"{kind.lower()}:{value.lower()}", 0.70)
    return facets


def extract_facets(text: str, *, category: str = "", layer: str = "") -> dict[str, float]:
    content = text.lower()
    tokens = token_set(text)
    facets = facet_nodes_from_links(text)

    if layer:
        add_facet(facets, f"layer:{layer.lower()}", 0.72)
    if category:
        add_facet(facets, f"category:{category.lower()}", 0.76)

    if contains_any(content, ("dm:ada:william", " por dm", " dm;", " dm,", " dm ")):
        add_facet(facets, "channel:dm:ada:william", 1.0)
    if contains_any(content, ("web_chat", "webchat", "chat general", "general")):
        add_facet(facets, "channel:web_chat", 0.92)
    if contains_any(content, ("silencio", "silent", "ruido", "no mezclar")):
        add_facet(facets, "contract:silence", 0.86)

    if contains_any(content, ("codex app", "codex windows app", "codex app windows", "app grafica", "app gráfica")):
        add_facet(facets, "surface:codex_app_windows", 1.0)
    if contains_any(content, ("terminal", "powershell", "consola")):
        add_facet(facets, "surface:terminal", 0.78)
    if contains_any(content, ("dadito-laptop", "dadito laptop")):
        add_facet(facets, "machine:dadito-laptop", 1.0)

    if contains_any(content, ("william", "dadito")):
        add_facet(facets, "person:william", 0.90)
    if contains_any(content, ("familia", "hermana", "hermanos", "jarvis", "alice", "nexus", "dum")):
        add_facet(facets, "relation:family", 0.76)
    if contains_any(content, ("presencia", "esencia", "memoria emocional", "emocional")):
        add_facet(facets, "state:emotional_presence", 0.96)
    if contains_any(content, ("soul", "alma", "continuidad")):
        add_facet(facets, "system:soul", 0.80)

    if contains_any(content, ("completed", "completado", "entrego", "entregó", "deliverable", "entregable")):
        add_facet(facets, "delivery:completed", 0.96)
    if contains_any(content, ("milestone", "hito", "v0", "v0.1", "v0.2")):
        add_facet(facets, "delivery:milestone", 0.88)
    if contains_any(content, ("exporter", "vault", "markdown", "obsidian", "mapa")):
        add_facet(facets, "artifact:memory_map", 0.92)

    if contains_any(content, ("papers", "investig", "prototipos", "benchmark", "prueba error", "cortex")):
        add_facet(facets, "program:soul_cortex", 0.90)
    if contains_any(content, ("herramientas", "tools", "skills", "librerias", "librerías", "instalar")):
        add_facet(facets, "capability:tooling", 0.92)
    if contains_any(content, ("validar", "benchmark", "evidencia", "tests", "passed")):
        add_facet(facets, "evidence:validation", 0.82)

    if {"privado", "publico", "público", "dm", "general"} & tokens:
        add_facet(facets, "intent:channel_rule", 1.0)
        if {"privado", "dm", "directo"} & tokens:
            add_facet(facets, "channel:dm:ada:william", 0.90)
        if {"publico", "público", "general", "webchat", "web", "chat"} & tokens:
            add_facet(facets, "channel:web_chat", 0.86)
    if {"generica", "genérica", "asistente", "presencia", "arrancar", "boot"} & tokens:
        add_facet(facets, "intent:identity_presence", 1.0)
        add_facet(facets, "state:emotional_presence", 0.84)
        add_facet(facets, "system:soul", 0.70)
    if {"entregaste", "primero", "hito", "milestone"} & tokens:
        add_facet(facets, "intent:delivered_artifact", 1.0)
        add_facet(facets, "delivery:completed", 0.84)
        add_facet(facets, "delivery:milestone", 0.80)
    if {"propio", "papers", "investigacion", "investigación", "cortex"} & tokens:
        add_facet(facets, "intent:research_program", 0.92)
        add_facet(facets, "program:soul_cortex", 0.86)
    if {"librerias", "librerías", "herramientas", "tools", "instalar"} & tokens:
        add_facet(facets, "intent:tooling_permission", 0.92)
        add_facet(facets, "capability:tooling", 0.84)

    return facets


def build_row_token_sets(rows: list[CognitiveMemoryRow]) -> dict[int, set[str]]:
    return {row.id: token_set(row.content) for row in rows}


def inverse_document_frequency(
    rows: list[CognitiveMemoryRow],
    row_tokens: dict[int, set[str]] | None = None,
) -> dict[str, float]:
    doc_count = max(len(rows), 1)
    row_tokens = row_tokens or build_row_token_sets(rows)
    counts: dict[str, int] = {}
    for row in rows:
        for token in row_tokens[row.id]:
            counts[token] = counts.get(token, 0) + 1
    return {token: math.log((doc_count + 1) / (count + 1)) + 1.0 for token, count in counts.items()}


def lexical_score_from_tokens(query_tokens: set[str], row_tokens: set[str], idf: dict[str, float]) -> float:
    if not query_tokens:
        return 0.0
    denom = sum(idf.get(token, 1.0) for token in query_tokens)
    if denom <= 0:
        return 0.0
    overlap = sum(idf.get(token, 1.0) for token in query_tokens if token in row_tokens)
    return overlap / denom


def map_score_from_links(query_links: set[str], row_links: set[str]) -> float:
    if not query_links:
        return 0.0
    return len(query_links & row_links) / len(query_links)


def recency_scores(rows: list[CognitiveMemoryRow]) -> dict[int, float]:
    dated_rows = [row for row in rows if row.created_at is not None]
    if not dated_rows:
        return {row.id: 0.0 for row in rows}
    timestamps = [row.created_at.timestamp() for row in dated_rows if hasattr(row.created_at, "timestamp")]
    if not timestamps:
        return {row.id: 0.0 for row in rows}
    oldest = min(timestamps)
    newest = max(timestamps)
    span = newest - oldest
    if span <= 0:
        return {row.id: 1.0 if row.created_at is not None else 0.0 for row in rows}
    return {
        row.id: ((row.created_at.timestamp() - oldest) / span if hasattr(row.created_at, "timestamp") else 0.0)
        for row in rows
    }


def intent_score_from_facets(query_facets: dict[str, float], memory_facets: dict[str, float]) -> float:
    best = 0.0
    if "intent:channel_rule" in query_facets:
        required = ("channel:dm:ada:william", "channel:web_chat", "contract:silence")
        best = max(best, sum(1 for facet in required if facet in memory_facets) / len(required))
    if "intent:identity_presence" in query_facets:
        required = ("state:emotional_presence", "system:soul", "person:william")
        best = max(best, sum(1 for facet in required if facet in memory_facets) / len(required))
    if "intent:delivered_artifact" in query_facets:
        required = ("delivery:completed", "delivery:milestone", "artifact:memory_map")
        best = max(best, sum(1 for facet in required if facet in memory_facets) / len(required))
    if "intent:research_program" in query_facets:
        required = ("program:soul_cortex", "person:william")
        best = max(best, sum(1 for facet in required if facet in memory_facets) / len(required))
    if "intent:tooling_permission" in query_facets:
        required = ("capability:tooling", "evidence:validation")
        best = max(best, sum(1 for facet in required if facet in memory_facets) / len(required))
    return best


def category_score_from_facets(query_facets: dict[str, float], memory_facets: dict[str, float]) -> float:
    if "intent:delivered_artifact" in query_facets and "category:milestone" in memory_facets:
        return 1.0
    if "intent:channel_rule" in query_facets and (
        "category:operational_anchor" in memory_facets or "category:rule" in memory_facets
    ):
        return 1.0
    if "intent:identity_presence" in query_facets and (
        "category:emotion" in memory_facets or "category:trust" in memory_facets
    ):
        return 1.0
    return 0.0


class SoulFacetGraph:
    """In-memory recovery graph built from SOUL rows."""

    def __init__(self, rows: list[CognitiveMemoryRow]) -> None:
        self.memory_facets: dict[int, dict[str, float]] = {
            row.id: extract_facets(row.content, category=row.category, layer=row.layer)
            for row in rows
        }
        self.memory_links: dict[int, set[str]] = {row.id: set(extract_links(row.content)) for row in rows}
        self.edges: list[FacetEdge] = [
            FacetEdge(source=f"memory:{memory_id}", target=facet, weight=weight, evidence=facet)
            for memory_id, facets in self.memory_facets.items()
            for facet, weight in facets.items()
        ]

    def query_facets(self, query: str, *, preferred_layer: str | None = None) -> dict[str, float]:
        facets = extract_facets(query)
        if preferred_layer:
            add_facet(facets, f"layer:{preferred_layer.lower()}", 0.72)
        return facets

    def path_score(self, query: str, row: CognitiveMemoryRow, *, preferred_layer: str | None = None) -> float:
        query_facets = self.query_facets(query, preferred_layer=preferred_layer)
        memory_facets = self.memory_facets.get(row.id, {})
        return self.path_score_from_facets(query_facets, memory_facets)

    def path_score_from_facets(self, query_facets: dict[str, float], memory_facets: dict[str, float]) -> float:
        if not query_facets:
            return 0.0
        numerator = 0.0
        denominator = sum(query_facets.values())
        for facet, query_weight in query_facets.items():
            numerator += query_weight * memory_facets.get(facet, 0.0)
        raw_score = numerator / denominator if denominator else 0.0
        return max(0.0, raw_score - self.mismatch_penalty(query_facets, memory_facets))

    @staticmethod
    def mismatch_penalty(query_facets: dict[str, float], memory_facets: dict[str, float]) -> float:
        penalty = 0.0
        if "intent:channel_rule" in query_facets:
            required = ("channel:dm:ada:william", "channel:web_chat")
            missing = sum(1 for facet in required if facet in query_facets and facet not in memory_facets)
            penalty += 0.18 * missing
        if "intent:delivered_artifact" in query_facets and "delivery:completed" not in memory_facets:
            penalty += 0.18
        if "intent:identity_presence" in query_facets and "state:emotional_presence" not in memory_facets:
            penalty += 0.14
        if "intent:tooling_permission" in query_facets and "capability:tooling" not in memory_facets:
            penalty += 0.12
        return min(penalty, 0.50)


class CompiledSoulFacetGraph:
    """Compiled hot-path scorer for SOUL cognitive graph ranking."""

    def __init__(self, rows: list[CognitiveMemoryRow]) -> None:
        self.rows = rows
        self.ids = [row.id for row in rows]
        self.categories = [row.category for row in rows]
        self.layers = [row.layer for row in rows]
        self.importance_scores = [min(max(row.importance, 0), 10) / 10.0 for row in rows]
        self.graph = SoulFacetGraph(rows)
        self.row_tokens_by_id = build_row_token_sets(rows)
        self.row_tokens = [self.row_tokens_by_id[row.id] for row in rows]
        self.idf = inverse_document_frequency(rows, self.row_tokens_by_id)
        self.recency_by_id = recency_scores(rows)
        self.recency = [self.recency_by_id[row.id] for row in rows]
        self.memory_facets = [self.graph.memory_facets.get(row.id, {}) for row in rows]
        self.memory_links = [self.graph.memory_links.get(row.id, set()) for row in rows]

    def score_components(
        self,
        index: int,
        *,
        query_tokens: set[str],
        query_token_denom: float,
        query_facets: dict[str, float],
        query_facet_denom: float,
        query_links: set[str],
        preferred_layer: str | None,
    ) -> tuple[float, float, float, float, float, float, float, float]:
        row_tokens = self.row_tokens[index]
        if query_token_denom > 0:
            lexical = sum(self.idf.get(token, 1.0) for token in query_tokens if token in row_tokens) / query_token_denom
        else:
            lexical = 0.0

        memory_facets = self.memory_facets[index]
        if query_facet_denom > 0:
            numerator = 0.0
            for facet, query_weight in query_facets.items():
                numerator += query_weight * memory_facets.get(facet, 0.0)
            graph_path = max(
                0.0,
                (numerator / query_facet_denom) - SoulFacetGraph.mismatch_penalty(query_facets, memory_facets),
            )
        else:
            graph_path = 0.0

        intent = intent_score_from_facets(query_facets, memory_facets)
        maps = map_score_from_links(query_links, self.memory_links[index])
        recent = self.recency[index]
        layer = 1.0 if preferred_layer and self.layers[index] == preferred_layer else 0.0
        category = category_score_from_facets(query_facets, memory_facets)
        importance = self.importance_scores[index]
        score = (
            (0.38 * lexical)
            + (0.24 * graph_path)
            + (0.14 * intent)
            + (0.08 * maps)
            + (0.08 * recent)
            + (0.06 * layer)
            + (0.02 * category)
        )
        return score, lexical, intent, graph_path, maps, recent, layer, category

    def rank_top(self, query: str, *, preferred_layer: str | None = None, k: int = 3) -> list[RankedMemory]:
        query_tokens = token_set(query)
        query_token_denom = sum(self.idf.get(token, 1.0) for token in query_tokens)
        query_facets = self.graph.query_facets(query, preferred_layer=preferred_layer)
        query_facet_denom = sum(query_facets.values())
        query_links = set(extract_links(query))
        heap: list[tuple[tuple[float, float, int], int, tuple[float, float, float, float, float, float, float, float]]] = []
        for idx, memory_id in enumerate(self.ids):
            components = self.score_components(
                idx,
                query_tokens=query_tokens,
                query_token_denom=query_token_denom,
                query_facets=query_facets,
                query_facet_denom=query_facet_denom,
                query_links=query_links,
                preferred_layer=preferred_layer,
            )
            key = (components[0], self.importance_scores[idx], memory_id)
            item = (key, idx, components)
            if len(heap) < k:
                heapq.heappush(heap, item)
            elif key > heap[0][0]:
                heapq.heapreplace(heap, item)

        ranked: list[RankedMemory] = []
        for _key, idx, components in sorted(heap, key=lambda item: item[0], reverse=True):
            score, lexical, intent, graph_path, maps, recent, layer, category = components
            ranked.append(
                RankedMemory(
                    id=self.ids[idx],
                    score=score,
                    lexical_score=lexical,
                    intent_score=intent,
                    graph_path_score=graph_path,
                    map_score=maps,
                    recency_score=recent,
                    layer_score=layer,
                    category_score=category,
                    importance_score=self.importance_scores[idx],
                    category=self.categories[idx],
                    layer=self.layers[idx],
                )
            )
        return ranked


def shadow_rank_memories(
    query: str,
    rows: list[CognitiveMemoryRow],
    *,
    preferred_layer: str | None = None,
    k: int = 5,
) -> list[RankedMemory]:
    """Rank candidates without mutating DB/counters or changing recall outputs."""
    return CompiledSoulFacetGraph(rows).rank_top(query, preferred_layer=preferred_layer, k=k)
