#!/usr/bin/env python3
"""
dum_resource_guard.py — Guardia de recursos/térmico del Spark, para DUM (fail-loud).

Construido por NEXUS (seguridad) por orden de William (13-jun-2026): "construye para que
DUM se haga cargo de eso". Cierra el GAP cazado por efecto: no había monitor de CPU/térmico
con alerta, así que el SoC subió + Brave acumuló 60 pestañas SIN que nada gritara (William lo
sintió antes que ningún monitor). Esto hace que el fallo silencioso GRITE.

Diseño (lecciones del 12-13 jun aplicadas):
  • FAIL-LOUD: silencio cuando todo sano; POST al equipo SOLO cuando cruza umbral real.
  • CADENCE-AWARE / anti cry-wolf: re-muestrea ~8s y exige el problema SOSTENIDO en ambas
    muestras (el mismo PID runaway, la temp todavía alta) antes de alertar. Un pico transitorio
    (p.ej. un fan-out de subagentes trabajando) NO dispara falso-positivo.
  • Umbrales CALIBRADOS por efecto: el overheat grave histórico fue 92°C; normal-bajo-carga
    70-83°C. WARN 85 / CRIT 90. Load>1.0/core. Proceso >150% CPU sostenido = runaway.
    Search (grep/find/rg/ugrep) > 30min = la causa #1 de overheat (búsqueda colgada).
  • READ-ONLY del sistema (lee /sys, ps, nvidia-smi; postea al chat). CERO privilegios,
    cero god-cred. NUNCA crashea (exit 0 pase lo que pase).
Corre por timer de DUM (SEAL_AGENT=DUM); postea como DUM = "DUM se hace cargo".
"""
import subprocess, json, os, sys, time, glob, pathlib, urllib.request


TEMP_WARN = 85.0      # °C SoC (overheat grave histórico = 92°C; normal-carga 70-83)
TEMP_CRIT = 90.0
TEMP_SAMPLES = 9      # muestras por corrida (incluye las 2 originales)
TEMP_INTERVAL = 10    # segundos entre muestras -> ventana de ~80 s
LOAD_WARN_RATIO = 1.0     # load1 / cores > 1.0 sostenido = saturado
LOAD_CPU_MIN = 50.0       # load alto SOLO alerta si CPU realmente ocupada (user+sys+irq) >= 50%.
# FIX (ALICE 16-jun, raíz NEXUS): el load-avg cuenta procesos en estado D (I/O-wait, p.ej.
# managers del clúster esperando nodos caídos) que NO consumen CPU. Sin este gate, el load
# cosmético gritaba toda la noche (falso-positivo). El overheat real (ugrep 1177% CPU) sigue
# cazándose porque ahí la CPU SÍ está ocupada. TEMP/RUNAWAY/SEARCH tienen su propio gate aparte.
PROC_CPU_WARN = 150.0     # un solo proceso > 150% CPU sostenido = runaway
PROC_AGE_MIN = 120        # ...pero con al menos 2 min de vida: un arranque quema
                          # CPU por diseno. 31-ago: alertamos sobre seal_infra_watchdog
                          # con "edad 0min" -- la rama de >150% no tenia puerta de edad
                          # y la ventana de 80 s no distingue un arranque de un bucle
                          # trabado. Falso positivo hallado por FABLE y NEXUS en la
                          # PRIMERA alerta que produjo el dato nuevo.
# FIX (NEXUS, 31-ago-2026 02:52, por orden de William "hay que controlarlo").
# QUE LO GENERO: deje un bucle de UN HILO al 99,9% de CPU durante TRES HORAS.
# Subio el SoC ~8 C. Este guardia corrio 18 veces en esa ventana y NO podia verlo:
# 150% exige DOS hilos. Un runaway de un solo hilo llega a 99,9% y es invisible.
# Por eso se agrega un segundo umbral, mas bajo pero con EDAD: un proceso normal
# no sostiene ~1 nucleo entero durante media hora.
PROC_CPU_1HILO = 90.0     # ~1 nucleo clavado
PROC_AGE_1HILO = 1800     # ...durante >= 30 min  -> runaway de un solo hilo
# BUGFIX (NEXUS, test por efecto): el patrón substring "rg " matcheaba "Xorg " (Xo-rg-espacio)
# -> falso-positivo en el display server. Ahora: match EXACTO del comm (nombre de proceso) +
# exige ALTO CPU (un search idle no recalienta; el overheat real fue un grep a 1177% CPU).
SEARCH_COMMS = frozenset({"grep", "ugrep", "rg", "ripgrep", "find", "fd", "ag"})
SEARCH_CPU_MIN = 20.0     # solo un search ALTO-CPU + largo es la causa #1 de overheat
SEARCH_AGE_MIN = 30


