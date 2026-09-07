# Integración del planner de Ronda 2 en `maze_solver.py` — guía para JARVIS
**Autora:** ALICE · 2026-07-13 · módulo `maze_planner.py` (probado por efecto, incl. persistencia)

Cierra el hueco MAYOR de la rúbrica: la Ronda 2 «ruta más corta / Time Attack».
El wall-follower reactivo actual queda INTACTO como fallback; el planner solo
se activa en `ronda=2` cuando hay un mapa aprendido en `ronda=1`.

> Archivo a copiar al paquete: `maze_planner.py` → `capytown_granprix_pkg/maze_planner.py`
> (setup.py NO necesita cambios: es un módulo importado, no un ejecutable nuevo.)

## Los 4 hooks (líneas de referencia sobre el `maze_solver.py` revisado)

### 1) Import defensivo (junto a los otros try/except de import, ~L79-90)
```python
try:
    from capytown_granprix_pkg.maze_planner import MazePlanner
    _HAVE_PLANNER = True
except Exception:
    _HAVE_PLANNER = False
```

### 2) En `__init__` (después de leer `self.ronda`, ~L653)
```python
# origin_xy = odom del centro de la celda INICIO (0,0). Con /odom_raw reseteado
# en INICIO es (0.0, 0.0). invert_x/invert_y según orientación real del robot.
self.planner = MazePlanner(origin_xy=(0.0, 0.0)) if _HAVE_PLANNER else None
self.r2_waypoints = None
self.r2_wp_idx = 0
if self.planner is not None and self.ronda == 2:
    # Ronda 2: cargar el mapa aprendido en Ronda 1 y planificar la ruta corta.
    if self.planner.load_map("/tmp/granprix_map.json"):
        self.r2_waypoints = self.planner.plan_waypoints()
        self.get_logger().info(
            f"[PLANNER] R2: ruta más corta = {len(self.r2_waypoints) if self.r2_waypoints else 0} "
            f"waypoints, long_optima={self.planner.optimal_len_cm()} cm")
```

### 3) En `on_odom` (~L695), registrar el mapa durante Ronda 1
```python
# ... después de setear self tener pose (x, y) desde msg ...
if self.planner is not None and self.ronda == 1:
    self.planner.observe_pose(x, y)   # marca aristas libres al cruzar celdas
```

### 4) Al alcanzar META en Ronda 1 → guardar el mapa a disco
Donde hoy se marca `self.meta_reached = True` (fin de la corrida), agregar:
```python
if self.planner is not None and self.ronda == 1:
    self.planner.save_map("/tmp/granprix_map.json")
    self.get_logger().info("[PLANNER] R1: mapa guardado para Ronda 2")
```

## Navegación por waypoints en Ronda 2 (dentro de `tick`, ~L661)
Cuando `self.ronda == 2` y `self.r2_waypoints`, en vez del wall-following puro,
apuntar al waypoint actual con el control a-punto que YA existe en el archivo
(`ang_diff` / `heading_hold_cmd` / odom-yaw):
```python
if self.ronda == 2 and self.r2_waypoints:
    tx, ty = self.r2_waypoints[self.r2_wp_idx]
    dx, dy = tx - self.x, ty - self.y
    dist = math.hypot(dx, dy)
    if dist < 0.12:                        # llegó al waypoint → siguiente
        self.r2_wp_idx = min(self.r2_wp_idx + 1, len(self.r2_waypoints) - 1)
    else:
        desired_yaw = math.atan2(dy, dx)
        # reutilizar heading_hold_cmd/ang_diff para girar hacia desired_yaw y avanzar;
        # el emergency-stop y PARE (cámara) SIGUEN mandando por encima (seguridad).
        ...
    # PARE de la cámara y emerg_dist se evalúan IGUAL que en R1 (no bypasear).
```
**Regla de arbitraje intacta:** el PARE de la cámara y el emergency-stop del LiDAR
siguen teniendo prioridad; el planner solo decide la RUTA, no anula la seguridad.

## Métrica de la rúbrica (bonus)
`planner.optimal_len_cm()` da `long_optima_cm` real → cerrar el campo de
`metricas_granprix.csv` que hoy se carga a mano (línea ~773 del write_metrics).

## Bonus 2: conteo automático de colisiones (`collision_counter.py`)
Cierra el campo `colisiones` del CSV (hoy manual, `colisiones_manual=-1`).
```python
from capytown_granprix_pkg.collision_counter import CollisionCounter
# en __init__:
self.cc = CollisionCounter(touch_dist=0.10, clear_dist=0.20)   # touch < emerg_dist(0.15)
# en tick(), con el rango frontal ya calculado (s.front):
self.cc.update(s.front, self.now_s())
# en write_metrics(): usar self.cc.count si colisiones_manual < 0
colisiones = self.cc.count if self.p.get("colisiones_manual", -1) < 0 else int(self.p["colisiones_manual"])
```
Es un PROXY honesto (breaches de frente-cerca con debounce), mejor que el -1;
el conteo oficial de la competencia lo sigue diciendo el árbitro/video.

## Fallback (importante)
Si `load_map` no encuentra mapa (no se corrió R1, o R2 con layout nuevo),
`plan_waypoints()` devuelve `None` → el FSM sigue con el wall-following normal.
Cero regresión: sin planner, se comporta exactamente como hoy.
