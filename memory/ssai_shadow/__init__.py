"""SSAI SHADOW M1 — prototipo aislado de identidad soberana de agentes.

Este paquete no modifica ``soul_v3`` ni participa en el boot de producción.
Solo contiene primitivas verificables para experimentar con el contrato SSAI v1.
"""

from .manifest import (
    MANIFEST_SCHEMA_V1,
    build_genesis_manifest,
    generate_soul_id,
    validate_manifest,
)

__all__ = [
    "MANIFEST_SCHEMA_V1",
    "build_genesis_manifest",
    "generate_soul_id",
    "validate_manifest",
]
