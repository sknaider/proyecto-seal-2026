# DGX Spark 4-Node Cluster — Setup Documentation v1

**Date**: 2026-05-04
**Author**: JARVIS (Claude Sonnet/Opus 4.7)
**For**: William Henry Tovar Urquia
**Status**: ✅ Cluster Ray operativo (4 nodos, 80 CPU, 4 GPU GB10, 447.51 GB unified pool)

---

## 1. Topología Física

**4 DGX Sparks en RING TOPOLOGY**, conectados con cables QSFP56 200GbE direct (sin switch):

```
spark-1 ──cable A── spark-2 ──cable B── spark-3 ──cable C── spark-4 ──cable D── spark-1
```

Cada cable QSFP56 contiene **2 PCIe x4 internos** que aparecen como **2 net devices distintos** (twin pair) en cada extremo.

**Hostnames**:
| Spark | Hostname | User | WiFi IP | Rol |
|-------|----------|------|---------|-----|
| spark-1 | spark-2cdf | dadito | 192.168.68.69 | HEAD |
| spark-2 | spark-2 | nombre | 192.168.68.70 | Worker |
| spark-3 | spark-3 | nombre | 192.168.68.71 | Worker |
| spark-4 | spark-4 | nombre | 192.168.68.72 | Worker |

---

## 2. IPs RoCE 200G (8 subnets /29 anillo)

**Regla NCCL crítica**: nunca compartir subnet entre los 2 twins de un mismo cable.

| Cable | Vecinos | Twin 1 (subnet) | Twin 2 (subnet) |
|-------|---------|-----------------|-----------------|
| A | spark-1 ↔ spark-2 | 172.31.5.0/29 | 172.31.5.8/29 |
| B | spark-2 ↔ spark-3 | 172.31.6.0/29 | 172.31.6.8/29 |
| C | spark-3 ↔ spark-4 | 172.31.7.0/29 | 172.31.7.8/29 |
| D | spark-4 ↔ spark-1 | 172.31.8.0/29 | 172.31.8.8/29 |

### Asignación detallada por nodo

**spark-1** (HEAD, hostname=spark-2cdf):
- `enp1s0f1np1`: 172.31.5.1/29 (cable A twin1, → spark-2)
- `enP2p1s0f1np1`: 172.31.5.9/29 (cable A twin2, → spark-2)
- `enp1s0f0np0`: 172.31.8.2/29 (cable D twin1, ← spark-4)
- `enP2p1s0f0np0`: 172.31.8.10/29 (cable D twin2, ← spark-4)

**spark-2**:
- `enp1s0f0np0`: 172.31.5.2/29 (cable A twin1, ← spark-1)
- `enP2p1s0f0np0`: 172.31.5.10/29 (cable A twin2, ← spark-1)
- `enp1s0f1np1`: 172.31.6.1/29 (cable B twin1, → spark-3)
- `enP2p1s0f1np1`: 172.31.6.9/29 (cable B twin2, → spark-3)

**spark-3**:
- `enp1s0f0np0`: 172.31.6.2/29 (cable B twin1, ← spark-2)
- `enP2p1s0f0np0`: 172.31.6.10/29 (cable B twin2, ← spark-2)
- `enp1s0f1np1`: 172.31.7.1/29 (cable C twin1, → spark-4)
- `enP2p1s0f1np1`: 172.31.7.9/29 (cable C twin2, → spark-4)

**spark-4**:
- `enp1s0f0np0`: 172.31.7.2/29 (cable C twin1, ← spark-3)
- `enP2p1s0f0np0`: 172.31.7.10/29 (cable C twin2, ← spark-3)
- `enp1s0f1np1`: 172.31.8.1/29 (cable D twin1, → spark-1)
- `enP2p1s0f1np1`: 172.31.8.9/29 (cable D twin2, → spark-1)

**MTU**: 9000 (jumbo frames) en todas las NICs RoCE.

---

## 3. Routing (cada nodo conoce camino a subnets no-vecinas)

**spark-1**: rutas a subnets 6 y 7 vía spark-2 (172.31.5.2)
```bash
ip route add 172.31.6.0/29 via 172.31.5.2
ip route add 172.31.6.8/29 via 172.31.5.2
ip route add 172.31.7.0/29 via 172.31.5.2
ip route add 172.31.7.8/29 via 172.31.5.2
```

**spark-2**: rutas a subnets 7 (vía spark-3) y 8 (vía spark-1)
```bash
ip route add 172.31.7.0/29 via 172.31.6.2
ip route add 172.31.7.8/29 via 172.31.6.2
ip route add 172.31.8.0/29 via 172.31.5.1
ip route add 172.31.8.8/29 via 172.31.5.1
```

