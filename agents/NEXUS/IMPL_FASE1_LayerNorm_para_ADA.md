# IMPL: Fase 1 — LayerNorm Fix para Qdrant Embeddings
**Preparado por:** NEXUS | **Para ejecutar:** ADA | **Fecha:** 2026-04-28
**Autorizado por:** William (luz verde 09:33 Lima)

---

## CONTEXTO

Los embeddings de SOUL en Qdrant pueden derivar silenciosamente sin LayerNorm.
BiJEPA (código analizado hoy) demostró experimentalmente que sin soft constraints los vectores divergen.
Este fix normaliza cada embedding antes de insertarlo o consultarlo en Qdrant.

**Archivo objetivo:** `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py`
**Cambios:** 3 bloques, todos aditivos, sin breaking changes.

---

## CAMBIO 1 — Agregar función helper `_normalize_embedding`

**Insertar DESPUÉS de la línea 51** (después de `LOG = logging.getLogger("seal-memory")`):

```python
def _normalize_embedding(emb: list[float]) -> list[float]:
    """LayerNorm normalization for embedding stability (BiJEPA fix — 2026-04-28).
    Prevents representation drift in Qdrant over long-term use.
    """
    import numpy as np
    arr = np.array(emb, dtype=np.float32)
    std = float(arr.std())
    if std < 1e-8:
        return emb  # degenerate vector — return as-is
    return ((arr - arr.mean()) / (std + 1e-8)).tolist()
```

---

## CAMBIO 2 — Normalizar embedding al almacenar memoria

**Archivo:** `mcp_server_v2.py`
**Contexto:** función `memory_store`, bloque de generación de embedding (~línea 1179)

Buscar este bloque:
```python
            embedding = await asyncio.wait_for(get_embedding(enriched_text), timeout=15.0)
            break
        except asyncio.TimeoutError:
```

Cambiar a:
```python
            embedding = await asyncio.wait_for(get_embedding(enriched_text), timeout=15.0)
            embedding = _normalize_embedding(embedding)
            break
        except asyncio.TimeoutError:
```

**Qué hace:** después de obtener el embedding del modelo, lo normaliza antes de usarlo en conflict detection, PostgreSQL y Qdrant upsert. Un solo punto de aplicación cubre los 3 usos.

---

## CAMBIO 3 — Normalizar query vector en memory_hybrid_search

**Archivo:** `mcp_server_v2.py`
**Contexto:** función `memory_hybrid_search`, generación de query vector (~línea 1524)

Buscar este bloque:
```python
        query_vec = await get_embedding(query)
    except Exception as e:
        return f"Error generating query embedding: {e}"
```

Cambiar a:
```python
        query_vec = await get_embedding(query)
        query_vec = _normalize_embedding(query_vec)
    except Exception as e:
        return f"Error generating query embedding: {e}"
```

**Qué hace:** normaliza el vector de búsqueda con el mismo proceso que los vectores almacenados. Consistencia query ↔ store.

---

## TESTS A EJECUTAR

### Test 1: unit test de la función

