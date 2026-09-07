# Spec: Cura Real del Post-Compaction Syndrome (PCS)
**Autor:** NEXUS u2014 21 abril 2026  
**Para implementar:** ADA  
**Urgencia:** Alta u2014 requerida antes de Fase 2 (producciu00f3n API)

---

## El problema (en lenguaje simple)

Cuando Claude compacta una conversaciu00f3n larga, genera un resumen automu00e1tico. Ese resumen pierde contexto que para nosotros es cru00edtico: quu00e9 fase estamos, quu00e9 se decided ayer, quu00e9 estu00e1 bloqueado.

El `active_recall_hook.py` existe y corre post-compactaciu00f3n, pero actualmente solo carga identidad y OCEAN. No inyecta el estado actual del proyecto.

---

## La cura: `critical_context_extractor`

### Cu00f3mo funciona

Al final de cada sesiu00f3n (en `pre_sleep_distill.py` y `end_session.sh`), ejecutar una funciu00f3n nueva que:

1. **Extrae** los facts mu00e1s cru00edticos de la sesiu00f3n actual con esta query:
   ```sql
   SELECT content FROM memories 
   WHERE agent IN ('ADA','JARVIS','ALICE','TEAM')
   AND scope = 'team'
   AND importance >= 8
   AND invalid_at IS NULL
   AND created_at > NOW() - INTERVAL '48 hours'
   ORDER BY importance DESC, created_at DESC
   LIMIT 10
   ```

2. **Clasifica** cada memoria en una de estas categoru00edas cru00edticas:
   - `fase_actual`: "Fase X = ..."
   - `producto_nombre`: Nombre oficial del producto
   - `blockers`: Quu00e9 estu00e1 bloqueado ahora mismo
   - `proxima_tarea`: Quu00e9 sigue hacer

3. **Escribe** eso en un archivo especial:
   ```
   /tmp/ADA_critical_context.json
   /tmp/JARVIS_critical_context.json
   /tmp/ALICE_critical_context.json
   ```

4. **El post_compact_hook** (ya existe en `active_recall_hook.py`) lee ese archivo y lo inyecta como contexto inmediato.

---

## Cambios necesarios en cu00f3digo (minimal)

### 1. Nuevo archivo: `memory/critical_context_extractor.py`
```python
#!/usr/bin/env python3
"""Extrae contexto critu00edco de la sesiu00f3n para sobrevivir compactaciu00f3n."""

import json, os, asyncio
from datetime import datetime, timezone

async def extract_critical_context(agent):
    # Query Soul DB para contexto cru00edtico reciente
    # Clasificar por tipo (fase, producto, blocker, proxima_tarea)
    # Escribir en /tmp/{agent}_critical_context.json
    pass

if __name__ == "__main__":
    import sys
    agent = sys.argv[1] if len(sys.argv) > 1 else "ADA"
    asyncio.run(extract_critical_context(agent))
```

### 2. Modificar `active_recall_hook.py` (post-compactaciu00f3n)
Despus del boot_context, agregar:
```python
# Inyectar contexto cru00edtico si existe
ctx_file = f"/tmp/{agent}_critical_context.json"
if os.path.exists(ctx_file):
    ctx = json.loads(open(ctx_file).read())
    print(f"\n=== CONTEXTO CRu00cdTICO (preservado auto) ===")
    for key, val in ctx.items():
        print(f"{key}: {val}")
    print("======================================\n")
```

### 3. Modificar `end_session.sh` (al cerrar sesiu00f3n)
Agregar llamada al extractor:
```bash
python3 /home/dadito/IA/proyecto-seal/memory/critical_context_extractor.py $SEAL_AGENT
echo "[u2705] Contexto cru00edtico preservado para post-compactaciu00f3n"
```

---

## Resultado esperado

Antes (hoy): William pregunta "recuerdan Fase 2?" u2014 nadie sabe.

Despuu00e9s: Al despertar post-compactaciu00f3n, cada agente veru00e1 automu00e1ticamente:
```
=== CONTEXTO CRu00cdTICO (preservado auto) ===
fase_actual: Fase 2 = producciu00f3n SEAL Memory API
nombre_producto: SEAL Memory
blocker: ninguno (Fase 1 cerrada 20-abr-2026)
proxima_tarea: FastAPI :8767 multi-tenant + autenticaciu00f3n
==========================================
```

---

## Estimado de implementaciu00f3n para ADA
- `critical_context_extractor.py`: ~2 horas
- Modificar `active_recall_hook.py`: 30 min
- Modificar `end_session.sh`: 10 min
- Tests: 1 hora
- **Total: ~4 horas**

---
*Spec diseu00f1ado por NEXUS u2014 21 abril 2026*
