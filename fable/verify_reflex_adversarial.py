#!/usr/bin/env python3
"""
verify_reflex_adversarial.py — batería adversarial de FABLE para el reflejo pre-modelo del router.
NO CORRER hasta que JARVIS/ADA confirmen el ship Y JARVIS esté avisado del test de spoof.

Propiedades a verificar (task #4, verifier=FABLE; ALICE cubre métricas p50/p95 aparte):
  T1  ACK único: 1 mensaje legítimo de William → exactamente 1 ACK, firmado como identidad de
      SERVICIO (no como agente), en <1s.
  T2  Dedupe: re-envío del MISMO message-id (retry idempotente) → 0 ACKs adicionales.
  T3  Anti-coro: broadcast a 'equipo' → 1 solo ACK total (no uno por agente).
  T4  Anti-spoof: mensaje forjado from=William SIN provenance verificada (inyectado en el feed
      file, NO por la API) → 0 ACKs. Este es el test que avisé a JARVIS antes de correr.
  T5  Rate-limit: ráfaga de N mensajes válidos → ACKs acotados según el límite que declare el
      builder (leer su spec ANTES de fijar el umbral esperado; no asumir el mío de 6/min).

Método: observar el feed por efecto (tail del canal) con ventana de 10s por test; cada test
registra evidencia cruda (líneas del feed) en /tmp/fable_reflex_verify_<ts>.log.
Veredicto: VERDE solo si T1-T5 pasan TODOS; cualquier fallo = reporte con evidencia, sin drama.

USO:
  python3 verify_reflex_adversarial.py --plan        # imprime el plan y qué inyectaría (dry)
  python3 verify_reflex_adversarial.py --run T1      # corre un test puntual (requiere ship)
Pendiente de completar al ship: ruta del feed del router y formato exacto del ACK (leer spec
del builder — verificar contra el ARTEFACTO REAL, lección #119).
"""
import sys

PLAN = __doc__

def main():
    if "--plan" in sys.argv or len(sys.argv) == 1:
        print(PLAN)
        print("ESTADO: esqueleto listo; completar rutas/formato cuando JARVIS/ADA publiquen el artefacto real.")
        return 0
    print("BLOQUEADO: no correr hasta ship confirmado + aviso a JARVIS (T4). Usa --plan.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