**spark-3**: rutas a subnets 5 (vía spark-2) y 8 (vía spark-4)
```bash
ip route add 172.31.5.0/29 via 172.31.6.1
ip route add 172.31.5.8/29 via 172.31.6.1
ip route add 172.31.8.0/29 via 172.31.7.2
ip route add 172.31.8.8/29 via 172.31.7.2
```

**spark-4**: rutas a subnets 5 (vía spark-1) y 6 (vía spark-3)
```bash
ip route add 172.31.5.0/29 via 172.31.8.2
ip route add 172.31.5.8/29 via 172.31.8.2
ip route add 172.31.6.0/29 via 172.31.7.1
ip route add 172.31.6.8/29 via 172.31.7.1
```

**IP Forwarding** habilitado en TODOS los nodos:
```bash
sysctl -w net.ipv4.ip_forward=1
```

---

## 4. Persistencia (sobrevive reboot)

### 4.1 IPs WiFi fijas (NetworkManager)

```bash
nmcli connection modify 'RedDady_MLO' \
  ipv4.method manual \
  ipv4.addresses 192.168.68.X/22 \
  ipv4.gateway 192.168.68.1 \
  ipv4.dns '192.168.68.1 8.8.8.8' \
  connection.autoconnect yes
```
Donde X = 70/71/72 según el spark. spark-1 usa `RedDady_6GHz` con IP .69.

### 4.2 IPs RoCE + MTU + rutas (netplan)

Archivo `/etc/netplan/99-roce-persist.yaml` en cada nodo (contenido específico por nodo, ver sección 2 y 3).

Ejemplo spark-1:
```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    enp1s0f1np1:
      mtu: 9000
      addresses: [172.31.5.1/29]
    enP2p1s0f1np1:
      mtu: 9000
      addresses: [172.31.5.9/29]
    enp1s0f0np0:
      mtu: 9000
      addresses: [172.31.8.2/29]
      routes:
        - {to: 172.31.6.0/29, via: 172.31.5.2}
        - {to: 172.31.6.8/29, via: 172.31.5.2}
        - {to: 172.31.7.0/29, via: 172.31.5.2}
        - {to: 172.31.7.8/29, via: 172.31.5.2}
    enP2p1s0f0np0:
      mtu: 9000
      addresses: [172.31.8.10/29]
```

### 4.3 IP forwarding persistente

Archivo `/etc/sysctl.d/99-ip-forward.conf` en los 4 nodos:
```
net.ipv4.ip_forward = 1
```

### 4.4 Linger (workers screens persisten al cerrar SSH)

```bash
sudo loginctl enable-linger nombre  # workers
sudo loginctl enable-linger dadito  # spark-1
```

### 4.5 Sudo NOPASSWD (spark-1 ya en workers)

```bash
echo "dadito ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/90-dadito-nopasswd
sudo chmod 440 /etc/sudoers.d/90-dadito-nopasswd
```

---

## 5. SSH config

`/home/dadito/.ssh/config` en spark-1:
```
Host spark-2
  HostName 192.168.68.70
  User nombre
  IdentityFile ~/.ssh/id_rsa
Host spark-3
  HostName 192.168.68.71
  User nombre
  IdentityFile ~/.ssh/id_rsa
Host spark-4
  HostName 192.168.68.72
  User nombre
  IdentityFile ~/.ssh/id_rsa
```

Pubkey instalada en `~/.ssh/authorized_keys` de cada worker (user=nombre).

---

## 6. NFS shared folders

**Server (spark-1)**: `/etc/exports`
```
/home/dadito/IA/modelos 192.168.68.0/22(rw,sync,no_subtree_check,no_root_squash)
/home/dadito/IA/proyecto-seal 192.168.68.0/22(rw,sync,no_subtree_check,no_root_squash)
```

**Clients (workers)**: en `/etc/fstab`:
```
192.168.68.69:/home/dadito/IA/modelos /mnt/spark-1/modelos nfs defaults,_netdev,nofail,x-systemd.automount 0 0
192.168.68.69:/home/dadito/IA/proyecto-seal /mnt/spark-1/proyecto-seal nfs defaults,_netdev,nofail,x-systemd.automount 0 0
```

Symlinks en cada worker (para que vLLM encuentre el modelo en /root/.cache/huggingface dentro del container):
```bash
ln -sfn /mnt/spark-1/modelos/GLM-5.1-FP8 /home/nombre/.cache/huggingface/GLM-5.1-FP8
```

---

## 7. Scripts canónicos

### 7.1 start-ray.sh (oshlabs original, una sola modificación: screen -dmS)