def soc_temps():
    out = []
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            t = int(open(z + "/temp").read().strip()) / 1000.0
            ty = open(z + "/type").read().strip()
            out.append((ty, t))
        except Exception:
            pass
    return out


def gpu_temp():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout.strip()
        if out:
            p = out.split(",")
            return float(p[0]), float(p[1])
    except Exception:
        pass
    return None, None


def load_cores():
    try:
        return float(open("/proc/loadavg").read().split()[0]), (os.cpu_count() or 1)
    except Exception:
        return 0.0, 1


def _cpu_snapshot():
    """jiffies (activo_real, total) de /proc/stat línea 'cpu'. activo = user+nice+sys+irq+softirq+steal
    (EXCLUYE idle e iowait → mide CPU computando de verdad, no esperando I/O)."""
    try:
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        # v = user nice system idle iowait irq softirq steal guest guest_nice
        idle = v[3] + (v[4] if len(v) > 4 else 0)            # idle + iowait
        active = sum(v) - idle
        return active, sum(v)
    except Exception:
        return 0, 0


def cpu_busy_pct(snap1, snap2):
    """% de CPU activa real entre dos snapshots. Bajo = idle/I/O-wait; alto = computando."""
    a = snap2[0] - snap1[0]
    t = snap2[1] - snap1[1]
    return (100.0 * a / t) if t > 0 else 0.0


def _seal_agent_de(pid):
    """SEAL_AGENT del environ del proceso, capturado AL DETECTAR. Devuelve None
    si no se puede leer -- nunca inventa un dueno."""
    try:
        raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
    except Exception:
        return None
    for part in raw.split("\0"):
        if part.startswith("SEAL_AGENT="):
            return part.split("=", 1)[1].strip() or None
    return None


def top_procs():
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,%cpu,etimes,comm,cputimes,args", "--sort=-%cpu"],
            capture_output=True, text=True, timeout=8).stdout
        rows = []
        for line in out.splitlines()[1:40]:
            p = line.split(None, 5)
            if len(p) >= 6:
                try:
                    # r[5] = cputimes (segundos de CPU CONSUMIDOS). Se apendo al
                    # final a proposito: r[0..4] conservan su significado y ningun
                    # consumidor existente se rompe.
                    rows.append((p[0], float(p[1]), int(p[2]), p[3], p[5], int(p[4])))
                except Exception:
                    pass
        return rows
    except Exception:
        return []


CD_FILE = "/tmp/dum_spark_alert_cd.json"


_REPO = pathlib.Path(__file__).resolve().parent.parent
_WRITER = _REPO / "scripts" / "seal_send.py"


