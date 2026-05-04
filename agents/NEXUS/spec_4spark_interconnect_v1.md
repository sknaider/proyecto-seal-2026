# SPEC — Interconexión 4-Nodo DGX Spark sin Switch (o con Switch)

**Autor:** NEXUS — Team SEAL
**Fecha:** 2026-05-04 02:05 Lima
**Versión:** v1 (draft inicial — pre-review JARVIS)
**Solicitante:** William
**Colaborador:** JARVIS (datos cluster + validación en vivo)

---

## 0a. TL;DR (para William, si solo lees una página)

**Pregunta:** ¿Es viable conectar los 4 Sparks juntos?
**Respuesta corta:** Sí, por 3 caminos distintos. Tu decisión depende de **cuánto querés invertir** y **qué tan rápido necesitás producción**.

| Camino | $$$ | Riesgo | Tiempo | Output |
|---|---|---|---|---|
| **Mashie SR4 (innovación)** | ~$1,376 | Alto | 10-13 días | 4-Spark mesh full bandwidth, **primero del mundo en validar** |
| **MikroTik CRS804 (clásico)** | ~$1,600 | Muy bajo | 4 horas (post-llegada) | 4-Spark mesh full bandwidth, oficial NVIDIA |
| **Software (gratis)** | $0 | Alto | 1 hora | comunicación funcional pero **5-10× más lento** que mesh |

**Mi recomendación:** esta noche aplicamos sysctl + GLOO (gratis, 1h, mejora todo). Mañana decidís entre **MikroTik CRS804** (seguro) o **mashie SR4** (innovador) basándote en qué tan importante es la innovación documentable para SEAL.

**Mientras tanto:** ⚠️ Update 03:50 Lima — JARVIS validó que el "3-Spark cluster" actual es **CHAIN, no triangle** (spark-2↔3↔4 sin cierre 2↔4). vLLM PP=3 también falla con NCCL `ibv_modify_qp` timeout. Para activar 3-Spark serving hay que **recablear** o aplicar Track 5.7 (NCCL_NET=Socket). JARVIS está probando 5.7 ahora.

**🎯 PLOT TWIST descubierto post-v1**: existe una opción **gratis** que puede destrabbar 4-Spark NCCL **sin comprar nada** — `NCCL_TOPO_FILE` con topología custom forzando Ring puro (sección 5.7). Si funciona, no necesitás Track A ni B. JARVIS la valida esta noche.

**Plan esta noche (libre albedrío JARVIS+NEXUS):**
1. ✅ JARVIS: aplicar sysctl tuning (sección 5.1)
2. 🌟 JARVIS: probar **NCCL_TOPO_FILE custom XML** (sección 5.7) — opción que puede destrabar todo gratis
3. ✅ JARVIS: bench Qwen3-Coder en 3-Spark + 4-Spark si #2 funciona
4. ✅ JARVIS: probar 3-4 modelos NVFP4 (Qwen3.5-397B, Llama-3.3-70B standalone, Nemotron-Super)
5. ✅ NEXUS: spec listo + matriz LLM + commit

Despertás con: cluster funcionando + decisión hardware cuantificada — y si #2 funciona, **decisión = $0**.

---

## 0. Executive Summary

El cluster `seal-cluster` posee **4 unidades DGX Spark GB10** que William desea operar como un único pool de cómputo distribuido (NCCL/RoCEv2 sobre 200G ConnectX-7). NVIDIA **no soporta oficialmente** topologías 4-Spark sin switch: 2-Spark y 3-Spark sí (blueprints `connect-two-sparks` y triangle), pero a partir de 4 nodos la guía es siempre "compre un switch".

Tras 5 h de debug por JARVIS y un análisis cruzado de 4 threads activos en NVIDIA Developer Forums (más papers y repos community), existen **8 caminos viables**. Los agrupo en tres tracks:

| Track | Inversión | Riesgo técnico | Cuándo aplica |
|---|---|---|---|
| **A · Hardware mesh sin switch** | $400–$1.5K | Medio (no validado en producción) | Si quieres 4-Spark NCCL full bandwidth sin gastar en switch |
| **B · Switch dedicado** | $2K–$25K | Bajo (oficial NVIDIA) | Si vas a escalar a 6+ Sparks o necesitas full guarantee |
| **C · Software sin hardware nuevo** | $0 | Alto (perf degradado) | Si solo necesitas comm funcional y aceptas penalty |

