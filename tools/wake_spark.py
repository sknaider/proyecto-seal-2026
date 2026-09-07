#!/usr/bin/env python3
"""wake_spark.py — despierta un DGX Spark apagado por Wake-on-LAN (magic packet).

CONTRATO (para el tablero de FABLE — hand-off JARVIS↔FABLE):
    python3 wake_spark.py <spark-2|spark-3|spark-4>
    → envía el magic packet WoL a la MAC WIRED de ese Spark.
    → exit 0 = paquete enviado (idempotente: mandarlo a una máquina YA prendida es inofensivo).
    → exit 2 = MAC no configurada todavía (los Spark estaban apagados cuando se creó esto).
    → exit 3 = nombre de Spark desconocido.

WoL puro en Python (socket UDP broadcast al puerto 9) — SIN dependencia de `wakeonlan`/apt.

REQUISITOS para que funcione de verdad (se configuran con los Spark ENCENDIDOS — PENDIENTE):
  1. WoL habilitado en BIOS/UEFI de cada Spark + en la NIC (`ethtool -s <iface> wol g`).
  2. Conexión por CABLE Ethernet (WoL sobre WiFi no es confiable — dato de ALICE).
  3. Rellenar las MAC WIRED reales abajo (hoy son placeholders; se obtienen con
     `ip link show <iface>` en cada Spark cuando esté encendido).
"""
import socket
import sys

# MAC WIRED de cada Spark (Ethernet, NO WiFi). PLACEHOLDERS hasta el próximo encendido físico.
# Reemplazar por las reales: en cada Spark ON → `ip link show` (la del iface eth cableado).
# ⚠️ WoL NO FUNCIONAL (16-jul): los 3 Spark tienen la IP 192.168.68.x en WiFi (wlP9s9).
# Estas MACs (capturadas por ARP) son de WiFi, y `ethtool wlP9s9` no soporta Wake-on
# (NEXUS verificó por SSH). WoL sobre WiFi no anda. Para habilitar wake remoto: conectar
# los Spark por CABLE Ethernet, capturar la MAC WIRED, y `ethtool <eth> wol g`. Hasta
# entonces, encendido FÍSICO. MACs WiFi abajo solo como registro (NO despiertan la máquina).
SPARK_MACS = {
    "spark-2": "f8:3d:c6:b7:31:44",  # .70 — MAC WiFi (wlP9s9), NO sirve para WoL
    "spark-3": "f8:3d:c6:af:da:bc",  # .71 — MAC WiFi (wlP9s9), NO sirve para WoL
    "spark-4": "f8:3d:c6:b7:32:08",  # .72 — MAC WiFi (wlP9s9), NO sirve para WoL
}

# Broadcast de la LAN (los Spark viven en 192.168.68.0/24).
BROADCAST_IP = "192.168.68.255"
WOL_PORT = 9


def _magic_packet(mac: str) -> bytes:
    """6 bytes 0xFF + la MAC repetida 16 veces = magic packet WoL."""
    clean = mac.replace(":", "").replace("-", "").strip()
    if len(clean) != 12:
        raise ValueError(f"MAC inválida: {mac!r}")
    mac_bytes = bytes.fromhex(clean)
    return b"\xff" * 6 + mac_bytes * 16


def wake(name: str) -> int:
    mac = SPARK_MACS.get(name)
    if mac is None:
        print(f"[wake_spark] Spark desconocido: {name!r}. Opciones: {', '.join(SPARK_MACS)}")
        return 3
    if set(mac.replace(":", "")) <= {"0"}:
        print(f"[wake_spark] MAC de {name} aún no configurada (placeholder). "
              f"Rellenar SPARK_MACS con la MAC wired real (Spark encendido → 'ip link show').")
        return 2
    packet = _magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, (BROADCAST_IP, WOL_PORT))
    print(f"[wake_spark] magic packet enviado a {name} ({mac}) via {BROADCAST_IP}:{WOL_PORT}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(wake(sys.argv[1]))
