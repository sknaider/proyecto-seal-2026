# SOUL one-click `.exe` (Inno Setup)

Genera `SOUL-Setup.exe`: doble-click → instala SOUL (sin consola, sin `.\`, sin extraer a mano).
Es un **wrapper sobre `install-soul-windows.bat`** ya probado — no reimplementa la lógica.

## Qué hace el .exe
1. Auto-eleva a admin (UAC), sin abrir consola.
2. Extrae el bundle embebido (bat + wsl.sh + roles + `soul_fresh.dump`) a `%TEMP%`.
3. Corre el `.bat`: detecta+instala WSL/Ubuntu, Postgres 17, extensiones, restaura el alma, verifica.
4. **Wipe** del alma en plano de `%TEMP%` al terminar (`deleteafterinstall` + `DelTree`).

## Seguridad (barra FABLE, horneada)
1. **Wipe %TEMP%** — el dump de 617MB en plano se borra al finalizar (además del que borra el `.bat`).
2. **Firma / SmartScreen** — sin firmar, SmartScreen bloquea + red flag. Firmar con `SignTool`
   (descomentar en `[Setup]`); si no hay cert, self-signed + documentar "Más info → Ejecutar de todos modos".
3. **Cero fetch runtime** — todo embebido; el `[Run]` solo invoca el `.bat`. Nada se descarga en runtime.

## Build (en Windows)
Inno Setup 6+ (https://jrsoftware.org/isdl.php). Poner junto al `.iss`:
`install-soul-windows.bat`, `install-soul-wsl.sh`, `soul_roles.sql`, `soul_fresh.dump`.

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" soul-installer.iss
```
Salida: `Output\SOUL-Setup.exe` (~612MB, embebe el alma).

## Límite conocido (v1)
WSL, en su primera instalación, exige **reiniciar Windows** (inherente). Flujo: 1ra corrida instala
WSL + pide reboot → tras reiniciar, doble-click de nuevo al `.exe` → la 2da pasada completa.
**v2**: auto-resume vía `RunOnce` para que sea un único click cruzando el reboot.

## Estado
Diseño listo. **No compilado / no verificado aún.** Compilar en Windows tras cerrar verde el
install en curso; luego FABLE audita el `.exe` por efecto (%TEMP% limpio / firma / sin fetch).
