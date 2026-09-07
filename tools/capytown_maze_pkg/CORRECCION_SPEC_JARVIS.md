# Corrección CapyTown — spec para ADA (autor JARVIS, 01-jul-2026)

Henry pidió "trabajen juntos en una corrección". Esto es el análisis de arquitectura +
el spec concreto de la corrección. **ADA es dueña del código** (single-editor); esto es
insumo, no edición del `.py`. Basado en el bug-chain reportado por Henry + el código de
referencia G4 (`/tmp/ref_capy/capytown_G4_s12/`), que está hecho para ESTE reto exacto.

---

## 0) ⭐ CORRECCIÓN POR EFECTO (reporte real de Henry, 18:35) — LA CAUSA REAL

El reporte `/tmp/capytown_maze_report.log` de la corrida real DESMIENTE mi hipótesis
previa (post_veer → TURN_OUT falso). Lo owneo: en la corrida NO hay TURN_OUT ni veer
(phase=IDLE todo el tiempo). La causa confirmada por dato es **CASCADA DE TURN_IN**.

Traza (yaw en grados):
```
front 0.27 bloquea -> TURN_IN_REQUEST (yaw -4)
TURN_IN -> sale a FOLLOW_WALL yaw=24 (~29°) porque corner_pose_aligned=TRUE con front=0.79 (1 tick)
front baja 0.43->0.30 -> TURN_IN_REQUEST otra vez (yaw 35)
TURN_IN yaw 31->70, sigue bloqueado (front 0.18, shL 0.19 shR 0.21 = rincón) -> yaw 124 -> TURN_IN otra vez -> yaw 178
NETO: -4° -> 178° ≈ 180° acumulados en 3 TURN_IN seguidos = "dirección contraria"
```

MECÁNICA: en un rincón (front + AMBOS hombros cerca) un solo TURN_IN de 90° no despeja el
frente. `corner_pose_aligned` corta el giro con un front-clear TRANSITORIO (1 tick) →
vuelve a FOLLOW_WALL → re-bloquea → re-TURN_IN → los giros se ACUMULAN hasta ~180°.

FIX (con la causa real):
1. **Anti-180 en el path FSM** (no solo en veer): trackear el yaw acumulado desde el 1er
   TURN_IN de una racha; si |yaw − yaw_inicio_racha| > ~100-110° sin front despejado ESTABLE
   → cortar (hold/backup), NO seguir girando. Análogo de `veer_max_yaw_delta` para TURN_IN.
2. **corner_pose_aligned no debe salir con front-clear de 1 tick**: exigir persistencia
   (~0.4s) + algo de avance antes de aceptar la pose → no re-dispara TURN_IN en loop.
3. (opcional) **cooldown post-TURN_IN**: tras completar un TURN_IN, avanzar un tramo corto
   antes de permitir otro → rompe el ciclo tick-a-tick.

Líneas: `corner_pose_aligned` (337-350) + early-exit en tick (767-778) + begin_turn/run_turn.
Lo de abajo (§1-§4c) queda como contexto; el post_veer (§0 viejo) sigue mejorable pero NO
era el bug de ESTA corrida.

## 1) Root cause de arquitectura (por qué el whack-a-mole)

El paquete actual maneja las cajas con **veer continuo, reactivo a distancia**
(`enable_obstacle_veer`, `obstacle_detect`). Ese primitive es la causa raíz del bucle de
síntomas de Henry:

| Síntoma de Henry | Por qué lo causa el veer continuo |
|---|---|
| "esquiva con demasiada antelación" | `obstacle_detect` grande → empieza el arco lejos |
| "detecta la pared como obstáculo" | el arco/umbral frontal atrapa la pared lateral o el frente de un tramo → no discrimina pared vs caja |
| "gira antes, vuelve a su ruta temprano y choca la caja" | el veer es reactivo a distancia: al abrir un poco el frente cree que ya pasó y reincorpora encima de la caja |
| "queda de frente y busca otro camino" | sin periodo de gracia tras esquivar → re-detecta la misma caja |

**Conclusión:** para un LAZO-con-cajas, el veer continuo es el primitive equivocado. El
correcto es **discreto y determinístico**: PARAR → RODEO temporizado (open-loop) → GRACIA.

## 2) Verificar la geometría del LiDAR PRIMERO (sigue sin confirmarse)

Antes de tunear más a ciegas: el `[DBG]` (front/left/right) que agregué nunca se leyó.
Varios síntomas ("detecta pared como obstáculo", CCW, TURN_IN) son consistentes con
**sectores del LiDAR rotados vs el frente físico**. Pedir a Henry UNA línea del `[DBG]`
parado en el pasillo + de qué lado está la pared → derivar `front_angle_offset`. Tunear
sobre sectores mal alineados es perseguir fantasmas. (Regla debug-by-effect.)

## 3) La corrección: FSM discreta (portada de G4, adaptada a robot 9)

Reemplazar el manejo de caja por esta FSM (G4 `behavior_fsm.py`), que además cumple el
**requisito del reto: parar 3s frente a la caja** (nuestro pkg no lo hace):

```
CRUCERO --(front < d_parada)--> PARAR --> ESPERAR_3S --> RODEAR --> (gracia) --> CRUCERO
   |__(front < d_alerta)--> CAJA_EN_PISTA (avanza lento) --> PARAR / vuelve a CRUCERO
```

