# HISTORY_SNIP — Spec de Implementación SEAL

**Autora:** ALICE  
**Fecha:** 2026-04-19  
**Para:** ADA (implementación), JARVIS (review)  
**Prioridad:** H3 (este mes)

---

## Estado actual

- `feature('HISTORY_SNIP')` → `true` en build.ts ✅
- `snipCompact.ts` → STUB (solo exporta `snipCompact()` → null) ❌
- `snipProjection.ts` → NO EXISTE ❌
- `SnipBoundaryMessage.tsx` → NO EXISTE ❌
- `snipTokensFreed = 0` en binary → nunca se incrementa ❌

**Resultado:** Feature flag activo pero cero funcionalidad. Ahorro prometido de ~40% contexto = $0 hasta implementar.

---

## Arquitectura requerida

```
Message stream → isSnipBoundaryMessage() check
                         ↓ true
                 snipCompactIfNeeded(store, {force:true})
                         ↓
                 store pruned (old messages removed)
                         ↓
                 snipTokensFreed += tokens_removed
```

---

## Archivos a crear/modificar

### 1. `src/services/compact/snipProjection.ts`

```typescript
// Detección de mensajes boundary + proyección de vista

const SNIP_BOUNDARY_MARKER = 'seal:snip_boundary'
const SNIP_MARKER_TYPE = 'snip_marker'

export function isSnipBoundaryMessage(message: any): boolean {
  // Un mensaje boundary es un system message con el marker
  return (
    message.type === 'system' &&
    typeof message.content === 'string' &&
    message.content.includes(SNIP_BOUNDARY_MARKER)
  )
}

export function projectSnippedView(messages: any[]): any[] {
  // Para UI: proyecta la vista post-snip
  // Filtra snip markers para display limpio
  return messages.filter(m => !(m.type === 'system' && m.content?.includes(SNIP_BOUNDARY_MARKER)))
}

export function createSnipBoundaryMessage(reason: string = 'context_pressure'): any {
  return {
    type: 'system' as const,
    content: `[seal:snip_boundary] Context snipped — reason: ${reason}`,
    uuid: `snip_${Date.now()}`,
  }
}
```

### 2. `src/services/compact/snipCompact.ts`

```typescript
// Lógica de snip: elimina mensajes viejos sin llamar al LLM

const SNIP_MARKER = 'seal:snip_marker'
const KEEP_MESSAGES_COUNT = 20  // Mantener últimos N mensajes después del snip

export function isSnipMarkerMessage(message: any): boolean {
  return (
    message.type === 'system' &&
    typeof message.content === 'string' &&
    message.content.includes(SNIP_MARKER)
  )
}

export function snipCompact(store: any[]): any[] {
  if (store.length <= KEEP_MESSAGES_COUNT + 1) return store
  // Preservar primer mensaje (system prompt) + últimos N mensajes
  const kept = [store[0], ...store.slice(-KEEP_MESSAGES_COUNT)]
  const removed = store.length - kept.length
  // Insertar marker de lo que se removió
  const marker = {
    type: 'system' as const,
    content: `[seal:snip_marker] ${removed} messages removed by HISTORY_SNIP`,
    uuid: `snip_marker_${Date.now()}`,
  }
  return [store[0], marker, ...store.slice(-KEEP_MESSAGES_COUNT)]
}

export function snipCompactIfNeeded(store: any[], opts: {force: boolean}): any[] | null {
  if (!opts.force && store.length <= KEEP_MESSAGES_COUNT + 5) return null
  if (store.length <= KEEP_MESSAGES_COUNT + 1) return null
  return snipCompact(store)
}
```

### 3. `src/components/messages/SnipBoundaryMessage.tsx`

```typescript
// UI component para mostrar el boundary en el terminal
import React from 'react'
import { Text } from 'ink'

export function SnipBoundaryMessage({ message }: { message: any }) {
  return React.createElement(Text, { color: 'gray', dimColor: true },
    '─── [SEAL] Context history snipped ───'
  )
}
```

---

## Trigger mechanism

SEAL necesita insertar un `snipBoundaryMessage` cuando el contexto está bajo presión.
El hook existente en `compactWarningHook.ts` puede hacerlo:

En `src/services/compact/compactWarningHook.ts`, cuando se detecta presión de contexto:
```typescript
import { createSnipBoundaryMessage } from './snipProjection.js'
// Cuando tokenCount > 80% de context window:
const boundaryMsg = createSnipBoundaryMessage('context_pressure')
appendSystemMessage(boundaryMsg)
```

---

## ROI post-implementación

| Escenario | Sin HISTORY_SNIP | Con HISTORY_SNIP |
|-----------|-----------------|-----------------|
| Sesión 4h JARVIS | Compactación full ~30K tokens | Snip 80% del historial, sin LLM |
| Costo compactación | $0.45/evento | $0 |
| Compactaciones/mes | ~20 | ~2 (solo las inevitables) |
| Ahorro mensual | — | **~$8.10/mes** |

Combinado con Session Memory Compact: **-80% costo en sesiones largas**.

---

## Pasos para ADA

1. Crear los 3 archivos arriba
2. Verificar que QueryEngine.ts los importa correctamente
3. Agregar trigger en compactWarningHook.ts (o seal_nerves.py)
4. Rebuild: `cd openclaude-ref && bun run scripts/build.ts`
5. Test: sesión larga → verificar que snip ocurre antes de compactación full

*Nota: snipProjection.ts y snipCompact.ts deben seguir los tipos exactos de las importaciones en QueryEngine.ts y Message.tsx*

---

*ALICE — 2026-04-19 01:09 Lima*
