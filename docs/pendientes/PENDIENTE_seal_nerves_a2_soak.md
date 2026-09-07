# PENDIENTE DECLARADO — `seal-nerves-a2-soak`

**Estado:** detenido a propósito. `seal-nerves-a2-soak.timer` **parado y deshabilitado**
por NEXUS el 7-sep-2026 14:11 (carril 4). No es una falla a reparar: **le falta una
DECISIÓN, no un arreglo.**

## Qué le falta, medido

```console
tools/nerves_a2_soak_monitor.py            existe (30.561 bytes)
ExecStart ... --release-id ${NERVES_A2_RELEASE_ID}
~/.config/seal/nerves_a2_soak.env          NO existe (se perdio con el home)
research/flywire_results/.../state.json    NO existe
el id en git (40 commits), journal, NFS    NO aparece
```

El monitor **falla cerrado** si el id está vacío (`release_id_required`, línea 641),
que es el comportamiento correcto: un soak de 24 h atado al release equivocado
mide otra cosa y **parece** que mide la buena.

## Por qué no lo repongo yo

El `release_id` **identifica qué release se está sometiendo al soak**. No es un
secreto recuperable ni un valor derivable del código: es una decisión de quien
corre la campaña A2 (ADA / William). Inventar uno produciría una campaña con
evidencia válida sobre un sujeto equivocado — el peor resultado posible, porque
se ve verde.

## Cómo se reactiva, cuando haya decisión

```bash
umask 077
printf 'NERVES_A2_RELEASE_ID=<id de la campana>\n' > ~/.config/seal/nerves_a2_soak.env
systemctl --user enable --now seal-nerves-a2-soak.timer
journalctl --user -u seal-nerves-a2-soak.service -n 20   # debe registrar la muestra
```

**Por qué se detuvo el timer y no se dejó reintentando:** disparaba cada 5 minutos
y fallaba siempre, llenando el journal y el panel de fallas con ruido que tapa
fallas reales. Un pendiente declarado se ve; un servicio en rojo permanente se
vuelve invisible por costumbre.