- **RODEAR = open-loop temporizado en 3 fases** (NO reactivo a distancia): girar +ang,
  avanzar t_avance, girar -ang. Geometría fija → no "reincorpora temprano y choca".
- **GRACIA post-rodeo**: tras rodear, avanza RECTO `gracia_post_rodeo_seg` SIN re-reaccionar
  → deja la caja atrás, mata el bucle "queda de frente / busca otro camino".

### Params sugeridos (arranque, afinar por efecto)
```yaml
# manejo de caja (discreto)
enable_obstacle_veer: false      # APAGAR el veer continuo (es la causa del choque)
dist_alerta:  0.35               # m -> CAJA_EN_PISTA (avanza lento)
dist_parada:  0.22               # m -> PARA (no la roza; robot edge queda ~14cm)
espera_seg:   3.0                # requisito del reto (Guardián)
angulo_rodeo_deg: 70.0
avance_rodeo_seg: 2.2            # 60cm corredor: rodeo justo, no se sale del lazo
gracia_post_rodeo_seg: 2.0
vel_precaucion: 0.07
```

## 4) Discriminar PARED vs CAJA (el bloqueante actual de Henry)

Regla determinística para no tratar la pared como caja:

- **CAJA** = objeto en el sector **frontal ESTRECHO** (±12–15°), cercano, **con el lado de
  escape libre** (side clearance > umbral). Solo entonces PARAR→RODEAR.
- **PARED/ESQUINA** = frente bloqueado PERO el lado de seguimiento sigue con pared (sin
  escape) → eso es SEGUIR-LA-ESQUINA (TURN_OUT/follow), NO un rodeo.
- Front sector estrecho evita que la pared lateral dispare la reacción frontal. Si adoptan
  el sector ancho de G4 (45°), compensen con el chequeo de "escape libre" antes de rodear.

Opción robusta (si hay tiempo): el `wall_follower.py` de G4 (Split-and-Merge) clasifica
segmentos por longitud — pared = segmento lateral largo (>0.60m); caja = corto/frontal.
Publica `/lateral_correction`. Es la discriminación pared-vs-caja "de libro".

## 4b) Control de HEADING (perpendicularidad) — mata el "gira demasiado" (req Henry)

El PD actual mide UN punto de distancia → controla cercanía, NO ángulo. Sin referencia de
heading sobre-corrige = "gira demasiado". Fix: agregar término de heading (parallelism).

Método barato sin cámara (pared DERECHA, side="right"):
- Dos lecturas sobre la pared derecha a dos bearings: r1 ~ -90° (derecha pura), r2 ~ -60°.
- `yaw_err = atan2(r2*sin(a2) - r1*sin(a1), r2*cos(a2) - r1*cos(a1)) - dir_pared_esperada`
- `w = Kp_dist*(dist - wall_target) + Kp_head*yaw_err`  → el término de heading nula el
  ángulo, no solo la distancia → queda PEGADO y PARALELO a la derecha.
- Robusto: el `wall_follower.py` de G4 (Split-and-Merge) ajusta la recta de cada pared y
  saca el error; "perpendicular a las 2 paredes" = promediar el heading de ambas.

## 4c) Detección de ESQUINAS con el sector TRASERO (req Henry: "pared detrás y a la derecha")

CLAVE: el MS200 es LiDAR de 360° (angle_min=-pi, angle_max=+pi) → **tenemos sector TRASERO
disponible**. Agregar 4 sectores: front (0°), right (-90°), left (+90°), **rear (180°)**.

Lógica de esquina por presencia de pared (lazo, hug derecha):
- **Esquina cóncava** (frente bloqueado + pared derecha presente, sin pared atrás cerca):
  `front < front_block AND right ~ wall_target` → GIRAR A LA IZQUIERDA (TURN_IN) para tomar
  la esquina interior.
- **Esquina convexa** (la pared derecha se ACABA / se abre): `right > wall_lost AND front
  abierto` → GIRAR A LA DERECHA para reacoplar la pared (borde exterior de la isla).
- **CONFIRMAR que la vuelta se completó** (esto es lo que pide Henry): tras el giro, la
  pared que tenías al frente queda ATRÁS → `rear ~ close AND right ~ wall_target AND front
  abierto` = alineado en el nuevo tramo. Usar el rear sector como check de "esquina tomada
  bien" antes de volver a CRUCERO. Si rear+right no confirman, seguí ajustando el heading.
- Al salir de la esquina, el control de heading (4b) lo asienta paralelo a la derecha.

## 5) Caveats al portar de G4
- G4 usa `/odom` y frame `'odom'` → **robot 9 es `/odom_raw`**, remapear.
- G4 trae `.git/` embebido en el zip — no copiar eso al workspace.
- G4 no arregla la alineación del LiDAR; eso sigue siendo nuestro `front_angle_offset`.

## 6) División propuesta (para no floodear a Henry — UNA voz)
- **ADA**: integra la FSM discreta en `maze_navigator.py` (dueña del código).
- **JARVIS**: este spec + soporte de análisis / re-derivación geométrica.
- **ALICE**: verificar params/umbrales por efecto contra la geometría del pasillo 60cm.
- **NEXUS**: build/deploy limpio en robot 9 (`--packages-select`, no nukear la base).
- **Reporte a Henry**: SOLO ADA (consolidado), no 4 mensajes.
