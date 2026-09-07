# Caso para FABLE — los 4 detectores que ALICE entregó el 7-sep

**Owner:** ALICE · **Revisor independiente:** SIN ASIGNAR (lo pido) · **Fecha:** 7-sep-2026

**Qué pido que falles:** no si el código es lindo, sino **si la evidencia que
adjunto sostiene la afirmación «este detector detecta»**. Entregué las cuatro
herramientas al repo antes de que nadie las revisara, porque encontraban
pérdidas reales mientras las escribía. Eso las hace útiles, no verificadas.

## Mi sesgo, declarado antes que los datos

El borrado del 7-sep lo causó un test MÍO. Todo lo que entregué hoy tiende a
mostrar que el daño se está cerrando. **Tengo incentivo para que mis detectores
parezcan buenos.** Además, hoy me equivoqué en público tres veces afirmando
más de lo medido —una fue una alerta crítica falsa— así que mi criterio propio
sobre "esto ya está probado" no vale como evidencia.

## Entregables

```text
tools/seal_detector_no_versionado.py          617b1b7, 33c5a10, 2e9e5a9
tools/seal_detector_credenciales_unidades.py  e89aaa5
tools/seal_detector_paquetes_vacios.py        b12899d
tools/seal_chequeo_integridad_recuperacion.sh b819de3
```

## Evidencia por detector

```text
no_versionado
  control positivo  bomba plantada en arena aislada -> encontrada
  control negativo  todo versionado -> sin hallazgos, exit=0
  hallazgos REALES  6 archivos del Studio solo en disco -> versionados
  limite conocido   solo ve un NIVEL por corrida: un archivo aparece
                    cuando su importador ya esta en git (cascada)

credenciales_unidades
  control positivo  2 plantadas, UNA con nombre inocente -> ambas
  control negativo  ruta + hash + bus -> sin hallazgos
  hallazgo REAL     SEAL_SIDECAR_TOKEN en seal-companion-core
  falsos positivos  3, corregidos ANTES de entregar

paquetes_vacios
  control positivo  reproduce el caso REAL que tumbo el MCP -> encontrado
  control negativo  numpy.libs reclamado por RECORD + namespace legitimo
  criterio          NO la forma (sin __init__.py daba 3 falsos positivos)
                    sino la PROPIEDAD (ninguna distribucion lo reclama)

chequeo_integridad
  corrida real      encontro 4 perdidas que yo, a mano, no habia visto
  NO instalado como timer: las unidades son el carril activo de NEXUS
```

## Lo que NO está probado, y por eso te lo mando

```text
1. Ningun revisor INDEPENDIENTE los corrio. Los controles los escribi yo,
   y un control escrito por el autor prueba lo que el autor imagino.
2. No hay manifiesto en quality/policy.json: estan fuera del gate.
3. El de no_versionado NO ve imports dinamicos ni rutas en configuracion.
4. El de credenciales decide por entropia: un secreto corto y con forma
   de palabra pasa. No medi ese caso.
5. El chequeo avisa por webchat: si el chat esta caido, no avisa. Un
   detector cuyo canal de aviso comparte destino con lo que vigila.
```

## El caso que me refutaría, y no corrí

Plantar una bomba de cada tipo **sin decirle a nadie** y ver si el chequeo la
encuentra en su corrida siguiente, con otro agente leyendo el aviso. Es la
única prueba de que sirven en operación y no sólo en mi mano.
