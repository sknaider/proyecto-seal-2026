# Autostart blindado del robot CapyTown

Recupera y protege el arranque automatico de los procesos que borraron en el robot publico:
**/scan (LiDAR), /odom y los servos (motores)**. Corre en **bash** (no lxterminal) como
servicio systemd: arranca al boot, se reinicia solo si se cae, y sobrevive reinicios.

## Antes de instalar: consegui el comando del bringup

En el Pi, corre esto para descubrir que lanzaba esos nodos (aunque el archivo este borrado):

```bash
grep -rn "launch\|bringup\|ros2" ~/.config/autostart/ ~/.bashrc ~/.profile /etc/rc.local 2>/dev/null
crontab -l 2>/dev/null
history | grep -i "launch\|bringup" | tail -20
```

Copia el comando exacto (ej. `ros2 launch yahboomcar_bringup bringup.launch.py`) y pegalo en
la linea `BRINGUP_CMD` de `start_bringup.sh`.

## Instalar

```bash
chmod +x *.sh
./install_autostart.sh          # instala + habilita al boot
sudo systemctl start capytown-bringup   # arranca ahora sin reiniciar
```

## Verificar por efecto

```bash
systemctl status capytown-bringup           # active (running)
journalctl -u capytown-bringup -f           # log del bringup
ros2 topic list | grep -E '/scan|/odom'     # deben aparecer
```

## Si alguien lo vuelve a borrar

```bash
./restore_autostart.sh          # lo repone en ~10 segundos
```

Guarda este kit tambien en un USB o carpeta segura fuera del robot, para poder restaurar
aunque borren la copia del Pi.

## Que edita cada archivo
- `start_bringup.sh` — **edita `BRINGUP_CMD`** (y el ROS_DOMAIN_ID / distro si aplica).
- `capytown-bringup.service` — el instalador ya ajusta User y ruta solo.
- `install_autostart.sh` / `restore_autostart.sh` — no tocar, se usan tal cual.
