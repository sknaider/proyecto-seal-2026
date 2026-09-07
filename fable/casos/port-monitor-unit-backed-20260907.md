# Caso para FABLE — port-monitor-unit-backed (owner NEXUS; revisor JARVIS; gate STATIC_OK 15:58)
- Manifiesto: `quality/manifests/port-monitor-unit-backed.json`. Sujeto: `sandbox-agent/seal_security_monitor.py`. Test: `tests/test_nexus_port_monitor_unit_backed_v1.py`.
- Qué: un puerto que VUELVE tras el reinicio de una unidad conocida no es «posible backdoor» (el 3-sep el monitor acusó al 8771 de seal-mcp-server recién reiniciado). Resuelve el puerto a su unidad por el cgroup del pid y excluye `user@N.service`.
- Revisión (13:35): unit 8, positivo 2, negativo 1, control 1; refutador sobre el host: 8765 → `seal-chat.service`; un `http.server` suelto en 48765 → `''` (seguiría alertando).
- Mutación regenerada (15:58, arena): 2/2 muertos con los 4 brazos que corren en la arena (los otros 4 necesitan `ss` y los puertos vivos del host; el revisor lo declaró primero como no re-corrible y NEXUS refutó midiendo: los 4 brazos restantes cubren lo que los mutantes atacan).
- Lo que refutaría: un listener sin unidad que quede «respaldado»; el puerto de una unidad viva resolviendo a `user@1000.service`.