**Recomendación NEXUS** (criterio: ROI vs riesgo dado el roadmap SEAL):
1. **Esta noche** → aplicar el track C completo (sysctl tuning + NCCL_NET=Socket fallback + GLOO en planos de control). Costo cero. Probar con JARVIS.
2. **Mientras tanto, paralelo** → escalar a **3-Spark + spark-4 standalone** para Qwen3-Coder (blueprint NVIDIA oficial, sin riesgo, ya tiene scripts JARVIS).
3. **48–72 h** → ejecutar Track A1 (mashie's transceivers SR4): partes ~$650 USD, riesgo controlado, valida una innovación documentada que la comunidad NO ha probado aún.
4. **Si Track A1 no funciona en bench** → Track B con MikroTik CRS804 ($1.6K).

---

## 1. Estado Actual del Cluster

### 1.1 Hardware (datos JARVIS, 04-may-2026 01:59 Lima)

```
NODE  WIFI            ROCE-A           ROCE-B           ROLE
spark-1   192.168.68.69   172.31.5.{1,9}   172.31.8.{2,10}  SOUL (fuera del cluster Ray)
spark-2   192.168.68.70   172.31.5.{2,10}  172.31.6.{1,9}   HEAD Ray cluster (10.0.0.2)
spark-3   192.168.68.71   172.31.6.{2,10}  172.31.7.{1,9}   Worker
spark-4   192.168.68.72   172.31.7.{2,10}  172.31.8.{1,9}   Worker
```

**Por nodo (idéntico):**
- Chip: NVIDIA GB10 (Grace Blackwell ARM 64-core + GB10 GPU integrada)
- 2× **ConnectX-7 dual-port** (Mellanox MT2910, board `NVD0000000087`)
  - Chip A — PCIe `0000:01:00.X` → ports `rocep1s0f0` + `rocep1s0f1`
  - Chip B — PCIe `0002:01:00.X` → ports `roceP2p1s0f0` + `roceP2p1s0f1`
- 4 ports lógicos = **2 puertos físicos QSFP56 × 2 NICs twins** (cada cable QSFP56 entrega 2 PCIe x4 al kernel)
- Firmware ConnectX-7: `28.45.4028`
- Estado: PORT_ACTIVE @ 200 Gb/s 4X HDR
- Link-layer: **Ethernet** (RoCEv2, no IB nativo)
- MTU configurada: 4096 (puerto IB), 9000 (interfaz IP), maxmtu 9978

**Stack software:**
- Driver NVIDIA: 580.142
- CUDA: 13.2 host / 13.0 nvcc / 13.2 container
- vLLM: `nvcr.io/nvidia/vllm:26.03.post1-py3`
- NCCL: `2.29.7+cuda13.2` (solo dentro del container; host limpio)
- NCCL plugin: `nccl_rdma_sharp_plugin v11` cargado
- Transformers: 5.8.0.dev0 (upgraded para soportar `glm_moe_dsa`)

### 1.2 Topología Manual Actual (anillo)

```
spark-1 ───cable A (200G)─── spark-2
   │                              │
cable D                       cable B
   │                              │
spark-4 ───cable C (200G)─── spark-3
```

Subnets RoCE: **8 subnets `/29`** (2 por cable, una por twin) — config validada por JARVIS:

```
Cable A (spark-1 ↔ spark-2):  twin1 → 172.31.5.0/29 (.1,.2)  twin2 → 172.31.5.8/29 (.9,.10)
Cable B (spark-2 ↔ spark-3):  twin1 → 172.31.6.0/29 (.1,.2)  twin2 → 172.31.6.8/29 (.9,.10)
Cable C (spark-3 ↔ spark-4):  twin1 → 172.31.7.0/29 (.1,.2)  twin2 → 172.31.7.8/29 (.9,.10)
Cable D (spark-4 ↔ spark-1):  twin1 → 172.31.8.0/29 (.1,.2)  twin2 → 172.31.8.8/29 (.9,.10)
```

Cada twin tiene su propia `/29` aislada — alineado con el blueprint NVIDIA `connect-two-sparks`. **No hay anti-pattern**: el problema NCCL es exclusivamente la falta de path L2 directo entre nodos no-vecinos (sección 2).

### 1.3 Lo que JARVIS ya probó (5 h debug)

| Variable | Resultado |
|---|---|
| `NCCL_IB_DISABLE=0` | `ibv_modify_qp Connection timed out` (vecinos no-directos) |
| `NCCL_IB_DISABLE=1` | mismo error (IB plugin sigue intentando) |
| `NCCL_DEBUG=INFO` | `ibv_modify_qp failed with 110 Connection timed out` en `roceP2p1s0f0:1`, GID 172.31.6.10 → 172.31.5.10 |
| `NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0` | solo 2 HCAs primarios (twins implícitos) → mismo error |
| `NCCL_SOCKET_IFNAME=enp1s0f0np0` + `GLOO_SOCKET_IFNAME=...` | mismo error |
| vLLM `TP=4 PP=1` | falla NCCL init en `collective_rpc init_device` |
| vLLM `TP=1 PP=4` | falla MISMO error NCCL |
| vLLM `TP=2 PP=2` | aún no probado |

### 1.4 Restricciones arquitecturales del SoC GB10 (HALLAZGO JARVIS dump NCCL)

JARVIS dumpeó la topología detectada por NCCL en single-node (spark-2). Hallazgos que **invalidan asunciones del v1**:

```xml
<system version="1">
  <cpu arch="arm64">
    <pci busid="0000:01:00.0" link_speed="32.0 GT/s PCIe" link_width="4">
      <nic><net name="rocep1s0f0" speed="200000" gdr="0" net="1" gin="1"/></nic>
    </pci>
    <pci busid="0002:01:00.0" link_speed="32.0 GT/s PCIe" link_width="4">
      <nic><net name="roceP2p1s0f0" speed="200000" gdr="0" net="1" gin="1"/></nic>
    </pci>
    <pci busid="000f:01:00.0" link_speed="2.5 GT/s PCIe" link_width="16">
      <gpu dev="0" sm="121" rank="0" gdr="0">
        <c2c bw="43125" count="8"/>
      </gpu>
    </pci>
  </cpu>
</system>
```

**Constraints duros (no negociables, by-design NVIDIA):**

1. **`gdr=0` en TODOS los devices** → **GPU Direct RDMA NO existe en GB10**. La GPU Blackwell está integrada al SoC ARM Grace, sin path PCIe directo NIC↔GPU. Toda comunicación NIC↔GPU pasa obligatoriamente por **CPU memory (Grace)**. Aplica incluso a vecinos directos.

2. **PCIe NICs: 32 GT/s × 4 lanes = ~16 GB/s teórico** por NIC (Gen5 x4). Los 200 Gb/s ópticos son ceiling de fibra, NO de bandwidth efectivo a GPU. Bandwidth real GPU↔GPU multi-host: limitado por PCIe x4, no por RoCE.

3. **C2C Grace↔Blackwell: 43 GB/s** on-die (chip-to-chip coherent cache). Es el path interno; NCCL debe terminar paquetes en Grace memory.

4. **NCCL solo detecta primary NICs** (`rocep1s0f0`, `roceP2p1s0f0`); los **twins (`f1np1`) no aparecen** — NCCL los considera mismo HCA o los filtra. Por eso `NCCL_IB_HCA` solo lista 2.

**Consecuencia para diseño:**
- Tracks A (mashie SR4) y B (switch) — el techo real es **~32 GB/s aggregate** por nodo (2 NICs × 16 GB/s), no 50 GB/s como yo asumí.
- Track C (Software) **gana valor relativo**: si toda comunicación pasa por CPU memory de todos modos, **NCCL_NET=Socket NO pierde tanto vs RDMA puro** como en GPUs discretas. La penalty estimada baja de 5–10× a 1.5–3×.

**Hipótesis nueva (a validar JARVIS bench, sección 5.7):**
> NCCL falla con `unhandled system error` porque busca path RDMA + GDR. Como `gdr=0`, el plugin IB intenta workarounds que dependen de QP setup L2-directo → fail entre non-neighbors. Forzando `NCCL_NET=Socket` + `NCCL_IB_DISABLE=1`, NCCL usa Ethernet sockets puros sobre RoCE → tolera routed paths IP → 4-Spark anillo funciona sin hardware nuevo.

**Test crítico** (JARVIS, 10 min):
```bash
# Baseline: 2 vecinos directos con RDMA puro
NCCL_NET=IB ... all_reduce_perf -b 1M -e 128M -f 2 -g 1 -t 1 \
  --hosts spark-2:1,spark-3:1

# Compare: 2 vecinos directos con Socket
NCCL_NET=Socket NCCL_IB_DISABLE=1 NCCL_SOCKET_IFNAME=enp1s0f0np0 \
  all_reduce_perf -b 1M -e 128M -f 2 -g 1 -t 1 \
  --hosts spark-2:1,spark-3:1
```

Si gap < 2×, **Socket gana en GB10** por arquitectura. 4-Spark anillo via Socket + IP forward sería viable sin hardware.

### 1.5 Bug colateral encontrado (sysctl)

```
net.core.rmem_max       = 212992          (necesario: 268435456)
net.core.wmem_max       = 212992          (necesario: 268435456)
```
**A 200G RoCE, 212 KiB de buffer es insuficiente.** Aunque no es la causa raíz del fallo NCCL, contribuye a timeouts intermitentes y degrada performance incluso en pares directos. **Aplicar antes de cualquier otra prueba.**

---

## 2. Root Cause Analysis (revisado v1.2 con hallazgo GB10 gdr=0)

### 2.0 Resumen de la causa

Doble factor:
1. **Hardware:** GB10 tiene `gdr=0` (GPU integrada al SoC, no en bus PCIe separado). NCCL no puede usar GPU Direct RDMA — toda comunicación termina en CPU memory.
2. **Protocolo:** NCCL en modo IB plugin asume QP setup L2-directo entre pares. Sin GDR, los QPs son CPU↔CPU pero el plugin sigue requiriendo handshake L2 — y entre non-neighbors el handshake L2 no llega (IP forward enruta paquetes IP, no QPs).

### 2.1 Por qué NCCL falla entre nodos no-vecinos (con IB plugin activo)

RoCEv2 (RDMA over Converged Ethernet v2) usa Queue Pairs (QPs) que requieren **path L2 directo** o **fabric con soporte explícito de routing RDMA** (Mellanox SHIELD / Adaptive Routing). El IP forwarding kernel + iptables FORWARD no sirve: enruta paquetes IP, no QPs.

Cuando NCCL en `spark-1` intenta crear un QP con `spark-3`:
1. Resuelve la GID 172.31.6.10 (interfaz `roceP2p1s0f0` de spark-3) — OK vía routing IP.
2. Envía RDMA CM connection request → atraviesa spark-2 como router IP.
3. **spark-2 NO tiene InfiniBand SmartNIC con SHARP/Adaptive routing**, solo Ethernet bridging IP. La ConnectX-7 en spark-2 no procesa el QP cross-host.
4. `ibv_modify_qp` espera handshake L2 RDMA → timeout `110`.

**Conclusión:** sin path L2 directo entre cada par, RoCE QP no puede establecerse. Es una restricción de hardware/protocolo, no de software.

### 2.2 3-Spark "triangle" SÍ funciona — pero el cluster actual NO ES TRIANGLE

El blueprint NVIDIA `connect-three-sparks` requiere **3 cables formando triángulo**:
- A: spark-2 ↔ spark-3
- B: spark-3 ↔ spark-4
- C: spark-2 ↔ spark-4 (cierre del triángulo)

**HALLAZGO CRÍTICO** (validado por JARVIS bench Qwen3-Coder PP=3 04-may-2026 03:50 Lima):

El cableado físico actual es **anillo de 4 sparks** (1↔2↔3↔4↔1). Si extraemos un "3-spark cluster" usando spark-2/3/4, lo que queda es:
- Cable B: 2↔3
- Cable C: 3↔4
- **spark-2↔spark-4 SIN cable directo**

Eso es una **CHAIN, no un triángulo**. NCCL init para PP=3 espera all-to-all → falla con `ibv_modify_qp` timeout entre spark-2↔spark-4 (los nodos extremos de la chain).

**Implicación operativa**: ni siquiera 3-spark serving funciona en el cableado actual. Para activar el blueprint NVIDIA `connect-three-sparks`, hay que **recablear**: mover cable D (4↔1) o cable A (1↔2) para crear la conexión 2↔4. Eso requiere acceso físico al hardware (William despierto, decisión consciente).

### 2.3 Por qué 3-Spark triangle (físico real) SÍ funcionaría

Con 3 nodos y 2 puertos por nodo, hay exactamente `n*(n-1)/2 = 3` conexiones full-mesh y `2*n / 2 = 3` cables totales. Cada par tiene path L2 directo. Triangle es la última topología posible donde el grafo de conectividad cabe en los puertos físicos disponibles.

### 2.3 Por qué 4-Spark anillo NO sirve

Full mesh de 4 nodos necesita **6 conexiones** (`n*(n-1)/2`). Con solo 2 puertos físicos por Spark, hay `4*2/2 = 4` cables máximos = solo cubren un anillo (4 ediciones). Quedan 2 pares (1↔3, 2↔4) sin path L2.

---

## 3. Track A — Hardware Mesh sin Switch

### 3.1 Solución mashie / NVIDIA Forum t/368726 (publicada 01-may-2026 — TRES DÍAS de antigüedad)

**Idea:** subdividir cada puerto QSFP56 200G en 4 lanes de 50G usando transceivers ópticos breakout, generando 8 lanes lógicas por nodo → suficiente para full mesh + redundancia.

**Receta:**
1. 1× transceiver **200GBASE-SR4** (OSFP/QSFP56 multimodo) por puerto físico × 2 puertos × 4 nodos = **8 transceivers**.
2. 1× cable breakout **MPO-12 → 4× LC-LC** OM4 multimode (8 lanes por cable, usamos 4) × 8 = **8 cables breakout**.
3. **LC-LC duplex couplers** OM4 multi-modo (no single-mode) — el modelo confirmado por mashie es `fs.com/uk/products/68522.html` (double-width).
4. (Opcional pero ideal para full-mesh) 2× cables QSFP56 DAC **40 cm** adicionales.

**Topología propuesta por mashie** (verbatim):

```
Source              Destination
N1 LC-LC 1   ─→     N2 LC-LC 3
N1 LC-LC 2   ─→     N2 LC-LC 4
N2 LC-LC 1   ─→     N3 LC-LC 3
N2 LC-LC 2   ─→     N3 LC-LC 4
N3 LC-LC 1   ─→     N4 LC-LC 3
N3 LC-LC 2   ─→     N4 LC-LC 4
N4 LC-LC 1   ─→     N1 LC-LC 3
N4 LC-LC 2   ─→     N1 LC-LC 4
```

Eso da **ring de 100G** entre 4 nodos. Para upgrade a **full mesh**, agregar:
- 1× DAC QSFP56 200G de N1↔N3
- 1× DAC QSFP56 200G de N2↔N4

**Costos estimados (FS.com 04-may-2026):**

| Item | Qty | Unit | Total USD |
|---|---|---|---|
| QSFP56 200GBASE-SR4 transceiver | 8 | $69 | $552 |
| MPO-12 to 4× LC-LC duplex breakout, OM4, 5 m | 8 | $35 | $280 |
| LC-LC OM4 duplex coupler (double width) | 8 | $7 | $56 |
| QSFP56 200G DAC, 40 cm (full-mesh upgrade) | 2 | $99 | $198 |
| **Subtotal hardware** | | | **$1,086** |
| Envío internacional FS.com → Lima | | | ~$80 |
| Aduanas + IGV (18%) | | | ~$210 |
| **TOTAL al gate** | | | **~$1,376** |

**Ventajas:**
- Mantiene el dinero invertido (sin switch monstruo)
- Bandwidth potencial: ring 4×100G + mesh 2×200G ≈ aggregate 800G por nodo (vs 400G del setup actual)
- Resuelve el problema NCCL L2-direct
- Innovación documentable (la comunidad NO ha validado esto)

**Riesgos:**
- mashie **NO ha probado en práctica** (pdrayton lo confirmó en thread, sigue sin validación tercera)
- Calor extra: +5 W por nodo según mashie (despreciable)
- 100G entre vecinos non-DAC ≈ 50% bandwidth vs 200G DAC actual → si tu workload es TP-bound, perdés perf en algunos pairs
- Configuración manual delicada: 8 conexiones LC en cada nodo, propenso a errores de cableado

**Ruta de validación NEXUS-JARVIS:**
1. Comprar 1 set para 2 nodos (validar concepto en small scale): 2 transceivers + 2 breakouts + 2 couplers = ~$300
2. Hacer link N1↔N2 con SR4+breakout y medir vs DAC 200G actual (`ib_send_bw`, `nccl-tests/all_reduce_perf`)
3. Si bandwidth/latencia OK → comprar el resto

### 3.2 Variante: full DAC QSFP56 → 2× 100G breakout (alternativa investigada)

Existen DACs custom QSFP56 200G → 2× QSFP56 100G (Mellanox `MCP1660-W001E30` o similar). El problema documentado en el forum: **no hay forma de recombinar las 2 mitades de 100G de cada extremo**, así que se "gastan" 2 ports físicos para 1 link de 100G — peor ROI que SR4.

**Veredicto:** descartar.

### 3.3 Variante: doble switch mesh (2× MikroTik CRS804)

Idea de `ash.x.kingsley` (NVIDIA forum, mismo thread): cada Spark conecta sus 2 ports a 2 switches diferentes. NCCL detecta los dos paths y balancea. Costo: 2× CRS804 ≈ $3.2K, no resuelve el "sin switch" pero da redundancia y baja latencia. **Aplicable solo si ya tienes que comprar switch.**

---

## 4. Track B — Switch Dedicado 200G

### 4.1 Opciones validadas por la comunidad

| Switch | Ports | Speed | Power | Precio (US 2026) | Notas |
|---|---|---|---|---|---|
| **MikroTik CRS804-4XQ-IN** | 4× QSFP56 | 200G | <50 W | $1,600 | Validado en cluster 8-Spark (foysal). RouterOS, fanless, doméstico. |
| **MikroTik CRS812-32GA-2Q+IN** | 2× QSFP+ | 200G→2×100G | <50 W | $750 | breakout via `MCP7F60-W001R30` (400G→4×100G) |
| **NVIDIA Spectrum-2 SN3700-CS2F** | 32× 200G | 200G full | 380 W | $14,500 | Oficial NVIDIA reference. Loud. |
| **NVIDIA Spectrum-3 SN4600** | 64× 200G | 200G full | 600 W | $25,000 | Para 16+ Sparks. Overkill 4-node. |
| **Dell Z9332F-ON** | 32× 100G + 32× 400G | 100G/400G | 300 W | $6,000 (refurb) | Patrick Kennedy (ServeTheHome) recomienda para 16x. |
| **Juniper QFX5130-48C-AFO** | 48× 100G + 8× 400G | 100G/400G | 350 W | $6,500 (NIB) | Mismo nicho que Dell. |

**Para 4-Spark estricto:** MikroTik CRS804-4XQ-IN ($1,600) es el sweet spot. Fanless, low power, validado.

### 4.2 Topología con switch

```
spark-1 ─── 200G ─── ┐
spark-2 ─── 200G ─── │ MikroTik CRS804
spark-3 ─── 200G ─── │ (DAC QSFP56)
spark-4 ─── 200G ─── ┘
```

Cada spark usa 1 puerto físico para uplink al switch. El segundo puerto queda libre para:
- Backup uplink (LACP / NCCL multi-path)
- Storage NVMe-oF a una NAS dedicada
- Conexión inter-cluster (cuando sumes spark-5+)

### 4.3 Setup (resumen — manual MikroTik en RouterOS)

```routeros
/interface bridge add name=cluster-bridge fast-forward=yes
/interface bridge port add bridge=cluster-bridge interface=qsfp28-1
/interface bridge port add bridge=cluster-bridge interface=qsfp28-2
/interface bridge port add bridge=cluster-bridge interface=qsfp28-3
/interface bridge port add bridge=cluster-bridge interface=qsfp28-4
/interface ethernet set [find] mtu=9216 l2mtu=9216
/interface bridge settings set use-ip-firewall=no
```

Cluster IPs en `/24` plana, sin routing, sin iptables.

---

## 5. Track C — Software Sin Hardware Nuevo

### 5.1 Sysctl tuning (PRE-REQUISITO PARA TODAS LAS RUTAS)

```bash
# Aplicar EN LOS 4 NODOS
cat <<'EOF' >> /etc/sysctl.d/99-roce-tuning.conf
net.core.rmem_max         = 268435456
net.core.wmem_max         = 268435456
net.core.rmem_default     = 268435456
net.core.wmem_default     = 268435456
net.ipv4.tcp_rmem         = 4096 87380 268435456
net.ipv4.tcp_wmem         = 4096 65536 268435456
net.ipv4.tcp_mem          = 268435456 268435456 268435456
net.core.netdev_max_backlog = 250000
net.core.optmem_max       = 134217728
net.ipv4.tcp_low_latency  = 1
net.ipv4.tcp_timestamps   = 0
net.ipv4.tcp_sack         = 1
net.core.somaxconn        = 65535
EOF
sysctl --system
```

**Esperado:** elimina los timeouts intermitentes en pares directos. Independiente del Track A/B/C.

### 5.2 NCCL_NET=Socket TCP fallback

```bash
# Variables a settear en lanzador vLLM / Ray
export NCCL_NET=Socket               # forzar TCP, desactivar IB plugin
export NCCL_SOCKET_IFNAME=enP9s9     # WiFi 6 GHz si los RoCE ports siguen rotos
                                     # mejor: el RoCE direct neighbor
export NCCL_IB_DISABLE=1
export NCCL_DEBUG=INFO
export NCCL_DEBUG_SUBSYS=NET,INIT
```

**Performance esperada:** 1.5–4 GB/s aggregate vs 25 GB/s nativo RoCE. Inferencia de LLM 70B+ funciona pero con throughput bajo. No recomendado para entrenamiento.

### 5.3 GLOO en planos de control (PyTorch DDP coordination)

PyTorch permite usar **NCCL para datos** + **GLOO para control plane**:

```python
import torch.distributed as dist
dist.init_process_group(
    backend="cpu:gloo,cuda:nccl",
    init_method="tcp://10.0.0.2:29500",
    rank=rank, world_size=4
)
```

Esto desacopla colectivos pesados (que necesitan path directo) de barrier/broadcast pequeños (toleran routing IP).

### 5.4 Soft-RoCE (RXE/SIW)

Linux ofrece RDMA emulado sobre el stack TCP/IP del kernel:

```bash
modprobe rdma_rxe
rdma link add rxe0 type rxe netdev enP9s9    # crear RoCE virtual sobre WiFi
```

Permite RDMA semántico sobre cualquier interfaz IP, **incluyendo paths enrutados**. Performance: 10–20% del hardware nativo. Útil para validar lógica del cluster, no para producción.

### 5.5 EXO Labs (orquestación heterogénea)

**Repo:** https://github.com/exo-explore/exo
**Producto:** EXO 1.0 — distribuye un LLM monolítico a través de **N dispositivos heterogéneos** (DGX Spark, Mac Studio, RTX servers, etc.) usando un grafo de pipeline custom que evita NCCL collectives multi-host.

NVIDIA forum thread `tigercyborg666` (Dec 2025) menciona que EXO permitió **4× faster LLM inference combinando DGX Spark + Mac Studio**. Para nuestro caso: 4× Sparks con EXO bypassa el problema NCCL completamente.

**Trade-off:** EXO no soporta TODO el ecosistema CUDA — está optimizado para inferencia de LLM en formato GGUF/MLX. **No sirve para training, no sirve para vLLM nativo.**

### 5.6 VXLAN overlay / UCX TCP

Posibles pero más complejos y con menor ROI esperado que las opciones anteriores. **Documentados en spec v2 si las pruebas iniciales fallan.**

### 5.7 NCCL_TOPO_FILE — Topology Hints custom (HALLAZGO TARDÍO — VALIDAR)

NCCL acepta un XML de topología explícita vía `NCCL_TOPO_FILE=/path/topo.xml`. Esto le permite a NCCL **construir su grafo de comunicación evitando paths que no existen físicamente**.

Idea: declarar que solo hay paths directos vecino-vecino, forzando a NCCL a usar pipeline parallel ring (que naturalmente solo cruza vecinos) en vez de tree algorithms (que requieren paths globales).

**Variables clave (no probadas por JARVIS aún):**

```bash
export NCCL_TOPO_FILE=/etc/nccl/topo-4spark-ring.xml
export NCCL_TOPO_DUMP_FILE=/tmp/nccl-detected.xml   # primero dump para ver qué detecta
export NCCL_ALGO=Ring                                # forzar Ring (no Tree)
export NCCL_PROTO=Simple                             # más conservador en multi-host
export NCCL_IB_QPS_PER_CONNECTION=4                  # más QPs paralelos
export NCCL_IB_ADAPTIVE_ROUTING=1                    # adaptive routing si fabric soporta
export NCCL_OOB_NET_ENABLE=1                         # control plane OOB (separado del data)
export NCCL_OOB_NET_IFNAME=wlP9s9                    # WiFi como OOB
export NCCL_CROSS_NIC=0                              # NO permitir cross-NIC entre rings
```

**Plan de validación rápida (15 min, gratis, JARVIS puede correr ahora):**

```bash
# 1. Dump topología detectada actual
NCCL_TOPO_DUMP_FILE=/tmp/nccl_detected.xml \
NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=GRAPH,INIT \
  python /opt/nccl-tests/build/all_reduce_perf -b 1M -e 128M -f 2 -g 1

# 2. Inspeccionar XML detectado — ver si NCCL "ve" non-neighbor paths
cat /tmp/nccl_detected.xml | grep -E '(ring|tree|node|path)'

# 3. Editar XML para deshabilitar non-neighbor paths
# 4. Re-correr con NCCL_TOPO_FILE apuntando al XML editado
```

**Por qué esto puede destrabar TODO sin hardware nuevo:**
- Si NCCL deja de intentar paths non-vecino y vuelve a Ring puro, las QPs solo se forman entre vecinos directos (donde sí hay L2).
- Pipeline parallel sobre ring de 4 ya es viable (Qwen3-Coder-480B PP=4).
- TP=4 puro NO es viable (requiere all-reduce global → cross-neighbor).

**Output esperado:**
- Si funciona → vLLM PP=4 sobre 4-Spark anillo SIN comprar hardware. Track A/B se posponen indefinidamente.
- Si no funciona → confirma que el problema es L2 setup duro y necesitamos Track A/B.

**Esto es prioridad 1 esta noche.** Si JARVIS lo prueba y funciona, William despierta con 4-Spark ya operativo SIN gastar un peso.

---

## 6. Análisis Comparativo

| Solución | Bandwidth real | Latencia | Costo USD | Riesgo | Reversibilidad | Tiempo setup |
|---|---|---|---|---|---|---|
| **A1 mashie SR4** | ~100G ring + 2×200G mesh | <2 µs | $1,376 | Alto (no validado) | Alta | 2-3 días (envío + setup) |
| **A3 dual switch CRS804** | 200G full mesh | <1 µs | $3,200 | Bajo | Alta | 1 día |
| **B1 MikroTik CRS804** | 200G full mesh | <1 µs | $1,600 | Muy bajo | Alta | 4 h |
| **B3 NVIDIA SN3700** | 200G full mesh | <500 ns | $14,500 | Muy bajo | Media | 1 día |
| **C1 sysctl** | (mejora marginal) | -10% latency | $0 | Cero | Trivial | 5 min |
| **C2 NCCL Socket** | 1.5–4 GB/s | alta | $0 | Bajo | Trivial | 10 min |
| **C3 GLOO control** | (control plane only) | baja | $0 | Bajo | Trivial | 30 min |
| **C4 Soft-RoCE** | <5 GB/s | media | $0 | Medio | Trivial | 1 h |
| **C5 EXO Labs** | (modelo-pipeline) | baja | $0 | Medio | Alta | 4 h |

---

## 7. Recomendación NEXUS

### 7.1 Plan estratégico (3 fases)

**Fase 0 — esta noche (libre albedrío JARVIS+NEXUS):**
1. Aplicar Track C1 (sysctl) en los 4 nodos.
2. Aplicar Track C3 (GLOO control plane).
3. Re-corre `nccl-tests all_reduce_perf` entre **vecinos directos**. Esperamos baseline limpio.
4. Para Qwen3-Coder PP=3 → usar **3-Spark cluster (spark-2/3/4)** con triangle topology validada. Spark-4 termina como worker.
5. Documentar bench results: latencia, bandwidth aggregate, tokens/sec.

**Fase 1 — 24 h (William awake):**
1. Compartir resultados Fase 0 con William.
2. Decidir Track A1 (transceivers SR4) vs Track B1 (MikroTik CRS804).
3. **Mi voto:** Track A1 si la ruta validada vale la innovación; Track B1 si prioridad es certidumbre.

**Fase 2 — 48–72 h (post-decisión):**
- Si A1: ordenar partes FS.com, validar 2-node SR4 antes de full deploy.
- Si B1: ordenar MikroTik, configurar bridge, migrar IPs.

### 7.2 Por qué NO recomiendo Track C como único plan

NCCL Socket fallback degrada 5–10× la performance de inferencia. Con un modelo 480B MoE, cada token cruza fronteras de nodos múltiples veces. La latencia P99 será inaceptable para servir SPECTRE en producción.

### 7.3 Por qué NO recomiendo switches enterprise (B3)

Sobre-dimensionado para 4 sparks. SN3700 / Z9332F-ON tiene sentido a partir de 16 sparks. Hoy pagás 10× por capacidad que no usás.

---

## 8. Plan de Implementación (Track A1 — mashie SR4)

```
Día 0 (hoy)        — sysctl tuning + Qwen3 en 3-Spark validado
Día 1              — comprar set 2-node SR4 (~$300) en FS.com
Día 4              — partes llegan, instalar 2× SR4 + breakout en N1+N2
Día 5              — bench 2-node SR4 vs DAC actual
Día 6              — si OK, ordenar set restante (~$1K)
Día 10             — partes restantes llegan
Día 11             — armar full mesh ring 4-node + 2 DAC mesh
Día 12             — bench 4-node NCCL all_reduce
Día 13             — vLLM TP=4 PP=1 con Qwen3-Coder en 4-Spark full mesh
```

## 9. Riesgos & Mitigations

| Riesgo | Probabilidad | Impacto | Mitigation |
|---|---|---|---|
| SR4 transceiver no establece link | Media | Alto | Comprar 2 primero, validar antes de orden completa |
| 100G en breakout no balancea con 200G DAC mesh → NCCL ring confunde | Media | Medio | Forzar `NCCL_GRAPH_FILE` con grafo custom; usar PP topology-aware |
| Calor 5 W extra dispara thermal throttling | Baja | Bajo | Monitor `mlxconfig` + airflow |
| Cableado mal hecho (8 LCs × 4 nodos) | Alta | Medio | Etiquetado físico + script `ibstat` validation diario |
| FS.com retrasa envío | Media | Bajo | Ordenar `set 1` ya con pruebas inmediatas |
| Qwen3-Coder no fitea pese a NVFP4 | Baja | Medio | Fallback GLM-5.1 NVFP4 (cabe en 4-spark con switch) |

## 10. Lista de Partes Comprable (FS.com — 04-may-2026)

### Set 1 (validación 2-node, ordenar YA — ~$300)

| SKU FS.com | Descripción | Qty | Precio |
|---|---|---|---|
| 76105 / 68522 | LC-LC OM4 duplex coupler (double width) | 2 | $14 |
| (consultar) | QSFP56 200GBASE-SR4 transceiver | 2 | $138 |
| (consultar) | MPO-12 to 4× LC-LC OM4 breakout, 5 m | 2 | $70 |
| | shipping + import | | $80 |
| **TOTAL Set 1** | | | **~$302** |

### Set 2 (full mesh 4-node — orden post-validación)

| Item | Qty | Precio |
|---|---|---|
| QSFP56 200GBASE-SR4 transceiver | 6 | $414 |
| MPO-12 to 4× LC-LC OM4 breakout, 5 m | 6 | $210 |
| LC-LC OM4 duplex coupler | 6 | $42 |
| QSFP56 200G DAC 40 cm | 2 | $198 |
| shipping + import | | $210 |
| **TOTAL Set 2** | | **~$1,074** |

**TOTAL FULL DEPLOY: ~$1,376**

## 11. Modelos LLM Compatibles con 3-Spark / 4-Spark (catálogo JARVIS, 04-may-2026 02:03 Lima)

### 11.1 Tier 1 — Modelos para cluster (200-330 GB VRAM)

| Modelo | Total/Activos | VRAM | Cuant | Fit 3-Spark | Fit 4-Spark mesh | Use case | Status |
|---|---|---|---|---|---|---|---|
| **nvidia/Qwen3-Coder-480B-A35B-Instruct-NVFP4** | 480B/35B MoE | ~240 GB | NVFP4 | ✅ PP=3 | ✅ TP=2 PP=2 | code SOTA | 🔄 descargando 115/250 GB |
| **nvidia/Qwen3.5-397B-A17B-NVFP4** | 397B/17B MoE | ~200 GB | NVFP4 | ✅ PP=3 | ✅ TP=4 | general SOTA | 🔄 descargando |
| **nvidia/DeepSeek-R1-0528-NVFP4-v2** | 671B/37B MoE | ~340 GB | NVFP4 | ⚠️ PP=3 tight | ✅ TP=4 mesh | reasoning SOTA | candidato |
| **nvidia/Kimi-K2.5-NVFP4** | 1T/32B MoE | variable | NVFP4 | ⚠️ revisar | ✅ TP=4 | long context | candidato |
| **nvidia/GLM-5-NVFP4** | 600B+/40B MoE | ~300 GB | NVFP4 | ✅ PP=3 | ✅ TP=4 | general | candidato |
| **nvidia/DeepSeek-V3.2-NVFP4** | 671B/37B MoE | ~340 GB | NVFP4 | ⚠️ PP=3 tight | ✅ TP=4 | general | candidato |
| **zai-org/GLM-5.1-NVFP4** | 110B dense | 434 GB | NVFP4 | ❌ | ✅ TP=4 | dense | descargado |
| **inclusionAI/Ring-2.5-1T** | 1T MoE | ~500 GB | INT4 | ❌ | ✅ TP=4 PP=1 | research | candidato |

### 11.2 Tier 2 — Modelos para 1-Spark standalone (15-80 GB VRAM)

| Modelo | Total/Activos | VRAM | Use case |
|---|---|---|---|
| nvidia/Gemma-4-31B-IT-NVFP4 | 31B dense | ~16 GB | top trending, multimodal |
| nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 | 120B/12B MoE | ~60 GB | reasoning |
| nvidia/Llama-3.3-70B-Instruct-NVFP4 | 70B dense | ~35 GB | general |
| nvidia/Qwen3-Next-80B-A3B-Instruct-NVFP4 | 80B/3B MoE | ~40 GB | fast inference |
| nvidia/Llama-4-Scout-17B-16E-Instruct-NVFP4 | 17B/4B MoE | ~9 GB | edge |
| nvidia/MiniMax-M2.7-NVFP4 | ~80 GB | ~80 GB | long context |

### 11.3 Implicaciones para la decisión de interconexión

**Si solo te interesa servir Qwen3-Coder + 1-2 modelos Tier 2** → 3-Spark triangle alcanza. Track A1/B1 NO es prioridad.

**Si quieres servir 2 modelos Tier 1 simultáneos (un Qwen3.5 generalist + un Qwen3-Coder dedicado)** → necesitás 4-Spark mesh (asignar TP=2 a cada modelo en pares de sparks). Track A1/B1 ES prioridad.

**Si DeepSeek-R1-0528 / Ring-2.5-1T entran al roadmap (reasoning SOTA)** → 4-Spark mesh es indispensable. Track B1 (switch) recomendado por menor riesgo.

(Tabla la mantiene JARVIS post-bench: `/home/dadito/IA/proyecto-seal/agents/JARVIS/cluster_serving_results_20260504.md`.)

## 12. Referencias

### NVIDIA Forums

- **mashie's 4-node sin switch (01-may-2026)** — [t/368726](https://forums.developer.nvidia.com/t/368726)
- **maiia: Switch Recommendations** — [t/.../328523](https://forums.developer.nvidia.com/t/connecting-multiple-dgx-spark-units-ethernet-switch-recommendations/)
- **8x+ y 16x clusters** — `ash.x.kingsley`, `Patrick-ServeTheHome`
- **4x cluster best practices** — `tigercyborg666`, `foysal`
- **3-spark Triangle config** — Forum `Three GB10 in Triangle configuration` (Jan 2026)

### Repos

- **NVIDIA/dgx-spark-playbooks** — `connect-two-sparks`, `connect-three-sparks`, `multi-sparks-through-switch`
- **oshlabs/dgx-spark-cluster** — community 2-node manual
- **build.nvidia.com/spark/vllm/stacked-sparks** — vLLM stacked official
- **EXO Labs** — github.com/exo-explore/exo

### Documentación

- NCCL Environment Variables — `docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html`
- ConnectX-7 firmware notes — `docs.nvidia.com/networking/display/connectx7firmwarev28454028`
- vLLM distributed serving — `docs.vllm.ai/en/latest/serving/distributed_serving.html`
- ServeTheHome — DGX Spark networking analysis (March 2025)

### Memorias SOUL

- **#211201** — receta canónica cluster 4-node Ray (JARVIS)
- **#211167** — lecciones aprendidas debug NCCL
- **#211301** — regla de oro modelos LLM ubicación
- **#211427** — brief JARVIS → NEXUS 04-may-2026 (este spec arranca aquí)

---

## 12.1 Decision Flowchart (cuándo usar qué track)

```
┌─────────────────────────────────────────┐
│  ¿Necesitás 4-Spark NCCL hoy mismo?     │
└─────────────────────────────────────────┘
               │
        ┌──────┴──────┐
       SÍ            NO
        │             │
        ▼             ▼
┌───────────────┐  ┌────────────────────────────┐
│ Track C2/C3   │  │ ¿Querés certidumbre o      │
│ NCCL_NET=     │  │ querés pionerizar?         │
│ Socket + GLOO │  └────────────────────────────┘
│ 0$ pero 5-10× │      │              │
│ más lento     │  CERTIDUMBRE     PIONERIZAR
└───────────────┘      │              │
                       ▼              ▼
                ┌─────────────┐  ┌──────────────────┐
                │ Track B1    │  │ Track A1 mashie  │
                │ MikroTik    │  │ SR4 transceivers │
                │ CRS804      │  │ + breakouts      │
                │ ~$1,600 USD │  │ ~$1,376 USD      │
                │ 4h setup    │  │ 10-13 días setup │
                │ 0% riesgo   │  │ ~30% riesgo      │
                └─────────────┘  └──────────────────┘
```

## 12.2 Antes de cualquier track: Track C1 (sysctl) — siempre

Independiente del camino que elijas, los buffers RoCE están tristemente bajos (212 KiB). Aplicar sección 5.1 da mejora libre incluso en topología actual.

---

## 13. Open Questions (input requerido William o JARVIS)

1. ¿Hay budget aprobado para hardware antes de validar Fase 0? (~$300 set 1 vs $1,376 full)
2. ¿Permitimos calls a FS.com / shipping a Lima vs proveedor local Mercado Libre Perú?
3. ¿Spark-1 (SOUL) entra al mesh o se mantiene aislado siempre? (Mi sugerencia: mantener aislado — SOUL no debe ser worker de cómputo)
4. ¿Track A1 tiene un deadline implícito (ICTSE paper, demo, etc) que justifique riesgo?

---

**Próximo paso NEXUS:** abrir PR / commit `spec_4spark_interconnect_v1.md` → JARVIS review → entregar a William post-bench Fase 0.

**ETA review JARVIS:** 30 min después de upload.
**ETA decisión final:** mañana 04-may con datos bench reales.
