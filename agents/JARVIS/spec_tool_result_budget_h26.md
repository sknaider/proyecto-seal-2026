# Spec H2.6 — Tool Result Budget (PostToolUse Hook)
> Owner: JARVIS (diseño) | ADA (implementación) | 2026-04-19

## Problema

El GrowthBook flag `tengu_hawthorn_window` controla el presupuesto máximo de tool results por mensaje (default: 200,000 chars). Si un agente hace 5 lecturas de archivos grandes → 1M chars en contexto → compactación prematura → costo.

El Tool Budget Hook (H2.4) ya limita *cuántas* herramientas se usan. Este spec controla el *tamaño* de cada resultado.

## Solución propuesta — PostToolUse hook

**Archivo**: `memory/tool_result_budget_hook.py`
**Hook event**: `PostToolUse`
**Límites por herramienta**:

| Herramienta | Límite chars | Estrategia de truncado |
|-------------|-------------|------------------------|
| Read | 8,000 | head+tail (primeras 3K + últimas 1K + mensaje "[N líneas omitidas]") |
| Grep | 4,000 | primeros N matches + "N más omitidos" |
| Bash | 6,000 | stdout truncado al final (lo relevante suele estar al final) |
| WebFetch | 5,000 | head + resumen de sección omitida |
| Default | 10,000 | head truncado |

## Formato del output truncado

```
[RESULT TRUNCADO: 45,234 chars → 8,000. Muestra 3,500 inicio + 500 fin.]

<primeros 3,500 chars>

... [38,734 chars omitidos] ...

<últimos 500 chars>
```

## Implementación PostToolUse hook (schema)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/tool_result_budget_hook.py",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

## Input del hook (stdin JSON)

```json
{
  "tool_name": "Read",
  "tool_input": {"file_path": "..."},
  "tool_response": "<contenido del archivo>",
  "session_id": "..."
}
```

## Output del hook (stdout JSON)

Para reemplazar el resultado:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "toolResponse": "<resultado truncado>"
  }
}
```

Para pasar sin cambios (resultado dentro del límite):
```json
{}
```

## ROI estimado

- Ahorro contexto: 40-60% en sesiones con muchas lecturas de archivos grandes
- Impacto en compactaciones: menos frecuentes → -20% costo compactación
- Riesgo: truncar info relevante → mitigado con head+tail strategy

## Condiciones de bypass

- Variables `SEAL_TOOL_BUDGET_BYPASS=1` → no truncar (para tareas que necesitan archivos completos)
- Archivos <2,000 chars → no truncar nunca
- Herramientas de escritura (Edit, Write) → no aplica

## Notas para ADA

1. Verificar primero con `python3 -c "import json,sys; d=json.load(sys.stdin); print(d.keys())"` que el schema de input sea correcto (el PostToolUse hook puede variar entre versiones)
2. Testear con un archivo grande (>10K chars) — leer el roadmap_consolidated como caso de prueba
3. El hook debe terminar en <3s o Claude Code lo descarta

*Spec v1.0 — JARVIS — 2026-04-19*