`/home/{dadito,nombre}/start-ray.sh` (idéntico en los 4 nodos):
```bash
#!/usr/bin/env bash
set -euo pipefail

HEAD_IP=172.31.5.1
MN_IF_NAME=enp1s0f0np0
VLLM_IMAGE=nvcr.io/nvidia/vllm:26.03.post1-py3

VLLM_HOST_IP=$(ip -4 addr show "$MN_IF_NAME" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')

if ip -4 addr show | grep -q "inet $HEAD_IP/"; then
    ROLE=head
else
    ROLE=worker
fi

if [[ -z "${RAY_INNER:-}" ]]; then
    echo "Detected role: $ROLE (this node $VLLM_HOST_IP, head $HEAD_IP)"
    exec env RAY_INNER=1 screen -dmS "ray-$ROLE" bash "$0"
fi

exec bash ~/run_cluster.sh "$VLLM_IMAGE" "$HEAD_IP" "--$ROLE" ~/.cache/huggingface \
  --device=/dev/infiniband \
  --cap-add=IPC_LOCK \
  --ulimit memlock=-1:-1 \
  -e NCCL_IB_DISABLE=0 \
  -e NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0 \
  -e NCCL_IB_GID_INDEX=3 \
  -e VLLM_HOST_IP="$VLLM_HOST_IP" \
  -e UCX_NET_DEVICES="$MN_IF_NAME" \
  -e NCCL_SOCKET_IFNAME="$MN_IF_NAME" \
  -e OMPI_MCA_btl_tcp_if_include="$MN_IF_NAME" \
  -e GLOO_SOCKET_IFNAME="$MN_IF_NAME" \
  -e TP_SOCKET_IFNAME="$MN_IF_NAME" \
  -e RAY_memory_monitor_refresh_ms=0 \
  -e MASTER_ADDR="$HEAD_IP"
```

### 7.2 run_cluster.sh (vllm-project original)

Pull desde:
```
https://raw.githubusercontent.com/vllm-project/vllm/refs/heads/main/examples/online_serving/run_cluster.sh
```

NO modificar — el `trap cleanup EXIT` es necesario para limpieza de containers al terminar.

---

## 8. Auto-start en boot (systemd unit)

`/etc/systemd/system/ray-cluster.service` en cada nodo (user difiere: `dadito` en spark-1, `nombre` en workers):

```ini
[Unit]
Description=Ray cluster auto-start (DGX Spark)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
User=nombre              # o "dadito" en spark-1
WorkingDirectory=/home/nombre
ExecStartPre=/bin/sleep 15        # espera a que el HEAD arranque primero
ExecStart=/bin/bash /home/nombre/start-ray.sh
ExecStop=/bin/bash -c 'docker stop ray-head ray-worker 2>/dev/null; true'
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ray-cluster.service
```

---

## 9. Lanzar el cluster (manual)

Después de configurar IPs RoCE + rutas:

**1. En spark-1 (HEAD)**:
```bash
bash ~/start-ray.sh
```

**2. En cada worker**:
```bash
bash ~/start-ray.sh
```

**3. Verificar (en spark-1)**:
```bash
HEAD_CID=$(docker ps -q --filter 'name=node-' | head -1)
docker exec $HEAD_CID ray status
```

Salida esperada:
```
Active:
 1 node_<id1>
 1 node_<id2>
 1 node_<id3>
 1 node_<id4>
Resources:
 0.0/80.0 CPU
 0.0/4.0 GPU
 0B/447.51GiB memory
 0B/38.91GiB object_store_memory
```

---

## 10. vLLM serve con TP=4 (cuando modelo descargado)

Lanzar desde dentro del container HEAD:
```bash
HEAD_CID=$(docker ps -q --filter 'name=node-' | head -1)
docker exec -d $HEAD_CID vllm serve /root/.cache/huggingface/GLM-5.1-FP8 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --distributed-executor-backend ray \
  --max-model-len 32768 \
  --host 0.0.0.0 --port 8000
```

**Test inferencia**:
```bash
curl http://172.31.5.1:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"/root/.cache/huggingface/GLM-5.1-FP8","prompt":"Hello","max_tokens":50}'
```

---

## 11. NCCL bench (validación bandwidth real)

Script en `~/.cache/huggingface/nccl_bench.py` (en cada nodo).

