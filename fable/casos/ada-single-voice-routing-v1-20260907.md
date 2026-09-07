# Caso — `ada-single-voice-routing-v1` (owner ADA, revisor independiente JARVIS)

| Campo | Valor |
|---|---|
| Manifiesto | `quality/manifests/ada-single-voice-routing-v1.json` |
| Sujetos | `messages/ada_codex_remote_bridge.py`, `messages/routing.yaml` |
| Tests | `messages/tests/test_ada_codex_remote_bridge_silence.py`, `messages/tests/test_ada_directed_routing.py` |
| Evidencia de mutación | `quality/mutation-ada-single-voice-routing-v1.v2.json` (revisor JARVIS, 6/6, ejecutable-v2, arena aprobada) |
| Spec | `quality/mutantes/ada-single-voice-routing-v1.spec.json` |
| Gate (7-sep 18:05) | `status: STATIC_OK · errors: None` |
| Commits | ed0ee0d (ALICE: ROOT relativo, línea 32) · ec16aec (re-firma) |

## Historia del bloqueo
Hasta las 18:02 el control en la arena salía ROJO: `ROOT = Path("/home/dadito/IA/proyecto-seal")`
literal (línea 32) no existe dentro del contenedor. ALICE lo cambió a `Path(__file__).resolve().parents[1]`
(idéntico en este host, verificado antes de editar); el revisor no editó el sujeto. El puente NO se
reinició: el valor resuelto es el mismo; el reinicio va con la agenda y aviso de 2 min.

## Refutador
```text
routing-sin-terminal-log        MUERTO
ack-sin-gracia                  MUERTO
ack-dm-apagado-por-default      MUERTO
ada-filtra-a-jarvis             MUERTO
jarvis-filtra-a-ada             MUERTO
reply-id-solo-numerico          MUERTO
control sin mutar               86 passed · brazos 86/1/2/2 exit 0
```
Cuatro firmas en `review.historial`; la vigente es de las 23:02Z.

## Lo que NO prueba
La conversación real por Codex (transporte vivo) no se ejercitó; es suite + mutación + gate.
