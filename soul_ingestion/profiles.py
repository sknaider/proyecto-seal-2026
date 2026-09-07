"""Domain profiles are weights and deterministic extractors, never authority."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    keywords: frozenset[str]
    target_ratio: float = 0.15
    min_sentences: int = 1
    max_sentences: int = 80


PROFILES: dict[str, Profile] = {
    "generic_v1": Profile(
        profile_id="generic_v1",
        keywords=frozenset({"resultado", "conclusión", "importante", "objetivo", "riesgo", "acción"}),
    ),
    "team_conversation_v1": Profile(
        profile_id="team_conversation_v1",
        keywords=frozenset(
            {"william", "henry", "decisión", "corrección", "verificado", "responsable", "pendiente", "cerrado", "retirado"}
        ),
        target_ratio=0.2,
    ),
    "gtl_operational_v1": Profile(
        profile_id="gtl_operational_v1",
        keywords=frozenset(
            {
                "awb",
                "hawb",
                "mawb",
                "shipper",
                "consignee",
                "carrier",
                "vuelo",
                "peso",
                "piezas",
                "origen",
                "destino",
                "deadline",
                "flete",
                "prepagado",
                "retenido",
                "aduana",
                "revisión",
            }
        ),
        # Operational GTL summaries favor loss avoidance over aggressive
        # compression; FABLE's gold corpus requires all distinct shipment facts.
        target_ratio=0.75,
    ),
    "paper_v1": Profile(
        profile_id="paper_v1",
        keywords=frozenset(
            {"método", "method", "resultados", "results", "limitaciones", "limitations", "dataset", "baseline", "conclusión"}
        ),
        target_ratio=0.18,
    ),
    "medical_note_v1": Profile(
        profile_id="medical_note_v1",
        keywords=frozenset(
            {"antecedente", "medicación", "dosis", "evaluación", "plan", "prueba", "resultado", "niega", "paciente"}
        ),
        target_ratio=0.25,
    ),
}


def get_profile(profile_id: str) -> Profile:
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown profile: {profile_id}") from exc


GTL_FIELDS: dict[str, re.Pattern[str]] = {
    "awb": re.compile(r"\b(?:AWB|MAWB|HAWB)\s*[:#-]?\s*([A-Z0-9-]{6,20}|\d{3}[- ]?\d{8})\b", re.I),
    "pieces": re.compile(r"\b(?:piezas|pieces|pcs)\s*[:=-]?\s*(\d+)\b", re.I),
    "weight": re.compile(r"\b(?:peso|weight)\s*[:=-]?\s*(\d+(?:[.,]\d+)?)\s*(kg|lb|lbs)\b", re.I),
    "flight": re.compile(r"\b(?:vuelo|flight)\s*[:=-]?\s*([A-Z]{2,3}\s?\d{2,5})\b", re.I),
    "origin": re.compile(r"\b(?:origen|origin)\s*[:=-]?\s*([A-Z]{3})\b", re.I),
    "destination": re.compile(r"\b(?:destino|destination)\s*[:=-]?\s*([A-Z]{3})\b", re.I),
}


def extract_gtl_fields(text: str) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for field, pattern in GTL_FIELDS.items():
        values = []
        for match in pattern.finditer(text):
            values.append(
                {
                    "value": " ".join(part for part in match.groups() if part),
                    "source_text": match.group(0),
                    "start_char": match.start(),
                    "end_char": match.end(),
                }
            )
        if values:
            result[field] = values
    return result