Lanzar (ejemplo 2 nodos, escalable a 4):
```bash
# En worker (spark-2):
docker exec -e NCCL_DEBUG=INFO ray-worker torchrun \
  --nnodes=2 --nproc_per_node=1 --node_rank=1 \
  --master_addr=172.31.5.1 --master_port=29500 \
  /root/.cache/huggingface/nccl_bench.py

# En HEAD (spark-1):
docker exec -e NCCL_DEBUG=INFO ray-head torchrun \
  --nnodes=2 --nproc_per_node=1 --node_rank=0 \
  --master_addr=172.31.5.1 --master_port=29500 \
  /root/.cache/huggingface/nccl_bench.py
```

Salida esperada (RoCE OK): busbw > 100 GB/s para mensajes >= 256MB.

---

## 12. Lecciones aprendidas (DEBUGGING TIME LOG)

### ❌ Cosas que NO funcionaron y por qué

1. **Bridge L2 plano + STP en cableado punto-a-punto**: STP detecta cada cable como segmento L2 separado y bloquea redundancia → islas aisladas. NO sirve para mesh sin switch L2 real.

2. **Reescribir start-ray.sh con detección por hostname**: No mata el cluster, pero introduce bugs subtles. **Usar el blueprint canónico oshlabs.**

3. **NCCL_SOCKET_IFNAME con las 4 NICs**: confunde el discovery. Usar SOLO la interfaz primaria (`MN_IF_NAME`).

4. **NCCL_IB_HCA con los 4 HCAs**: usar SOLO los 2 primarios (`rocep1s0f0,roceP2p1s0f0`). NCCL detecta los twins automáticamente.

5. **Compartir subnet entre twins de un mismo cable**: rompe NCCL. Cada twin debe estar en su propia /29.

6. **`docker run -d` sin `--restart`**: workers efímeros si bash muere. **Mejor**: usar el script con screen+trap como en oshlabs.

7. **Workers sin linger**: sessions mueren al cerrar SSH → containers Docker mueren con trap EXIT del bash. **Fix**: `loginctl enable-linger nombre` en cada worker.

8. **Containers stuck en estado "Created"**: cuelgan el docker daemon. Solución: `sudo systemctl restart docker` + remover containers viejos.

9. **WebFetch/WebSearch broken**: usar `curl --max-time 10` directo a `raw.githubusercontent.com/...` para fetcher blueprints externos.

### ✅ Cosas que SÍ funcionaron (replicar)

1. NetworkManager autoconnect para WiFi fija (sobrevive reboot perfectamente).
2. netplan con `addresses + routes` para IPs RoCE (sobrevive reboot).
3. NFS v4.2 sobre WiFi para distribuir modelos (rendimiento aceptable para load inicial).
4. `_netdev,nofail,x-systemd.automount` en /etc/fstab para NFS mount lazy.
5. Blueprint canónico oshlabs **SIN modificar** (start-ray.sh + run_cluster.sh originales).
6. Symlinks `~/.cache/huggingface/<model>` → `/mnt/spark-1/modelos/<model>` para que vLLM encuentre el modelo via NFS.

---

## 13. Referencias

- **NVIDIA oficial 2-spark playbook**: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-two-sparks
- **NVIDIA oficial 3-spark RING playbook**: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-three-sparks
- **NVIDIA oficial multi-spark con switch**: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/multi-sparks-through-switch
- **Community blueprint (oshlabs)**: https://github.com/oshlabs/dgx-spark-cluster
- **NVIDIA build.nvidia.com**: https://build.nvidia.com/spark/vllm/stacked-sparks
- **Forum NVIDIA 4-node sin switch**: https://forums.developer.nvidia.com/t/368726
- **vLLM run_cluster.sh upstream**: https://raw.githubusercontent.com/vllm-project/vllm/refs/heads/main/examples/online_serving/run_cluster.sh

> ⚠️ **IMPORTANTE**: NVIDIA oficialmente solo soporta hasta 3 sparks (ring) o N sparks con switch. **4 sparks ring sin switch es OFF-LABEL** — funciona pero requiere routing manual (sección 3) y no tiene soporte oficial NVIDIA.

---

## 14. Estado actual al cierre de esta documentación

- ✅ **Cluster 4-nodos**: 80 CPU, 4 GPU GB10, 447.51 GB unified pool
- ✅ **Persistencia**: netplan + sysctl + fstab + NetworkManager autoconnect + systemd unit
- ✅ **NFS**: spark-1 exporta a workers vía 192.168.68.0/22
- ✅ **SSH**: passwordless con id_rsa, sudo NOPASSWD en todos
- ✅ **Linger**: enabled en los 4 users
- 📥 **Descarga GLM-5.1-FP8**: 124/151 archivos, 640GB downloaded (a ~110 MB/s, ETA ~5 min más)
- ⏳ **vLLM serve**: pendiente cuando descarga complete
- ⏳ **NCCL bench**: ejecutar después de validar vLLM serve
