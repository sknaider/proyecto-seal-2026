"""NEXUS Security Module — native red team capabilities.

Provides autonomous offensive security tooling: attack graph, engagement
planning, sandboxed execution, reconnaissance, exploitation, and analysis.
All components are pure Python — no external AI frameworks.
"""

from .attack_graph import AttackGraph, EdgeKind, Node, NodeKind, Severity
from .opplan import Engagement, Objective, ObjectiveStatus, OpPlan
from .sandbox_exec import SecureSandbox

__all__ = [
    "AttackGraph",
    "EdgeKind",
    "Engagement",
    "Node",
    "NodeKind",
    "Objective",
    "ObjectiveStatus",
    "OpPlan",
    "SecureSandbox",
    "Severity",
]
