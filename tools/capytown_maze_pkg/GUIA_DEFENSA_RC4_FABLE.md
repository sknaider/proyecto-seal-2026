# Guía de DEFENSA — RC-4 «El Censo y el Guardián de las Cajas» (FABLE)

> **Para Henry, antes de la defensa oral (60% de la nota).** Esta guía NO es un guion para
> memorizar — el profe Marks repregunta «¿qué pasaría si…?» y hunde al que recita sin entender.
> El objetivo es que **entiendas** el sistema para defenderlo con tus palabras. Cada sección tiene:
> **(1) qué te va a preguntar · (2) la respuesta con el porqué · (3) repreguntas trampa y cómo salir.**
> Todo está anclado al código real (`box_detector.py` y `maze_navigator.py`). Cita el **nombre de la función/constante** (ej. `detect_boxes_in_scan`, `RANGE_JUMP`), no el número de línea (cambia con las ediciones).

---

## DEF1 — Comprensión teórica (18%, 3.6 pts): LaserScan, clustering 1D, transformaciones

### 1a. ¿Qué es un `LaserScan`? (te lo van a preguntar seguro)
Es el mensaje del LiDAR 2D (MS200). Da un **arreglo de distancias** (`ranges`), una por ángulo, barriendo el plano. Campos que importan:
- `ranges[i]` = distancia medida en el rayo *i* (metros).
- `angle_min` = ángulo del primer rayo; `angle_increment` = paso angular entre rayos.
- El ángulo del rayo *i* es: **`ángulo_i = angle_min + i · angle_increment`**.
- `range_min`/`range_max` = límites válidos (fuera de eso, el dato es basura: NaN/inf/0).

**Por qué importa:** el LiDAR NO te da (x,y), te da (distancia, ángulo) = **coordenadas polares**. Todo el trabajo de percepción es convertir eso en algo útil.

### 1b. Clustering 1D (el corazón del censo) — función `detect_boxes_in_scan`
El scan es una tira de distancias. Una caja es un **tramo de rayos vecinos a distancia parecida**. El algoritmo:
1. Recorre los rayos y **crece un segmento** mientras el salto entre rayos consecutivos sea chico:
   `while |r[j+1] − r[j]| < RANGE_JUMP (0.12 m): j++` — un salto grande = **borde** (fin de un objeto).
2. Por cada segmento calcula su **ancho físico**: `width = dist · (n_puntos · angle_increment)`.
   (arco = radio × ángulo; el ángulo del tramo es `n_puntos · angle_increment`).
3. Es una **caja** si `|width − 0.20| ≤ 0.08` (entre 12 y 28 cm) **y** sobresale de sus vecinos (`protrusión > 5 cm`).

**El porqué (clave para el profe):** es «1D» porque clusterizamos sobre el **índice del rayo** (la tira ordenada por ángulo), no sobre un plano 2D. El salto de rango es la distancia-métrica del cluster. No usamos k-means ni nada pesado: es apto para correr en el robot (edge).

### 1c. Transformaciones polar → cartesiano → mundo (odom) — `detect_boxes_in_scan` (frame LiDAR) + el `_tick` del nodo (a mundo)
Tres pasos, y el profe puede pedir las fórmulas:
1. **Polar (frame del LiDAR):** del segmento sacás `(dist, ang)` con `ang = angle_min + mid · angle_increment`.
2. **Cartesiano (frame del robot):** `x_r = dist·cos(ang)`, `y_r = dist·sin(ang)`.
3. **Mundo (frame odom):** rotás por el yaw del robot y trasladás por su posición:
   `wx = x0 + dist·cos(yaw + ang)` · `wy = y0 + dist·sin(yaw + ang)`
   donde `(x0, y0, yaw)` viene de `/odom` (el yaw se saca del cuaternión, `yaw_from_quat`).

**Por qué al mundo y no al robot:** para **censar sin duplicar**. El robot se mueve; la misma caja aparece en muchos scans. Solo llevándola a coordenadas del mundo podemos decir «esta caja ya la conté» (dedup, ver DEF2).

---

## DEF2 — Justificación de decisiones de diseño (16%, 3.2 pts): con criterio, no intuición

| Decisión | Valor | **Por qué ese valor** |
|---|---|---|
| Umbral de salto (`RANGE_JUMP`) | 0.12 m | La rúbrica pide 8–15 cm. 12 cm está en el medio: chico bastó para separar cajas contiguas, grande evita que el ruido del LiDAR parta una caja en dos. |
| Ancho esperado + tolerancia (`BOX_SIZE`/`BOX_TOL`) | 0.20 ± 0.08 m | Las cajas son 20×20 cm. La tolerancia ±8 cm cubre el error de medición del ancho a distancia (un tramo se ve más angosto/ancho según cuántos rayos lo tocan). |
| Protrusión mínima (`MIN_PROTRUSION`) | 0.05 m | Distingue una **caja** (sobresale de la pared) de un pedazo de **pared** plana. Sin esto, cualquier tramo de 20 cm de pared sería un falso positivo. |
| Distancia de dedup (`BOX_MERGE_DIST`) | 0.30 m | Dos detecciones a < 30 cm = la misma caja vista dos veces. Las cajas se montan separadas ≥ 40 cm (regla del reto), así que 30 cm no fusiona dos cajas reales. |

