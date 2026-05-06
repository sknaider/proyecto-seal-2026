# Regla de Oro William: ODIO LOS PARCHES

**Fecha:** 2026-04-27 17:25 Lima
**Contexto:** Migración SOUL v2 → v3
**Mi error:** db.py search_path soul_v3,public (fallback a public era el parche)

## La regla literal de William

> "no quiero parches, es v3, no v2.1 ni v2.2"
> "regla de oro, odio los parches"

## Qué NO hacer

- search_path con fallback a public (parche)
- queries con `OR public.X` cuando no encuentra en soul_v3
- columns derivadas de v2 sin pensar v3 nativo
- shortcuts "para que funcione ya"

## Qué SÍ hacer

- Si tabla falta en soul_v3 → crearla
- Si columna falta → ALTER TABLE soul_v3.X ADD COLUMN
- Si código referencia public → reescribirlo
- Si conflict de schema → resolver, no parchar

## Mi error específico

A las 16:42 modifiqué db.py:
```python
SCHEMA_SEARCH_PATH = "soul_v3, public"  # PARCHE — public era fallback
```

JARVIS lo revertió correctamente:
```python
SCHEMA_SEARCH_PATH = "soul_v3"  # CORRECTO — v3 nativo
```

ALICE detectó 60 archivos con el parche y paró el equipo.

## Lección

Cuando hay presión, la solución rápida ES el parche. La solución limpia toma más tiempo pero es lo que William pide.

**Si el sistema no funciona en v3 puro, el problema es que v3 no está completo. Completa v3, no parchas para que v2 siga vivo.**