def _send(msg, channel, to="equipo"):
    """Publica como DUM por el escritor AUTENTICADO. Devuelve (ok, detalle).

    Antes esto hacia un POST crudo a /api/agents/send sin credencial. En modo
    ENFORCE eso da HTTP 401 agent_auth_required -- y el `except Exception` lo
    tragaba, asi que el guardia imprimia "CRITICO -> web_chat + DM William" sin
    haber enviado nada. Medido 31-ago: 45 CRITICO en el journal en 7 dias, CERO
    mensajes con "GUARDIA SPARK" o "EMERGENCIA" en soul_v3.chat_messages.
    Control de que el cero significa algo: DUM publico 94 mensajes en esos
    mismos 7 dias, o sea que podia hablar.  (JARVIS 13-jun, roto; JARVIS 31-ago)

    El detalle vuelve al llamador para que el log NO pueda afirmar un envio que
    no ocurrio: el defecto no fue fallar, fue fallar mintiendo.
    """
    try:
        r = subprocess.run(
            [sys.executable, str(_WRITER), "DUM", to, msg,
             "--channel", channel, "--type", "alert"],
            cwd=str(_REPO), capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return False, f"rc={r.returncode} {(r.stderr or out).strip()[:200]}"
    # Verificacion por EFECTO: exijo el id devuelto, no un rc==0.
    try:
        payload = json.loads(out.splitlines()[-1])
    except Exception:
        return False, f"respuesta no parseable: {out[:200]}"
    if not payload.get("ok") or not payload.get("id"):
        return False, f"rechazado: {out[:200]}"
    return True, str(payload["id"])


def _cooldown_ok(key, cooldown_s):
    """True si la key NO está en cooldown (y registra el envío). Anti-spam por-tipo. JARVIS 13-jun.
    El CRÍTICO usa cooldown corto (5min) — una emergencia no se silencia largo (capa NEXUS)."""
    now = time.time()
    try:
        cd = json.load(open(CD_FILE)) if os.path.exists(CD_FILE) else {}
    except Exception:
        cd = {}
    if now - cd.get(key, 0) < cooldown_s:
        return False
    cd[key] = now
    try:
        json.dump(cd, open(CD_FILE, "w"))
    except Exception:
        pass
    return True


def main():
    # ---- MUESTRA 1 ----
    soc1 = soc_temps()
    load1, cores = load_cores()
    _t1 = time.time()
    cpu1 = _cpu_snapshot()   # para medir CPU-activa real sobre la ventana de re-muestra
    procs1 = top_procs()
    soc_max1 = max([t for _, t in soc1], default=0.0)

    cand = []
    if soc_max1 >= TEMP_WARN:
        cand.append("temp")
    if cores and load1 / cores >= LOAD_WARN_RATIO:
        cand.append("load")
    # El cribado de la muestra 1 usaba `%cpu` = promedio de VIDA, asi que un
    # runaway JOVEN (una hora ocioso y recien ahora quemando) nunca llegaba a ser
    # candidato y el delta no se calculaba jamas. Medido en mi propio banco:
    # vida 2%, delta real 100% -> "OK sano". El veredicto del runaway se decide
    # ENTERO en la etapa de dos muestras, asi que el candidato entra siempre.
    # Costo: 8 s de re-muestra cada 10 min. La ganancia es que el detector
    # detecta. (defecto de cribado hallado por mi banco tras el fix de ALICE)
    runaways1 = procs1
    cand.append("runaway")
    searches = [r for r in procs1
                if r[3] in SEARCH_COMMS and r[1] >= SEARCH_CPU_MIN and r[2] >= SEARCH_AGE_MIN * 60]
    if searches:
        cand.append("search")

    if not cand:
        print("OK — sano, silencio (fail-loud solo en problema)")
        return

    # ---- RE-MUESTRA (confirmar SOSTENIDO, anti falso-positivo transitorio) ----
    time.sleep(8)
    soc2 = soc_temps()
    load2, _ = load_cores()
    _t2 = time.time()
    cpu_busy = cpu_busy_pct(cpu1, _cpu_snapshot())   # % CPU activa real sobre los ~8s
    procs2 = top_procs()
    soc_max2 = max([t for _, t in soc2], default=0.0)

    # Cada alerta lleva (nivel, tipo, texto). Tiering NEXUS: WARN->latidos / CRITICO->web_chat+DM William.
    alerts = []  # (level, atype, text)
    if "temp" in cand:
        # SOSTENIDO POR MAYORIA, no por dos monedas (FABLE 31-ago, medido: el SoC
        # oscila ~11 C en 40 s; con 2 muestras a 8 s el umbral se cruza por azar
        # en LAS DOS direcciones -- grita por un pico y se calla ante un problema
        # real si la segunda muestra cae). Muestreo TEMP_SAMPLES veces y exijo
        # que la MAYORIA supere el umbral. No aflojo 85/90: se calibraron sobre
        # un sobrecalentamiento real de 92 C. Lo que cambia es QUE se mide.
        serie = [soc_max1, soc_max2]
        # La GPU se muestrea EN EL MISMO BUCLE. Antes iba una sola lectura
        # puntual al texto de la alerta, junto a una mediana de 9 del SoC:
        # dos regimenes distintos en la misma linea. Y la carga de GPU es a
        # RAFAGAS -medido por ALICE: 3,4,4,5,5,95 % en 18 s- asi que un punto
        # describe el instante, no la ventana. Es la forma inversa del `%cpu`
        # promedio-de-vida: aquel promedia de mas, este no promedia nada.
        gserie = []
        _gt0, _gu0 = gpu_temp()
        if _gu0 is not None:
            gserie.append((_gt0, _gu0))
        for _ in range(TEMP_SAMPLES - 2):
            time.sleep(TEMP_INTERVAL)
            serie.append(max([t for _, t in soc_temps()], default=0.0))
            _gt, _gu = gpu_temp()
            if _gu is not None:
                gserie.append((_gt, _gu))
        n_warn = sum(1 for v in serie if v >= TEMP_WARN)
        n_crit = sum(1 for v in serie if v >= TEMP_CRIT)
        mayoria = len(serie) // 2 + 1
        soc_med = sorted(serie)[len(serie) // 2]
        if n_warn >= mayoria:
            crit = n_crit >= mayoria
            if gserie:
                _ut = sorted(u for _, u in gserie)
                _tt = sorted(g for g, _ in gserie if g is not None)
                gu = f"mediana {_ut[len(_ut)//2]:.0f}% (rango {min(_ut):.0f}-{max(_ut):.0f}, n={len(_ut)})"
                gt = f"{_tt[len(_tt)//2]:.0f}" if _tt else "n/d"
            else:
                gt, gu = "n/d", "n/d"
            alerts.append(("CRITICO" if crit else "WARN", "temp",
                           f"TEMP {'CRITICO' if crit else 'WARN'} SOSTENIDO: SoC mediana {soc_med:.0f}C "
                           f"({n_warn}/{len(serie)} muestras sobre {TEMP_WARN:.0f}, "
                           f"{n_crit}/{len(serie)} sobre {TEMP_CRIT:.0f}) en {(len(serie)-1)*TEMP_INTERVAL}s, "
                           f"rango {min(serie):.0f}-{max(serie):.0f}C, GPU {gt}C util {gu}"))
        else:
            print(f"TEMP oscilante, no sostenida: {n_warn}/{len(serie)} sobre {TEMP_WARN:.0f} "
                  f"(rango {min(serie):.0f}-{max(serie):.0f}C, mediana {soc_med:.0f}C) -> sin alerta")
    if "load" in cand and cores and load2 / cores >= LOAD_WARN_RATIO:
        if cpu_busy >= LOAD_CPU_MIN:
            alerts.append(("WARN", "load", f"LOAD alto sostenido: {load2:.1f} en {cores} cores ({100*load2/cores:.0f}%), CPU {cpu_busy:.0f}% activa"))
        else:
            print(f"LOAD {load2:.1f} alto pero CPU solo {cpu_busy:.0f}% activa (I/O-wait/D-state, no saturacion) -> sin alerta")
    # CARGA REAL vs PROMEDIO DE VIDA (ALICE, 31-ago, medido).
    # `ps -o %cpu` es TIME/ELAPSED: el promedio de TODA la vida del proceso.
    # Miente en los DOS extremos y por razones opuestas: un proceso JOVEN que
    # arranca quemando marca altisimo; uno VIEJO que quemo horas y ya paro sigue
    # marcando alto durante horas. Re-muestrear no lo arregla -- repetir una
    # medicion sesgada devuelve el mismo sesgo y se SIENTE confirmacion.
    # Carga real = delta de CPU consumida entre las dos muestras / segundos reales.
    _cpu1 = {r[0]: r[5] for r in procs1}
    _dt = _t2 - _t1
    def _cpu_real(r):
        # Sin una ventana REAL no hay porcentaje real: un _dt de milisegundos
        # produce cifras absurdas (mi banco saco 41.527.762 %). Prefiero decir
        # "no se" a publicar un numero imposible -- el `n/d` se ve en la alerta.
        if _dt < 1.0:
            return None
        base = _cpu1.get(r[0])
        if base is None:
            return None                      # no estaba en la muestra 1: no se
        return 100.0 * (r[5] - base) / _dt

    pids1 = {r[0] for r in runaways1}
    sustained = []
    for r in procs2:
        if r[0] not in pids1:
            continue
        real = _cpu_real(r)
        efectivo = r[1] if real is None else real
        if ((efectivo >= PROC_CPU_WARN and r[2] >= PROC_AGE_MIN)
                or (efectivo >= PROC_CPU_1HILO and r[2] >= PROC_AGE_1HILO)):
            sustained.append((r, real))
    for r, real in sustained[:3]:
        # EVIDENCIA CAPTURADA AL DETECTAR (FABLE, 31-ago): para cuando alguien
        # lee la alerta el PID ya murio y no queda nada que investigar. El
        # guardia YA tiene estos datos en la mano cuando decide; no ponerlos en
        # el texto es tirar la prueba. `args` se trunca: ahi viaja el system
        # prompt completo de un agente.
        agente = _seal_agent_de(r[0])
        alerts.append(("WARN", "runaway",
                       f"RUNAWAY sostenido: pid {r[0]} {r[3]}"
                       f"{' [' + agente + ']' if agente else ''} "
                       f"CPU real {('%.0f%%' % real) if real is not None else 'n/d'} "
                       f"(promedio de vida {r[1]:.0f}%), edad {r[2] // 60}min, "
                       f"args: {r[4][:100]}"))
    for r in searches[:3]:
        alerts.append(("WARN", "search", f"SEARCH colgado: pid {r[0]} {r[3]} {r[2]//60}min (causa#1 overheat)"))

    if not alerts:
        print("Pico transitorio, no sostenido en re-muestra -> sin alerta (anti cry-wolf)")
        return

    crit = [a for a in alerts if a[0] == "CRITICO"]
    warn = [a for a in alerts if a[0] == "WARN"]

    # CRITICO -> web_chat (visible) + DM William. Cooldown corto 5min: una emergencia NO se entierra (NEXUS).
    if crit:
        body = "[GUARDIA SPARK / DUM] EMERGENCIA: " + " | ".join(t for _, _, t in crit + warn) + " -- verifiquen YA."
        ckey = "crit:" + "+".join(sorted({a[1] for a in crit}))
        if _cooldown_ok(ckey, 300):
            ok_pub, det_pub = _send(body, "web_chat")
            ok_dm, det_dm = _send(body, "dm:dum:william", to="William")
            # El log dice lo que PASO, no lo que se intento. Si un envio falla,
            # el journal tiene que gritarlo: una emergencia que no sale es una
            # emergencia que no existe para William.
            if ok_pub and ok_dm:
                print(f"CRITICO ENVIADO -> web_chat id={det_pub} · DM id={det_dm}:", body)
            else:
                print(f"CRITICO NO ENTREGADO -> web_chat:{'OK id='+det_pub if ok_pub else 'FALLO '+det_pub}"
                      f" · DM:{'OK id='+det_dm if ok_dm else 'FALLO '+det_dm} :: {body}",
                      file=sys.stderr)
        else:
            print("CRITICO en cooldown corto (<5min) -> no repito aun")
    # WARN puro -> latidos (canal sistema), cooldown 30min por-tipo (JARVIS).
    elif warn:
        body = "[GUARDIA SPARK / DUM] " + " | ".join(t for _, _, t in warn) + " -- verifiquen por efecto."
        wkey = "warn:" + "+".join(sorted({a[1] for a in warn}))
        if _cooldown_ok(wkey, 1800):
            ok_w, det_w = _send(body, "latidos")
            if ok_w:
                print(f"WARN ENVIADO -> latidos id={det_w}:", body)
            else:
                print(f"WARN NO ENTREGADO -> latidos: FALLO {det_w} :: {body}", file=sys.stderr)
        else:
            print("WARN en cooldown (<30min) -> silencio")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("guard error (no crash):", e)
    sys.exit(0)
