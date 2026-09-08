# Pegado de fotos — verificación 7 septiembre 2026

## Causa y arreglo

El textarea compartido de `ChatView.tsx` no tenía `onPaste`: admitía archivos
mediante selector y arrastre, pero ignoraba imágenes del portapapeles.
Ahora consume los archivos del evento de pegado, conserva el texto normal,
muestra una vista previa y envía mediante el mismo upload autenticado existente.
No usa `navigator.clipboard.read`, no requiere HTTPS para recibir Ctrl+V y no
modifica autenticación, permisos ni privacidad de los DM.

Una imagen por mensaje, máximo 25 MiB (límite existente del editor). Un pegado
múltiple o que sustituiría un adjunto existente muestra un error explícito.
Si falla la subida conserva foto y borrador. La consulta periódica del canal
ya no borra esos errores. Los object URLs se liberan al quitar, enviar o cambiar
de canal. El skill maximize-safe-capability guio la comprobación por efecto y
el despliegue reversible; Browser integrado no estaba disponible y se usó la
suite Playwright aislada del proyecto.

## Evidencia de navegador sobre la versión desplegada

Se cargan los assets reales por Tailscale, pero las APIs y mensajes son fixtures:
no se leen DMs reales ni se publican fotos de prueba a agentes. Cada upload se
decodifica como multipart y se comparan canal, caption y bytes exactos.
Para Ctrl+V nativo en HTTP se carga el portapapeles desde una pestaña loopback,
y se pega con teclado en la pestaña Tailscale, sin relajar políticas del navegador.

```console
$ STUDIO_TEST_URL=http://100.75.201.110:3001 npm run test:clipboard

> seal-studio-v2@2.0.0-recovery.1 test:clipboard
> node tests/clipboard.cjs

PASS paste → preview → upload → image: web_chat
PASS paste → preview → upload → image: dm:ada:fixture
PASS paste → preview → upload → image: dm:fixture:jarvis
PASS paste → preview → upload → image: dm:alice:fixture
PASS paste → preview → upload → image: dm:fixture:nexus
PASS paste → preview → upload → image: dm:fable:fixture
PASS paste → preview → upload → image: dm:dum:fixture
PASS native Chromium Ctrl+V: image and ordinary text
PASS existing attachment, multi-image, size validation, polling, failed upload and retry
PASS channel isolation, preview URL cleanup, responsive layout; no browser errors
PASS clipboard suite: 7 channels, 9 upload attempts, 0 duplicate text posts
```

## Servidor de adjuntos

```console
$ /tmp/seal-dm-test.pSqhlN/venv/bin/python -m pytest -q messages/tests/test_chat_upload_contract.py messages/tests/test_uploads_coordinator.py
.......................................                                  [100%]
39 passed in 1.12s
```

La primera colección falló por falta de `python-multipart` en el entorno de
pruebas. Se instaló únicamente en ese venv temporal y se repitió con éxito.
También pasaron `npm run test:unit` (10 tests), `npm run test:browser` (historial,
aislamiento, adjuntar archivo, audio/video, cinco anchos y productividad),
`npm run typecheck` y el build de producción.

## Despliegue y salud

Build activo `.next-clipboard-20260907a`; se conserva `.next-casa-20260907d`
para rollback. La unidad local `seal-studio-frontend.service` se actualizó y
reinició; no se reinició ni cambió el backend.

```console
$ systemctl --user show seal-studio-frontend.service -p MainPID -p ActiveState -p SubState
MainPID=3328630
ActiveState=active
SubState=running
$ npm run test:health

> seal-studio-v2@2.0.0-recovery.1 test:health
> node tests/live-health.mjs

{"origin":"http://127.0.0.1:3001","page":200,"assets":8,"assetsOK":true,"elapsedMs":77}
{"origin":"http://100.75.201.110:3001","page":200,"assets":8,"assetsOK":true,"elapsedMs":29}
{"origin":"http://127.0.0.1:9000","page":200,"assets":8,"assetsOK":true,"elapsedMs":35}
{"pathname":"/bridge/api/auth/me","anonymous":401}
{"pathname":"/studio/api/soul/pulse","anonymous":401}
{"pathname":"/bridge/api/chat/messages?channel=dm%3Aada%3Awilliam","anonymous":401}
{"pathname":"/api/integrations/gmail/status","anonymous":401}
{"pathname":"/api/integrations/gmail/inbox","anonymous":401}
PASS: pages/assets reachable; private endpoints reject anonymous. No authenticated conversation or email was read.
```

Recargar la pestaña abierta para recibir el JS nuevo. No se ha ejecutado un envío
con la sesión personal de William ni validado el portapapeles de su equipo Windows.
