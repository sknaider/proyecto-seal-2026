# PENDIENTE DECLARADO — `seal-memory-bundle`

**Qué era, según su propia descripción en el journal (literal):**
*«SEAL — respaldo del repo de memoria FUERA de su propia carpeta»*

**Corrió por última vez el 7-sep a las 00:03:50 y terminó bien — una hora y
treinta y nueve minutos antes de que se borrara el home a la 01:42:53.**

## Por qué no lo reconstruyo, medido

```console
~/.config/systemd/user/seal-memory-bundle.service   NO existe (se perdio)
~/.config/systemd/user/seal-memory-bundle.timer     SI existe, OnCalendar=*-*-* 00:03:00
_CMDLINE en el journal                              solo "/usr/lib/systemd/systemd --user"
                                                    (no preserva el ExecStart del hijo)
*.bundle bajo /home y /mnt (desde el 1-sep)         NINGUNO
el directorio de memoria, hoy                       NO es un repo git
```

**Tres cosas faltan a la vez:** el comando, el sujeto (qué repo bundleaba) y la
salida (dónde escribía). Reconstruirlo a ojo produciría **un respaldo que
parece andar y respalda otra cosa** — el peor resultado para una pieza cuyo
único trabajo es existir el día que todo se pierde.

## Lo que sí queda establecido, y es lo importante

**El respaldo del repo de memoria corrió esa madrugada y su salida no aparece
en ninguna parte.** O escribía dentro del home —y se borró con él, lo que
contradice su propia descripción— o iba a un destino que hoy no existe. **Las
dos posibilidades son un hallazgo, no un detalle de configuración.**

## Qué hace falta para cerrarlo

Una decisión, no una medición: **qué se respalda (el directorio de memoria de
Claude, el repo, ambos) y a dónde**, con el destino FUERA de `/home`. Es de
quien defina la política de respaldo tras el incidente.

Emparenta con el pendiente de [`seal-nerves-a2-soak`](PENDIENTE_seal_nerves_a2_soak.md):
en los dos, inventar el dato faltante da verde y miente.
