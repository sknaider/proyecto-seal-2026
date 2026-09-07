# SOUL Memory SDK — edge externo `api.soulsmemory.com`

Estado: **preparado, no desplegado**. Este runbook no autoriza un lanzamiento.
El vhost de la API es independiente de `soulsmemory.com` y no debe mezclarse
con su landing, snapshot público ni `/api/demo-request`.

## Frontera preparada

- Cliente externo → TLS 1.2/1.3 en Nginx (`api.soulsmemory.com`).
- Nginx → gateway loopback `127.0.0.1:8780`.
- El origen sigue sin listener público; no se publica `:8780`, `:8768` ni
  `172.22.0.1:8767` mediante NAT/firewall.
- Superficie pública allowlisted: `/health`, `/openapi.json` y las rutas
  canónicas `/v1/memories`, `/v1/memories/{id}`, `/v1/recall`,
  `/v1/tenant/memories`, `/v1/tenant/recall`.
- Todo lo demás devuelve 404; aliases legacy y `/v1/admin` no se exponen.
- Nginx impone límite agregado, límite por peer, conexiones, cuerpo de 1 MiB,
  timeouts y `X-Request-ID`. La cuota por API key permanece en el gateway.
- `X-Forwarded-For` del cliente nunca se usa como identidad: Nginx lo reemplaza
  por `$remote_addr`; no hay `real_ip_header` hasta definir un proxy confiable.

## Prerrequisitos exactos antes de habilitar

1. P1 de identidad/RLS del Memory SDK cerrado y verificado adversarialmente.
2. API y gateway verdes en `127.0.0.1:8768` y `127.0.0.1:8780`.
3. Una API key canario con scopes mínimos, expiración, revocación y secreto
   entregado fuera de logs/repositorio.
4. DNS `A api.soulsmemory.com` apuntando a la IPv4 pública correcta. Crear
   `AAAA` solo si el host tiene IPv6 pública filtrada y funcional; si no, omitirlo.
5. Certificado y clave dedicados con SAN `api.soulsmemory.com`, por ejemplo:
   `/etc/letsencrypt/live/api.soulsmemory.com/fullchain.pem` y `privkey.pem`.
   Adquirirlo antes de habilitar (preferible DNS-01). Este artefacto no ejecuta ACME.
6. Entrada TCP/443 permitida al Nginx; TCP/80 es opcional y solo redirige a TLS.
   Los puertos de origen deben permanecer bloqueados externamente.
7. Confirmar que no existe otro `server_name api.soulsmemory.com`:
   `sudo /usr/sbin/nginx -T | grep -n 'server_name api.soulsmemory.com'`.
8. Ventana de cambio, observador de logs y rollback acordados. No habilitar solo
   porque DNS/cert existan.

## Render y preflight (sin activar)

```bash
python3 ops/render_api_soulsmemory_nginx.py \
  --certificate /etc/letsencrypt/live/api.soulsmemory.com/fullchain.pem \
  --certificate-key /etc/letsencrypt/live/api.soulsmemory.com/privkey.pem \
  --output /tmp/api.soulsmemory.com.conf

sudo /usr/sbin/nginx -t
```

El segundo comando valida el runtime actual, no el archivo aún no instalado.
Para probar el template aislado, incluido TLS, usar:

```bash
python3 -m pytest -q tests/test_api_soulsmemory_nginx.py
```

## Despliegue futuro (solo en ventana autorizada)

1. Renderizar a `/tmp/api.soulsmemory.com.conf` y revisar el diff.
2. `sudo install -o root -g root -m 0644 /tmp/api.soulsmemory.com.conf /etc/nginx/sites-available/api.soulsmemory.com`
3. Crear únicamente el enlace `/etc/nginx/sites-enabled/api.soulsmemory.com`.
4. `sudo /usr/sbin/nginx -t`; si falla, retirar el enlace y no recargar.
5. `sudo systemctl reload nginx` (reload, no restart).
6. Probar TLS/SNI, headers, 404 de ruta no allowlisted, 401 sin key, canario
   autenticado y 429 de rate limit. Revisar que el upstream siga en loopback.

## Rollback

1. Retirar únicamente el enlace
   `/etc/nginx/sites-enabled/api.soulsmemory.com`; conservar el archivo en
   `sites-available` para análisis.
2. Ejecutar `sudo /usr/sbin/nginx -t`.
3. Solo con test verde, `sudo systemctl reload nginx`.
4. Confirmar que `soulsmemory.com` sigue intacto y que `127.0.0.1:8780/health`
   continúa verde. El rollback del edge no toca datos, roles ni el gateway.

## Evidencia mínima de aceptación

- `nginx -t` exitoso con el vhost instalado.
- TLS válido y hostname/SAN correctos.
- HTTP redirige; HTTPS responde; TLS antiguo se rechaza.
- `/openapi.json` contiene solo contrato público canónico.
- `POST/GET` permitidos funcionan con key canario; método/ruta no allowlisted
  falla; cuerpo >1 MiB falla 413; ráfaga excedida falla 429.
- `X-Request-ID` aparece en respuesta y upstream.
- Un `X-Forwarded-For` falsificado no altera el peer/rate bucket.
- Ningún puerto de origen es alcanzable desde Internet.
