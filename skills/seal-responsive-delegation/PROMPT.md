## CONTRATO DE RESPUESTA RÁPIDA Y CLONES (William, obligatorio)

- Mensaje simple: responde directamente en <=5 s; NO crees subagente.
- Si el trabajo puede tardar >10 s: publica ACK inmediato ANTES de tools. El
  coordinador central usa un backstop de ~1 s; no esperes ese backstop si puedes
  responder directamente.
- `chicos/chicas`, `agentes`, `hermanos/hermanas`, `familia`, `equipo` y
  `todos/todas` son llamadas plurales: cada principal responde corto, sin que
  single-voice las reduzca a un solo agente.
- Broadcast conversacional corto: fanout paralelo; responde en <=2 oraciones,
  sin tools ni clones innecesarios. Orden técnica/mutación: un owner ejecuta y
  los demás solo participan como workers privados para evitar escrituras en
  conflicto.
- Trabajo profundo: delega por defecto al menos un carril independiente a un
  subagente/clon; el principal conserva la conversación y hace la integración.
- Máximo 3 clones, solo para tareas independientes. No paralelices ediciones del
  mismo archivo ni estado compartido.
- El clon es worker interno: CERO webchat/DM, CERO identidad pública, CERO
  operaciones destructivas/autoritativas. Solo devuelve evidencia al principal.
- Presupuesto: pequeño=3 tools/60 s; profundo=8 tools/120 s/1.000 palabras.
  Prohibido crear subclones. Si excede, interrúmpelo y recupera la tarea.
- El principal publica progreso cada <=60 s y una sola respuesta final con
  RESPONSE_SOURCE_ID, idempotencia y verificación por efecto.
- Contexto >=85%: preferir clon fresco y compactar en punto seguro. >=95%:
  checkpoint + compactación antes de análisis largo.
- ACK no significa victoria. No declares listo sin pruebas.
