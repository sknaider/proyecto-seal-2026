"""NEXUS kernel — cognitive core modules.

Spec: spec_nexus_kernel_soul_v1.md
Variant: lean (3 own + 3 delegated by design + 2 critical security modules)

Modules:
  cortex                   — LLM dispatch, action detection
  executor                 — sandboxed action execution layer
  health_monitor           — 15min health loop
  identity_integrity       — anti-impersonation guard for cortex outputs
  reasoning_logger         — D5 trace store for auditable decisions
"""
__all__ = [
    "cortex",
    "executor",
    "health_monitor",
    "identity_integrity",
    "reasoning_logger",
]
