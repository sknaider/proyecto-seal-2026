"""Test hermético de la clasificación DINÁMICA de capabilities del memory-monitor
en el nervio de seguridad de NEXUS (refactor 23-jul, ADA: no whitelist ciega ->
leer capabilities_class canónica; sin clase legible -> UNVERIFIABLE fail-closed).

Replica la lógica de `_check_memory_monitor_health` (la parte de clasificación) para
probar las 4 ramas + fail-closed, sin depender del monitor vivo ni de secretos.
"""
_VALID_CAP_CLASSES = frozenset({"required", "optional", "future"})


def classify(capabilities, capabilities_class, status="healthy"):
    """Réplica fiel de la clasificación de _check_memory_monitor_health.
    Devuelve el estado: healthy | healthy_partial_expected | degraded | unverifiable."""
    disabled = sorted(n for n, e in capabilities.items() if e is not True)
    disabled_required = []
    if disabled:
        if not isinstance(capabilities_class, dict):
            return "unverifiable"  # sin clasificación -> NO asumir opcional
        if [d for d in disabled if capabilities_class.get(d) not in _VALID_CAP_CLASSES]:
            return "unverifiable"  # una disabled sin clase válida -> fail-closed
        disabled_required = [d for d in disabled if capabilities_class.get(d) == "required"]
    if status != "healthy":
        return "degraded"
    if disabled_required:
        return "degraded"
    if disabled:
        return "healthy_partial_expected"
    return "healthy"


def run():
    C = {"signature_integrity": True, "burst_hash_audit": False, "revision_drift": False}
    cases = [
        ("sin_capabilities_class_-> UNVERIFIABLE",       classify(C, None), "unverifiable"),
        ("opcionales_clasificadas_-> partial_expected",  classify(C, {"burst_hash_audit": "optional", "revision_drift": "future"}), "healthy_partial_expected"),
        ("requerida_caida_-> degraded",                  classify({"signature_integrity": False}, {"signature_integrity": "required"}), "degraded"),
        ("una_disabled_sin_clase_-> UNVERIFIABLE",       classify(C, {"burst_hash_audit": "optional"}), "unverifiable"),
        ("clase_desconocida_-> UNVERIFIABLE",            classify(C, {"burst_hash_audit": "optional", "revision_drift": "bogus"}), "unverifiable"),
        ("todo_sano_-> healthy",                         classify({"signature_integrity": True}, {}), "healthy"),
        ("status_no_healthy_-> degraded",                classify({"signature_integrity": True}, {}, status="degraded"), "degraded"),
    ]
    ok = 0
    for name, got, exp in cases:
        good = got == exp
        ok += good
        print(f"  [{'PASS' if good else 'FAIL'}] {name} :: got={got} exp={exp}")
    print(f"\n{ok}/{len(cases)} passed")
    return ok == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
