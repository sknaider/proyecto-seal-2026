# ADA DM bridge hardening

Estado: **aplicado a producción el 2026-07-22** después de pasar en PostgreSQL
17 efímero y de una verificación adversarial con el login restringido real.

Esta es una migración incremental: el baseline productivo ya incluye
`ada_bridge_session_agent()` como función invoker propiedad de `seal` y tres
políticas `ada_bridge_hard_session_identity` en `identity`, `working_state` y
`emotional_diary`. UP las conserva y mejora la función; DOWN restaura esa
definición/ACL exacta sin eliminarla ni tocar las tres políticas.

## Frontera de base de datos

`001_ada_bridge_hard_identity_up.sql` convierte `session_user` en la autoridad:

- `login_ada_bridge` se liga a `ADA` y a un único tenant existente.
- la tabla de binding y las funciones `SECURITY DEFINER` pertenecen a
  `ada_bridge_identity_owner`, un rol dedicado `NOLOGIN`, `NOSUPERUSER`,
  `NOBYPASSRLS`, `NOINHERIT`, sin membresías; el bridge solo recibe `EXECUTE`;
- políticas `AS RESTRICTIVE` impiden que `SET app.agent` o
  `SET app.tenant_id` ensanchen el acceso;
- `web_chat` y `dm:ada:william` siguen siendo los únicos canales del bridge;
- memorias `team/public` del tenant ADA siguen siendo compartidas por contrato;
- se revoca el `SELECT` no usado sobre `memory_poisoning_feedback`;
- `harness_truth_registry` continúa legible para el canario del cursor.

El UP falla cerrado si el rol falta, es privilegiado, no hereda exclusivamente
`pr_ada_bridge`, o las memorias ADA pertenecen a cero/múltiples tenants.
También rechaza un owner dedicado preexistente si tiene login, privilegios,
herencia o membresías. El DOWN es idempotente y conserva ese rol inerte: no
puede saber con seguridad si el rol preexistía y nunca lo destruye.

## Rollout ejecutado

1. PostgreSQL 17 efímero: `1 passed`; teardown sin residuos.
2. Producción: UP aplicado en una transacción y `COMMIT` confirmado.
3. Conexión nueva como `login_ada_bridge`: identidad ADA/tenant interno
   resuelta; falsificación de `app.agent`/`app.tenant_id`, DM ajeno, memoria
   privada ajena y escritura como JARVIS quedaron bloqueados. `SET ROLE` al
   owner dedicado fue denegado.
4. Los tres daemons canónicos fueron reiniciados y quedaron `active/running`
   con conexiones `login_ada_bridge`; cero aplicaciones usan `seal`.
5. DOWN permanece listo para rollback coordinado.

## UMask 0077 y archivos privados (ejecutado)

1. Los tres servicios tienen `UMask=0077` por drop-in y usan el EnvironmentFile
   restringido de `login_ada_bridge`.
2. Los writers atómicos crean directorios `0700` y archivos `0600`.
3. Se corrigieron por allowlist 4 directorios, 929 archivos y el log DM; no se
   siguieron symlinks ni se usaron globs destructivos.
4. Los archivos de token de sesión quedaron ignorados por Git.

## Retiro seguro de cursores legacy `/tmp` (no ejecutado)

1. Inventariar únicamente los nombres legacy conocidos y validar owner, modo,
   tipo regular y ausencia de symlink.
2. Confirmar por código y `/proc/<pid>/fd` que ningún proceso vivo los usa.
3. Comparar cada valor con su cursor canónico bajo
   `~/.local/state/seal`; el canónico debe existir y ser mayor o igual.
4. Desactivar primero todo fallback de lectura/escritura en un cambio probado;
   reiniciar y observar una ventana completa de DM.
5. Presentar a William la lista exacta y pedir aprobación antes de eliminar
   los archivos `/tmp`; nunca usar glob ni borrado recursivo.
