---
auto_invoke: true
name: supabase_best_practices
description: Mejores prácticas de seguridad, rendimiento y diseño para bases de datos Supabase (PostgreSQL).
---

# Supabase & PostgreSQL Best Practices

Guía esencial para desarrollar aplicaciones seguras y escalables con Supabase.

## 1. Seguridad (Row Level Security - RLS)
**MANDATORIO:** Nunca desactives RLS en tablas públicas.
- Habilita RLS: `alter table "mi_tabla" enable row level security;`
- Crea políticas explícitas:
  ```sql
  create policy "Usuarios pueden ver sus propios datos"
  on "mi_tabla" for select
  using ( auth.uid() = user_id );
  ```
- **Service Role Key:** JAMÁS la expongas en el cliente (frontend). Úsala solo en el backend/Server Actions para tareas administrativas.

## 2. Rendimiento de Consultas
- **Índices:** Crea índices en columnas que uses en `WHERE`, `JOIN` y `ORDER BY`.
  - *Tip:* Supabase tiene un "Index Advisor" en el dashboard.
- **Select Específico:** Evita `select *`. Pide solo las columnas que necesitas.
  - `const { data } = await supabase.from('users').select('id, name')`
- **Count:** Evita `count(*)` exacto en tablas gigantes. Usa conteos estimados si es posible.

## 3. Conexiones (Connection Pooling)
- En entornos Serverless (como Next.js deployed en Vercel), **SIEMPRE usa el Connection Pooler** (Supavisor) de Supabase, no la conexión directa (puerto 5432).
- URL de Transacción: Puerto 6543 (Modo Transacción). Úsalo para casi todo en aplicaciones web.
- URL de Sesión: Puerto 5432. Úsalo solo para migraciones o conexiones de larga duración.

## 4. Tipado (TypeScript)
- Genera los tipos automáticamente con el CLI de Supabase:
  `npx supabase gen types typescript --project-id "tu-id" > src/types/supabase.ts`
- Usa estos tipos en tu cliente para tener autocompletado y seguridad de tipos en toda la DB.

## 5. Almacenamiento (Storage)
- Usa buckets privados por defecto.
- Configura políticas de Storage (similares a RLS) para restringir quién puede subir o descargar archivos.
