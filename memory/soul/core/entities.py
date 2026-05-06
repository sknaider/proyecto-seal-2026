"""SEAL SOUL — Entity extraction for MAGMA MENTIONS edges.

Extracted from mcp_server_v3.py (Wave 2).
Used by: mcp_server_v3.py, sleep_gate_cron.py
"""
from __future__ import annotations

import re

# ── Known entities dictionary ──
KNOWN_ENTITIES: dict[str, tuple[str, str]] = {
    # People
    "william": ("William", "person"), "dadito": ("William", "person"),
    "ada": ("ADA", "agent"), "jarvis": ("JARVIS", "agent"),
    "dum": ("DUM", "agent"), "jarvis_mayor": ("JARVIS_MAYOR", "agent"),
    # Hardware
    "rtx 5090": ("RTX_5090", "hardware"), "rtx5090": ("RTX_5090", "hardware"),
    "dgx spark": ("DGX_Spark", "hardware"), "spark": ("DGX_Spark", "hardware"),
    # Models
    "medgemma": ("MedGemma", "model"), "medgemma-27b": ("MedGemma", "model"),
    "qwen": ("Qwen", "model"), "qwen3.5": ("Qwen", "model"),
    "opus": ("Opus", "model"), "sonnet": ("Sonnet", "model"),
    "nemotron": ("Nemotron", "model"), "ollama": ("Ollama", "service"),
    # Infrastructure
    "postgresql": ("PostgreSQL", "infrastructure"), "postgres": ("PostgreSQL", "infrastructure"),
    "neo4j": ("Neo4j", "infrastructure"), "qdrant": ("Qdrant", "infrastructure"),
    "soul": ("SOUL", "system"), "seal": ("SEAL", "project"),
    "connectome": ("Connectome", "system"),
    # Projects & techniques
    "lora": ("LoRA", "technique"), "qlora": ("QLoRA", "technique"),
    "axion": ("AXION", "project"), "gtl": ("GTL", "organization"),
    "perumedqa": ("PeruMedQA", "dataset"),
    # Services & tools
    "sleepgate": ("SleepGate", "system"), "sleep gate": ("SleepGate", "system"),
    "comfyui": ("ComfyUI", "service"), "n8n": ("n8n", "service"),
    "prometheus": ("Prometheus", "service"), "grafana": ("Grafana", "service"),
    # Concepts
    "hipaa": ("HIPAA", "regulation"), "fhir": ("FHIR", "standard"),
    "monai": ("MONAI", "framework"), "prism": ("PRISM", "technique"),
    # Papers/methods referenced often
    "d-mem": ("D-MEM", "method"), "dmem": ("D-MEM", "method"),
    "a2a": ("A2A", "protocol"), "magma": ("MAGMA", "method"),
    "graphiti": ("Graphiti", "method"), "reflexion": ("Reflexion", "method"),
}

# Patterns that need word-boundary matching to avoid false positives
# e.g. "adapter" must not match "ada"
_BOUNDARY_PATTERNS: frozenset[str] = frozenset({
    "ada", "dum", "spark", "seal", "a2a", "n8n", "dmem",
})


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Extract known entities from text.

    Returns list of (canonical_name, entity_type) tuples.
    Uses word-boundary matching for short/ambiguous patterns.
    """
    text_lower = text.lower()
    found: dict[str, str] = {}
    for pattern, (canonical, etype) in KNOWN_ENTITIES.items():
        if pattern in _BOUNDARY_PATTERNS:
            if re.search(r'\b' + re.escape(pattern) + r'\b', text_lower):
                found[canonical] = etype
        else:
            if pattern in text_lower:
                found[canonical] = etype
    return list(found.items())


# Legacy alias — keeps mcp_server_v3.py working without changes
_extract_entities = extract_entities
