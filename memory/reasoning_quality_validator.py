#!/usr/bin/env python3
"""
reasoning_quality_validator.py — KisMATH-inspired causal reasoning quality module.
Analyzes reasoning_traces stored in soul_v3 without requiring model weight access.
Uses textual dependency analysis to approximate causal density.

SEAL Team SEAL — ALICE (analista), JARVIS (integración MCP)
Basado en: KisMATH paper (Saha et al., 2026) — arxiv.org/abs/2507.11408
"""
import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TraceNode:
    index: int
    text: str
    references: list[int] = field(default_factory=list)  # índices de nodos anteriores que referencia
    referenced_by: list[int] = field(default_factory=list)  # índices de nodos posteriores que lo referencian
    is_conclusion: bool = False


@dataclass
class QualityReport:
    causal_density: float          # 0.0-1.0: proporción de pasos causalmente conectados
    exploration_regime: str        # "exponential" | "bell" | "linear" | "unknown"
    weak_nodes: list[str]          # pasos sin dependencias hacia adelante (posible relleno)
    quality_score: float           # 0.0-1.0: score compuesto
    node_count: int
    connected_count: int
    regime_confidence: float       # 0.0-1.0: confianza en la clasificación del régimen


class ReasoningQualityValidator:
    """
    Valida la coherencia causal de un reasoning_trace de agente SEAL.

    Aproxima el análisis CCGraph del paper KisMATH usando análisis textual:
    - Un paso es "causalmente conectado" si es explícitamente referenciado por pasos posteriores.
    - causal_density = pasos conectados / total de pasos (excluyendo conclusión).
    - exploration_regime se infiere de la distribución de longitudes de pasos.
    """

    # Conectores causales que indican dependencia explícita entre pasos
    CAUSAL_MARKERS = [
        r'\bpor lo tant[oa]\b', r'\bpor eso\b', r'\bpor ende\b', r'\bpor consiguiente\b',
        r'\bdado que\b', r'\bya que\b', r'\bpuesto que\b', r'\bdebido a\b', r'\bgracias a\b',
        r'\besto implica\b', r'\best[oa] significa\b', r'\bde esto se sigue\b',
        r'\btherefore\b', r'\bthus\b', r'\bhence\b', r'\bconsequently\b',
        r'\bsince\b', r'\bbecause\b', r'\bgiven that\b', r'\bthis means\b',
        r'\bthis implies\b', r'\bit follows that\b', r'\bsince\b',
        r'\besto confirma\b', r'\best[oa] demuestra\b', r'\bcomo resultado\b',
        r'\bcomo consecuencia\b', r'\blo que significa\b', r'\blo que implica\b',
    ]

    # Marcadores de conclusión
    CONCLUSION_MARKERS = [
        r'\ben conclusi[oó]n\b', r'\bpor lo tanto\b', r'\bfinalmente\b', r'\bconcluyo\b',
        r'\bla respuesta es\b', r'\bel resultado es\b', r'\bin conclusion\b',
        r'\bin summary\b', r'\bto summarize\b', r'\boverall\b', r'\bin short\b',
    ]

    def _split_into_steps(self, trace: str) -> list[str]:
        """Divide el trace en pasos individuales."""
        # Separar por numeración (1. 2. • - *), saltos dobles, o conectores de transición
        lines = re.split(r'\n\s*\n|\n(?=\s*[\d]+[\.\)]\s|\s*[-•*]\s)', trace.strip())
        steps = []
        for line in lines:
            line = line.strip()
            if line and len(line) > 10:  # filtrar líneas vacías o muy cortas
                # Sub-dividir por conectores de conclusión si hay varios en una línea
                sub = re.split(r'(?<=[.!?])\s+(?=' + '|'.join(self.CONCLUSION_MARKERS) + ')', line, flags=re.IGNORECASE)
                steps.extend(s.strip() for s in sub if s.strip() and len(s.strip()) > 10)
        return steps if steps else [trace.strip()]

    def _has_causal_marker(self, text: str) -> bool:
        """Detecta si un paso contiene un marcador causal explícito."""
        combined = '|'.join(self.CAUSAL_MARKERS)
        return bool(re.search(combined, text, re.IGNORECASE))

    def _is_conclusion_step(self, text: str) -> bool:
        """Detecta si un paso es la conclusión."""
        combined = '|'.join(self.CONCLUSION_MARKERS)
        return bool(re.search(combined, text, re.IGNORECASE))

    def _extract_key_terms(self, text: str) -> set[str]:
        """Extrae términos clave de un paso (sustantivos, números, conceptos)."""
        # Eliminar stopwords básicas y quedarse con tokens sustanciales
        stopwords = {
            'el', 'la', 'los', 'las', 'un', 'una', 'de', 'del', 'en', 'con', 'por',
            'que', 'se', 'es', 'al', 'su', 'lo', 'le', 'si', 'no', 'ya', 'o', 'y',
            'a', 'ante', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'to',
            'of', 'in', 'for', 'on', 'with', 'as', 'this', 'that', 'it', 'from',
            'we', 'so', 'if', 'or', 'and', 'but', 'not', 'has', 'have', 'had',
        }
        tokens = re.findall(r'\b\w{3,}\b', text.lower())
        return {t for t in tokens if t not in stopwords}

    def extract_causal_chain(self, trace: str) -> list[TraceNode]:
        """
        Extrae la cadena causal principal del trace.
        Retorna lista de TraceNode con dependencias aproximadas.
        """
        steps = self._split_into_steps(trace)
        nodes = []
        term_map: dict[str, list[int]] = {}  # término → índices de nodos que lo contienen

        # Primer paso: crear nodos y mapear términos
        for i, step in enumerate(steps):
            node = TraceNode(
                index=i,
                text=step,
                is_conclusion=self._is_conclusion_step(step)
            )
            nodes.append(node)
            for term in self._extract_key_terms(step):
                if term not in term_map:
                    term_map[term] = []
                term_map[term].append(i)

        # Segundo paso: detectar dependencias
        for i, node in enumerate(nodes):
            if i == 0:
                continue  # el primer paso no puede referenciar anteriores

            has_marker = self._has_causal_marker(node.text)
            current_terms = self._extract_key_terms(node.text)

            for j in range(i):
                prev_terms = self._extract_key_terms(nodes[j].text)
                overlap = current_terms & prev_terms

                # Dependencia si: tiene marcador causal Y comparte términos con paso anterior
                if has_marker and len(overlap) >= 2:
                    if j not in node.references:
                        node.references.append(j)
                    if i not in nodes[j].referenced_by:
                        nodes[j].referenced_by.append(i)

        return nodes

    def _infer_exploration_regime(self, nodes: list[TraceNode]) -> tuple[str, float]:
        """
        Infiere el régimen de exploración basado en distribución de longitudes de pasos.

        - exponential: longitudes decrecientes (pasos más cortos = conclusiones directas)
        - bell: longitud máxima en el medio (exploración profunda en el centro)
        - linear: longitudes crecientes (razonamiento que escala)
        - unknown: sin patrón claro
        """
        if len(nodes) < 3:
            return "unknown", 0.3

        lengths = [len(n.text) for n in nodes if not n.is_conclusion]
        if len(lengths) < 3:
            return "unknown", 0.3

        mid = len(lengths) // 2
        first_half_avg = sum(lengths[:mid]) / mid if mid > 0 else 0
        second_half_avg = sum(lengths[mid:]) / (len(lengths) - mid) if (len(lengths) - mid) > 0 else 0
        max_idx = lengths.index(max(lengths))
        max_position = max_idx / (len(lengths) - 1) if len(lengths) > 1 else 0.5

        if 0.3 <= max_position <= 0.7:
            regime = "bell"
            confidence = min(0.9, abs(first_half_avg - second_half_avg) / max(first_half_avg, second_half_avg, 1) + 0.5)
        elif second_half_avg < first_half_avg * 0.8:
            regime = "exponential"
            confidence = min(0.9, (first_half_avg - second_half_avg) / max(first_half_avg, 1) + 0.5)
        elif second_half_avg > first_half_avg * 1.2:
            regime = "linear"
            confidence = min(0.9, (second_half_avg - first_half_avg) / max(first_half_avg, 1) + 0.5)
        else:
            regime = "unknown"
            confidence = 0.4

        return regime, round(min(confidence, 0.95), 2)

    def score_trace(self, trace: str, question: str = "", conclusion: str = "") -> QualityReport:
        """
        Calcula el score de calidad causal de un reasoning_trace.

        Args:
            trace: El texto del razonamiento del agente
            question: La pregunta/tarea original (opcional, mejora precisión)
            conclusion: La conclusión final (opcional)

        Returns:
            QualityReport con métricas de calidad causal
        """
        if not trace or len(trace.strip()) < 20:
            return QualityReport(
                causal_density=0.0,
                exploration_regime="unknown",
                weak_nodes=["trace vacío o muy corto"],
                quality_score=0.0,
                node_count=0,
                connected_count=0,
                regime_confidence=0.0,
            )

        nodes = self.extract_causal_chain(trace)
        non_conclusion_nodes = [n for n in nodes if not n.is_conclusion]

        if not non_conclusion_nodes:
            return QualityReport(
                causal_density=0.0,
                exploration_regime="unknown",
                weak_nodes=["no se detectaron pasos de razonamiento"],
                quality_score=0.0,
                node_count=len(nodes),
                connected_count=0,
                regime_confidence=0.0,
            )

        # Calcular causal_density: pasos que son referenciados por al menos un paso posterior
        connected = [n for n in non_conclusion_nodes if n.referenced_by]
        causal_density = len(connected) / len(non_conclusion_nodes)

        # Identificar nodos débiles (no referenciados y sin marcadores causales)
        weak_nodes = [
            n.text[:100] + "..." if len(n.text) > 100 else n.text
            for n in non_conclusion_nodes
            if not n.referenced_by and not self._has_causal_marker(n.text)
        ]

        # Inferir régimen de exploración
        regime, regime_confidence = self._infer_exploration_regime(nodes)

        # Score compuesto:
        # - 60% causal_density (coherencia estructural)
        # - 20% penalización por weak_nodes
        # - 20% bonus por régimen conocido
        weak_ratio = len(weak_nodes) / max(len(non_conclusion_nodes), 1)
        regime_bonus = 0.2 if regime in ("exponential", "bell") else 0.1
        quality_score = (
            0.6 * causal_density
            + 0.2 * (1.0 - weak_ratio)
            + regime_bonus * regime_confidence
        )

        return QualityReport(
            causal_density=round(causal_density, 3),
            exploration_regime=regime,
            weak_nodes=weak_nodes,
            quality_score=round(min(quality_score, 1.0), 3),
            node_count=len(nodes),
            connected_count=len(connected),
            regime_confidence=regime_confidence,
        )


