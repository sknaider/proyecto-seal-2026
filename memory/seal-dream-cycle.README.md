# SOUL Dream Cycle — systemd timers

2 timers que disparan `dream_cycle.py --agent all --cycle <morning|evening>`
desde un único service template (`@.service`).

## Schedule

| Timer | OnCalendar (UTC) | Lima local | Cycle |
|---|---|---|---|
| `seal-dream-cycle-morning.timer` | `12:00` | 07:00 | morning |
| `seal-dream-cycle-evening.timer` | `04:00` (siguiente día) | 23:00 | evening |

> ⚠ Si querés cambiar la hora local: ajustar `OnCalendar` (Lima = UTC−5; sin DST).

## Instalación (requiere sudo)

```bash
sudo install -m 644 \
  /home/dadito/IA/proyecto-seal/memory/seal-dream-cycle@.service \
  /etc/systemd/system/

sudo install -m 644 \
  /home/dadito/IA/proyecto-seal/memory/seal-dream-cycle-morning.timer \
  /home/dadito/IA/proyecto-seal/memory/seal-dream-cycle-evening.timer \
  /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now seal-dream-cycle-morning.timer
sudo systemctl enable --now seal-dream-cycle-evening.timer
```

## Verificar

```bash
# Listar timers activos del dream cycle
systemctl list-timers seal-dream-cycle-*

# Disparar manualmente (sin esperar al cron)
sudo systemctl start seal-dream-cycle@morning.service

# Logs
tail -f /home/dadito/IA/proyecto-seal/messages/dream_cycle.log
journalctl -u 'seal-dream-cycle@*' --since '1 hour ago' -f
```

## Coordinación con `daily_sleep.py`

`daily_sleep.py` (4am Lima) sigue corriendo — produce `session_distill` conciso
y se almacena como `memories.type=milestone`.

`dream_cycle.py` (7am + 11pm Lima) produce narrativas richas + structured
extraction y vive en `soul_v3.daily_dreams` (schema NEXUS S1).

No hay conflicto: distintos horarios, distintos outputs, propósitos distintos.

## Skip condicional

El script chequea `<3 memorias` y `LLM unavailable` y skipea grácilmente
(retorna stats `{skipped: true, reason: ...}` sin error).

## Spec ref

- §5 `spec_seal_companion_master_v1_20260522.md`
- Task SOUL DB #4
- Doc 24 `/agents/ALICE/docs/openhuman_replication/v2/24_intelligence_memory.md`
- Doc 40 (Developer Options → Intelligence)