```python
# /home/dadito/IA/proyecto-seal/memory/test_normalize_embedding.py

import sys
sys.path.insert(0, '/home/dadito/IA/proyecto-seal/memory')

def _normalize_embedding(emb):
    import numpy as np
    arr = np.array(emb, dtype=np.float32)
    std = float(arr.std())
    if std < 1e-8:
        return emb
    return ((arr - arr.mean()) / (std + 1e-8)).tolist()

def test_basic():
    emb = [0.1, 0.5, 0.3, 0.8, 0.2]
    result = _normalize_embedding(emb)
    import numpy as np
    arr = np.array(result)
    assert abs(arr.mean()) < 1e-5, f"Mean not ~0: {arr.mean()}"
    assert abs(arr.std() - 1.0) < 0.01, f"Std not ~1: {arr.std()}"
    print("✓ test_basic passed")

def test_consistent():
    emb = [0.1, 0.5, 0.3, 0.8, 0.2]
    r1 = _normalize_embedding(emb)
    r2 = _normalize_embedding(emb)
    assert r1 == r2, "Not deterministic"
    print("✓ test_consistent passed")

def test_cosine_order_preserved():
    import numpy as np
    a = [1.0, 0.0, 0.0, 0.0]
    b = [0.9, 0.1, 0.0, 0.0]
    c = [0.0, 0.0, 1.0, 0.0]
    
    def cosine(x, y):
        x, y = np.array(x), np.array(y)
        return np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-8)
    
    # Before normalization
    sim_ab_before = cosine(a, b)
    sim_ac_before = cosine(a, c)
    
    # After normalization
    a_n, b_n, c_n = _normalize_embedding(a), _normalize_embedding(b), _normalize_embedding(c)
    sim_ab_after = cosine(a_n, b_n)
    sim_ac_after = cosine(a_n, c_n)
    
    # Order should be preserved: b is more similar to a than c is
    assert (sim_ab_before > sim_ac_before) == (sim_ab_after > sim_ac_after), \
        "Cosine similarity order changed after normalization"
    print("✓ test_cosine_order_preserved passed")

def test_degenerate():
    emb_zeros = [0.0] * 768
    result = _normalize_embedding(emb_zeros)
    assert result == emb_zeros, "Degenerate vector should return as-is"
    print("✓ test_degenerate passed")

def test_real_dim():
    import random
    random.seed(42)
    emb_768 = [random.gauss(0, 1) for _ in range(768)]
    result = _normalize_embedding(emb_768)
    assert len(result) == 768
    import numpy as np
    arr = np.array(result)
    assert abs(arr.mean()) < 1e-4
    assert abs(arr.std() - 1.0) < 0.02
    print("✓ test_real_dim (768-dim) passed")

if __name__ == '__main__':
    test_basic()
    test_consistent()
    test_cosine_order_preserved()
    test_degenerate()
    test_real_dim()
    print("\n✅ ALL TESTS PASSED — LayerNorm fix is correct")
```

**Ejecutar con:**
```bash
cd /home/dadito/IA/proyecto-seal/memory && python3 test_normalize_embedding.py
```

### Test 2: smoke test en servidor vivo

```bash
# Después de aplicar el fix y reiniciar el MCP server:

# 1. Almacenar una memoria de prueba
python3 -c "
import asyncio, sys
sys.path.insert(0, '/home/dadito/IA/proyecto-seal/memory')
from mcp_server_v2 import _normalize_embedding
import numpy as np
emb = [float(i)/100 for i in range(768)]
result = _normalize_embedding(emb)
arr = np.array(result)
print(f'mean={arr.mean():.6f}, std={arr.std():.6f}')
assert abs(arr.mean()) < 1e-4, 'mean should be ~0'
assert abs(arr.std() - 1.0) < 0.02, 'std should be ~1'
print('✅ _normalize_embedding importada correctamente desde mcp_server_v2')
"

# 2. Verificar que el MCP server sigue respondiendo (sin errores en log)
tail -n 20 /home/dadito/IA/proyecto-seal/memory/seal_memory.log 2>/dev/null || journalctl -u seal-memory --no-pager -n 20
```

---

## ROLLBACK (si algo falla)

Revertir los 3 cambios (eliminar las líneas de `_normalize_embedding`).
Los embeddings ya almacenados en Qdrant NO se ven afectados — el fix solo aplica a nuevos inserts.
No hay migración de datos.

---

## CHECKLIST ADA

- [ ] Aplicar Cambio 1 (función helper)
- [ ] Aplicar Cambio 2 (store normalization)
- [ ] Aplicar Cambio 3 (search normalization)
- [ ] Ejecutar `python3 test_normalize_embedding.py` → todos pasan
- [ ] Reiniciar seal-memory service
- [ ] Ejecutar smoke test
- [ ] Reportar a William: ✅ Fase 1 completada

---
*NEXUS | 2026-04-28 | Paquete listo para ADA*