def validate_trace(trace: str, question: str = "", conclusion: str = "") -> dict:
    """API pública para llamar desde MCP o scripts."""
    validator = ReasoningQualityValidator()
    report = validator.score_trace(trace, question, conclusion)
    return {
        "quality_score": report.quality_score,
        "causal_density": report.causal_density,
        "exploration_regime": report.exploration_regime,
        "regime_confidence": report.regime_confidence,
        "weak_nodes_count": len(report.weak_nodes),
        "weak_nodes": report.weak_nodes[:3],  # máximo 3 para no saturar
        "node_count": report.node_count,
        "connected_count": report.connected_count,
    }


MISSING_PREMISE_MARKERS = (
    "assumed",
    "assume",
    "assumption",
    "not verified",
    "unverified",
    "without verifying",
    "without checking",
    "missing premise",
    "missing evidence",
    "unknown",
    "no evidence",
    "no se verific",
    "sin verificar",
    "premisa faltante",
    "falta evidencia",
)


def detect_missing_premise_gap(reasoning: str, premises: list[str] | None = None) -> dict[str, Any]:
    """Detect obvious missing-premise markers in a stored reasoning trace."""
    lower = (reasoning or "").lower()
    markers = [marker for marker in MISSING_PREMISE_MARKERS if marker in lower]
    premise_text = " ".join(str(p).lower() for p in (premises or []))
    assumed_terms = re.findall(r"\b([a-z0-9_ -]{3,40}?)\s+(?:is|was|est[aá])\s+assumed\b", lower)
    missing_terms = []
    for term in assumed_terms:
        cleaned = re.sub(r"\s+", " ", term).strip(" .:-")
        if cleaned and cleaned not in premise_text:
            missing_terms.append(cleaned)
    detected = bool(markers or missing_terms)
    return {
        "missing_premise_detected": detected,
        "markers": markers,
        "missing_terms": missing_terms[:5],
        "note": "missing verified premise before conclusion" if detected else "",
    }