### FP vs FN — el trade-off que el profe SIEMPRE pregunta
- **Falso Positivo (FP):** contar una caja que no existe (un pedazo de pared, ruido). Lo matamos con **protrusión** + **ancho**.
- **Falso Negativo (FN):** no ver una caja que sí está. Pasa si la tolerancia de ancho es muy estricta o el salto muy chico.
- **Nuestro criterio:** priorizamos **FP = 0** (la rúbrica IMP2 lo exige) — más vale ser conservador. La protrusión + el rango de ancho hacen el censo estricto. El costo es que una caja mal orientada o muy lejos podría ser FN — eso se ve en las métricas (DEF3).

---

## DEF3 — Análisis de métricas (14%, 2.8 pts): se llena con TUS 10 corridas

> El `metricas_lidar.csv` se genera **solo** al cerrar cada corrida (`census_metrics` compara el censo contra el ground-truth que vos cargás con cinta). Tu trabajo (R4): correr las 10 veces, cargar el ground-truth, y **analizar** el CSV. El marco de análisis:
- **VP** (verdadero positivo): caja real detectada. **FP**: detección sin caja. **FN**: caja real no detectada.
- **Tasa de detección = VP / (VP + FN)** — la métrica central de la rúbrica, agregada sobre las 10 corridas.
- **Error de posición:** distancia entre la posición censada (wx,wy) y la real (cinta métrica). La rúbrica pide ≤ 30 cm.
- **Qué decir en la defensa:** no solo el número — **interpretalo**. Ej: «en la corrida 7 tuvimos 1 FN porque la caja C5 estaba a > 2 m y el tramo cayó por debajo del ancho mínimo; se corrige acercando el sector o bajando `BOX_TOL`». Eso es «propone mejoras basadas en datos» = nivel Excelente.

---

## DEF4 — Dominio del código (8%, 1.6 pts): ubicar y explicar cualquier parte

### Los dos nodos (arquitectura oficial: `/scan → box_detector → /cajas_avistadas → fsm`)
- **`box_detector.py`** — el CENSO. Función pura `detect_boxes_in_scan` (testeable sin ROS) + nodo `BoxDetector` que suscribe `/scan` + `/odom`, corre a 5 Hz. **Publica dos cosas:** `/cajas_avistadas` (`Int32` = conteo, para la FSM) y `/cajas_avistadas_pos` (`PoseArray` = **posiciones** de cada caja en el mundo, para IMP2). Al cerrar la corrida (Ctrl-C) escribe una fila en `metricas_lidar.csv` con VP/FP/FN/tasa/error, comparando el censo contra el ground-truth (función pura `census_metrics`, verificada por efecto).
- **`maze_navigator.py`** — el GUARDIÁN (FSM de reacción). Funciones puras clave:
  - `decide_state`: la FSM. Prioridad → boxed→TURN_IN · pared se abre→TURN_OUT · frente bloqueado→TURN_IN · si no→FOLLOW_WALL.
  - `follow_cmd`: ley de control **PD** de seguir-pared. `error = wall_target − dist_pared`; con **deadband** (no zigzaguea) y término **D** (amortigua). Signo unit-testeado.
  - `run_turn`: giros cerrados por **odom** (por yaw, no por tiempo), con el fix de **overshoot-stop** (para al alcanzar O pasar el objetivo).
  - **Guardián `GUARD_STOP`** (Parte B, IMP3): al detectar una caja adelante, el robot **PARA** (`publish(0,0)`), **ESPERA 3 s** (`box_stop_wait_t`) y recién entonces **RODEA**. Se resetea por caja, así se detiene en CADA una. La parada ocurre a la distancia de disparo (~35 cm > los 15 cm que exige la rúbrica).

### Repreguntas «¿qué pasaría si…?» (prepará estas)
- **«¿Y si el LiDAR está montado girado?»** → hay `front_angle_offset` que rota los sectores; en robot 9 = 0, verificado por efecto.
- **«¿Y si un rayo da una lectura fantasma cercana?»** → `sector_robust` usa **mediana** y descarta outliers (un rayo espurio no colapsa el sector; hacen falta ≥2 rayos para registrar obstáculo). Test lo cubre.
- **«¿Y si el suscriptor no recibe nada?»** → QoS **BEST_EFFORT** (el LiDAR publica así; un suscriptor RELIABLE recibiría cero mensajes en silencio — la trampa #1 de «no hace nada»).
- **«¿Por qué la FSM no gira 180° al ver una caja?»** → en modo `loop_boxes` un obstáculo localizado NO dispara `RECOVER` (180); se maneja con veer/TURN_IN acotado por el guard anti-180 (accum-cap 135°).
- **«¿Cómo evitás contar dos veces la misma caja?»** → dedup por posición en el **mundo** (odom), no en el frame del robot (DEF1c).

---

## DEF5 — Comunicación y trabajo de equipo (4%, 0.8 pts)
- Roles del grupo (rúbrica): R1 Integración · R2 Percepción (box_detector) · R3 FSM · **R4 Datos/Métricas (vos, Henry)**.
- Que **cada integrante** hable de SU parte — el profe evalúa participación equilibrada.
- Vos (R4) dominás las **métricas**: sos quien mejor puede defender DEF3 (VP/FP/FN, las 10 corridas). Preparalo a fondo.
- Sé claro y honesto: si algo no llegó (ej. un caso límite), decilo con criterio («lo detectamos, se corrige así») — vale más que fingir.

---

## Honestidad (léelo)
La defensa la das **vos**. El profe repregunta para ver si entendés de verdad. Esta guía te da el mapa, pero
tenés que **caminar el código** hasta que puedas explicarlo con tus palabras y responder lo inesperado.
Si hay un gap (una métrica que no corriste, un caso que no probaste), preséntalo con criterio, no lo escondas:
«esto lo medimos / esto queda como mejora» — eso es lo que un evaluador riguroso premia.