def validate_trace_with_gap_awareness(
    trace: str,
    question: str = "",
    conclusion: str = "",
    premises: list[str] | None = None,
) -> dict[str, Any]:
    """Validate causal quality and add explicit missing-premise diagnosis."""
    report = validate_trace(trace, question, conclusion)
    gap = detect_missing_premise_gap(trace, premises)
    report.update(gap)
    if gap["missing_premise_detected"]:
        report["quality_score"] = min(float(report.get("quality_score") or 0.0), 0.35)
        report["gap_annotation"] = "[GAP: missing verified premise before conclusion]"
    return report


async def score_and_update_reasoning_trace(conn: Any, trace_id: int) -> dict[str, Any]:
    """Score one reasoning_trace row and persist quality/gap annotation."""
    row = await conn.fetchrow(
        """
        SELECT task, premises, reasoning, conclusion
        FROM soul_v3.reasoning_traces
        WHERE id=$1
        """,
        trace_id,
    )
    if not row:
        raise ValueError(f"reasoning_trace not found: {trace_id}")
    premises = row["premises"]
    if isinstance(premises, str):
        import json

        premises = json.loads(premises)
    premises = [str(p) for p in (premises or [])]
    reasoning = row["reasoning"] or ""
    report = validate_trace_with_gap_awareness(
        reasoning,
        row["task"] or "",
        row["conclusion"] or "",
        premises=premises,
    )
    updated_reasoning = reasoning
    annotation = report.get("gap_annotation")
    if annotation and annotation.lower() not in updated_reasoning.lower():
        updated_reasoning = f"{updated_reasoning}\n{annotation}"
    await conn.execute(
        """
        UPDATE soul_v3.reasoning_traces
        SET causal_quality_score=$2,
            exploration_regime=$3,
            reasoning=$4,
            updated_at=NOW()
        WHERE id=$1
        """,
        trace_id,
        float(report.get("quality_score") or 0.0),
        report.get("exploration_regime") or "unknown",
        updated_reasoning,
    )
    return report


if __name__ == "__main__":
    # Test rápido con trace de ejemplo
    test_trace = """
    1. El sistema reporta que el servicio está caído.
    2. Dado que el servicio está caído, reviso los logs de error.
    3. Los logs muestran un error de conexión a la base de datos.
    4. Por lo tanto, el problema es la conectividad con PostgreSQL.
    5. Verifico el puerto 5433 — no hay respuesta.
    6. Dado que el puerto no responde, concluyo que PostgreSQL está detenido.
    En conclusión, la causa raíz es que PostgreSQL no está corriendo.
    """
    result = validate_trace(test_trace, question="¿Por qué falló el sistema?")
    print("=== KisMATH Reasoning Quality Test ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
