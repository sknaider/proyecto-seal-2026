#!/usr/bin/env python3
"""
spectre_dashboard.py — Tablero DEDICADO e interactivo de SPECTRE (pedido de William 16-jul).
Panel para: ver el cerebro actual, CAMBIAR de cerebro (GLM cluster / Ollama) con un click,
y APAGAR/PRENDER el cerebro grande (GLM) para liberar RAM.

Sirve el HTML + la API de control en :8093. El swap escribe /tmp/spectre_brain.conf (2 líneas:
url, model) que el shim de SPECTRE (:8092) lee live. Apagar/prender GLM = ssh a spark-2.
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

BRAIN_CONF = "/tmp/spectre_brain.conf"
SHIM_URL = "http://127.0.0.1:8092"
GLM_URL = "http://192.168.68.70:8090/v1/chat/completions"
OLLAMA_BASE = "http://127.0.0.1:11434"
PORT = 8093
CHAT_AUTH_URL = "http://127.0.0.1:8765/api/auth/me"
CONTROL_ROLES = {"admin", "superuser"}
CONTROL_ORIGIN_PORTS = {"3001", "8093"}
SPARKS = ("spark-2", "spark-3", "spark-4")   # whitelist — nunca va input crudo al shell
# IPs REALES del cluster (ALICE 16-jul): el chequeo TCP debe usar estas y NO el
# hostname. /etc/hosts mapea spark-2→10.0.0.2 (IP vieja, muerta) mientras que
# ssh_config apunta a 192.168.68.70. Por eso `ssh spark-2` andaba pero el socket
# de Python resolvía por /etc/hosts → timeout → el panel mostraba "apagado" en falso.
SPARK_IPS = {"spark-2": "192.168.68.70", "spark-3": "192.168.68.71", "spark-4": "192.168.68.72"}
RELEASE_PRODUCTS = {
    "core": {
        "label": "Core básico",
        "repo": "soul-framework",
        "tag_pattern": r"v?(?P<version>\d+\.\d+\.\d+)",
        "asset_template": "soul_framework-{version}-py3-none-any.whl",
        "asset_suffix": ".whl",
        "fallback": "https://github.com/sknaider/soul-framework/releases/latest",
        "private": False,
    },
    "platform": {
        "label": "Platform completo",
        "repo": "soul-platform",
        "tag_pattern": r"v(?P<version>\d+\.\d+\.\d+)",
        "asset_template": "SOUL-Platform-{version}-Windows.zip",
        "asset_suffix": ".zip",
        "fallback": "https://github.com/sknaider/soul-platform/releases/latest",
        "private": False,
    },
}
RELEASE_CACHE_TTL = 900
_release_cache = {}
_release_cache_lock = threading.Lock()


def _trusted_github_release_url(url, repo, *, tag="", asset_name=""):
    """Accept only one canonical GitHub release or asset URL."""
    try:
        parsed = urlsplit(str(url))
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    expected = f"/sknaider/{repo}/releases/tag/{tag}"
    if asset_name:
        expected = f"/sknaider/{repo}/releases/download/{tag}/{asset_name}"
    return parsed.path == expected


def _release_metadata(product, payload):
    """Turn GitHub release JSON into a small, host-validated download contract."""
    config = RELEASE_PRODUCTS[product]
    repo = config["repo"]
    if not isinstance(payload, dict):
        payload = {}
    tag = str(payload.get("tag_name") or "")
    match = re.fullmatch(config["tag_pattern"], tag)
    valid_release = (
        not payload.get("draft") and not payload.get("prerelease") and bool(match)
    )
    release_url = str(payload.get("html_url") or "")
    if not valid_release or not _trusted_github_release_url(release_url, repo, tag=tag):
        release_url = config["fallback"]
    installer_url = ""
    asset_api_path = ""
    asset_digest = ""
    asset_size = 0
    version = match.group("version") if match else ""
    expected_name = config["asset_template"].format(version=version)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    for asset in assets if valid_release else []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        candidate = str(asset.get("browser_download_url") or "")
        if (
            name == expected_name
            and _trusted_github_release_url(candidate, repo, tag=tag, asset_name=expected_name)
        ):
            if config["private"]:
                asset_id = asset.get("id")
                digest = str(asset.get("digest") or "")
                size = asset.get("size")
                if (
                    not isinstance(asset_id, int)
                    or asset_id <= 0
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or not isinstance(size, int)
                    or not 0 < size <= 250 * 1024 * 1024
                ):
                    continue
                installer_url = f"/api/release-download/{product}"
                asset_api_path = f"repos/sknaider/{repo}/releases/assets/{asset_id}"
                asset_digest = digest.removeprefix("sha256:")
                asset_size = size
            else:
                installer_url = candidate
            break
    return {
        "product": product,
        "label": config["label"],
        "version": f"v{version}" if valid_release else "latest",
        # A valid release page is useful as metadata, but it is not a direct
        # package download.  When the asset is missing or untrusted, retain the
        # stable, repository-scoped fallback instead of silently presenting the
        # release page as an installer.
        "installer_url": installer_url or config["fallback"],
        "release_url": release_url,
        "direct": bool(installer_url),
        "asset_name": expected_name if installer_url else "",
        "_asset_api_path": asset_api_path,
        "_asset_digest": asset_digest,
        "_asset_size": asset_size,
    }


def latest_release(product):
    """Resolve the latest direct installer, cached so panel refreshes do not flood GitHub."""
    if product not in RELEASE_PRODUCTS:
        raise ValueError(f"producto de release inválido: {product}")
    now = time.monotonic()
    with _release_cache_lock:
        cached = _release_cache.get(product)
        if cached and now - cached[0] < RELEASE_CACHE_TTL:
            return dict(cached[1])
    config = RELEASE_PRODUCTS[product]
    try:
        if config["private"]:
            completed = subprocess.run(
                [
                    "gh", "api",
                    f"repos/sknaider/{config['repo']}/releases?per_page=100",
                ],
                capture_output=True,
                check=True,
                text=True,
                timeout=8,
            )
            payloads = json.loads(completed.stdout)
            if not isinstance(payloads, list):
                raise ValueError("GitHub releases response is not a list")
            result = next(
                (
                    candidate
                    for payload in payloads
                    if (candidate := _release_metadata(product, payload))["direct"]
                ),
                _release_metadata(product, {}),
            )
        else:
            req = urllib.request.Request(
                f"https://api.github.com/repos/sknaider/{config['repo']}/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "SEAL-Panel-SOUL",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                result = _release_metadata(product, json.load(response))
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ):
        # A transient GitHub failure must not downgrade a known-good direct
        # package.  Reuse the last validated value even after its refresh TTL.
        result = dict(cached[1]) if cached else {
            "product": product,
            "label": config["label"],
            "version": "latest",
            "installer_url": config["fallback"],
            "release_url": config["fallback"],
            "direct": False,
        }
    with _release_cache_lock:
        _release_cache[product] = (now, result)
    return dict(result)


def releases_status():
    return [
        {key: value for key, value in latest_release(product).items() if not key.startswith("_")}
        for product in RELEASE_PRODUCTS
    ]


def _sh(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"err: {e}"


def current_brain():
    try:
        lines = open(BRAIN_CONF).read().splitlines()
        url = lines[0].strip() if lines else GLM_URL
        model = lines[1].strip() if len(lines) > 1 else "glm"
    except Exception:
        url, model = GLM_URL, "glm"
    return url, model


def brain_healthy(url):
    try:
        h = url.replace("/chat/completions", "/models")
        with urllib.request.urlopen(h, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def list_brains():
    brains = [{"name": "GLM-5.2-abliterated (clúster 3 Sparks)", "url": GLM_URL, "model": "glm",
               "kind": "cluster"}]
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=4) as r:
            for m in json.load(r).get("models", []):
                n = m["name"]
                if "minilm" in n or "embed" in n:  # no sirven de cerebro chat
                    continue
                brains.append({"name": f"{n} (Ollama local)",
                               "url": f"{OLLAMA_BASE}/v1/chat/completions", "model": n,
                               "kind": "ollama"})
    except Exception:
        pass
    return brains


def spark_off(node):
    """Apaga (poweroff) un Spark. node DEBE estar en la whitelist SPARKS — nunca al shell crudo.
    La conexión ssh se corta cuando el nodo se apaga: eso es ÉXITO, no error."""
    if node not in SPARKS:
        return False, f"nodo inválido: {node}"
    # -f = background el poweroff para que ssh devuelva antes de que muera la conexión
    _sh(f"ssh -o ConnectTimeout=6 -o BatchMode=yes {node} "
        "'sudo systemctl poweroff -i 2>/dev/null || sudo poweroff 2>/dev/null &' ", timeout=10)
    return True, f"{node}: orden de apagado enviada — se está durmiendo. 💤"


WAKE_SCRIPT = "/home/dadito/IA/proyecto-seal/tools/wake_spark.py"


def spark_on(node):
    """Despierta un Spark vía WoL (script de JARVIS). node DEBE estar en la whitelist —
    va como argv[], NUNCA al shell. Mapea exit codes a mensajes claros."""
    if node not in SPARKS:
        return False, f"nodo inválido: {node}"
    try:
        r = subprocess.run(["python3", WAKE_SCRIPT, node],
                           capture_output=True, text=True, timeout=15)
    except Exception as e:
        return False, f"{node}: no pude invocar el wake ({e})"
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return True, f"{node}: 🔌 magic packet enviado. Si el WoL está habilitado, despierta en ~30-60s — mirá el semáforo."
    if r.returncode == 2:
        return False, (f"{node}: WoL NO disponible — los Spark van por WiFi, que no soporta Wake-on-LAN "
                       "(confirmado por JARVIS+NEXUS). Para prender remoto habría que CABLEARLOS por Ethernet "
                       "primero; ahí JARVIS arma el WoL. Por ahora: encendido FÍSICO.")
    return False, f"{node}: {out or 'error de wake'}"


# El CENTRAL (.200) es ESTE host (donde corre el panel, el chat y TODOS los agentes).
# Apagarlo tumba todo y NO tiene encendido remoto (el panel muere con él). Por eso va con
# doble confirmación: token exacto obligatorio en el backend además del prompt del front.
CENTRAL_OFF_TOKEN = "APAGAR-CENTRAL"


def central_off(confirm):
    """Apaga el CENTRAL (.200) = este mismo host. Requiere el token exacto (2da barrera del
    backend). Poweroff LOCAL en background con un respiro para que la respuesta HTTP salga
    antes de que la máquina muera."""
    if confirm != CENTRAL_OFF_TOKEN:
        return False, "🔒 Confirmación inválida — el central NO se apaga sin la confirmación exacta."
    _sh("(sleep 1; sudo systemctl poweroff -i 2>/dev/null || sudo poweroff 2>/dev/null) &", timeout=6)
    return True, ("CENTRAL .200: apagándose en ~1s 💤 — se caen el chat, TODOS los agentes y este panel. "
                  "Reencender: botón FÍSICO del equipo (no hay encendido remoto).")


# Servicios de modelos CONTROLABLES (William 16-jul: "botones para pausar los vLLM/llama
# activos y no tener choques de RAM"). Whitelist: la clave mapea a un unit systemd FIJO —
# nunca va input crudo al shell. Pausar=stop, Reanudar=start (reversible, NO mata).
# 2 mecanismos (JARVIS): kind=systemd → systemctl stop/start; kind=docker → docker stop/start.
# El 480B es 1 service en spark-2 (head) + workers RPC en .71/.72: parar el head pausa TODO
# el cluster de una (NO nodo-por-nodo). El vLLM del PoC será un contenedor docker (se agrega
# cuando esté su nombre). Whitelist por clave — nunca input crudo al shell.
MODEL_SERVICES = [
    {"key": "coder", "label": "Qwen3-Coder-480B (llama.cpp)", "kind": "systemd", "crit": "safe",
     "target": "seal-llama-cluster.service", "node": "spark-2",
     "desc": "Modelo GRANDE de asistencia de código (Qwen3-Coder, 480B) en los 3 Spark vía "
             "llama.cpp RPC (head spark-2 + workers .71/.72). Uso: programación pesada. ~87GB/nodo.",
     "note": "Pausar libera los 3 Spark (~87GB/nodo) — SEGURO si nadie está codeando con él. "
             "Reanudar recarga el 480B (~varios min); el head re-conecta los workers."},
    {"key": "dum", "label": "gemma4-dum — cerebro del GUARDIÁN DUM", "crit": "critical",
     "statuskind": "port", "host": "127.0.0.1", "port": 8899, "node": ".200",
     "desc": "Modelo (gemma-4-e2b, :8899) que le da vista al GUARDIÁN de seguridad DUM. Corre 24/7.",
     "note": "CRÍTICO — NO se pausa: dejaría CIEGO al Guardián de seguridad del equipo."},
    {"key": "minilm", "label": "Ollama all-minilm — embeddings de MEMORIA", "crit": "critical",
     "statuskind": "port", "host": "127.0.0.1", "port": 11434, "node": ".200",
     "desc": "Modelo de embeddings (:11434) que sostiene la MEMORIA SEAL (búsqueda vectorial de recuerdos).",
     "note": "CRÍTICO — NO se pausa: rompería el recall de memoria de todos los agentes."},
    # Se agrega cuando el vLLM del PoC tenga contenedor:
    # {"key": "vllm-poc", "label": "vLLM PoC (:8000)", "kind": "docker", "crit": "safe",
    #  "target": "<container>", "node": "spark-2", "desc": "...", "note": "..."},
]
_SVC_BY_KEY = {s["key"]: s for s in MODEL_SERVICES}


def service_status(svc):
    """Estado del servicio. statuskind=port → TCP; kind=docker → docker; si no → systemctl."""
    if svc.get("statuskind") == "port":
        import socket
        try:
            with socket.create_connection((svc["host"], svc["port"]), timeout=1.5):
                return "running"
        except Exception:
            return "stopped"
    if svc.get("kind") == "docker":
        cmd = (f"docker inspect -f '{{{{.State.Running}}}}' {svc['target']} 2>/dev/null")
        out = _sh(f"ssh -o ConnectTimeout=5 -o BatchMode=yes {svc['node']} \"{cmd}\"", timeout=10)
        s = out.strip().splitlines()[-1].strip().lower() if out.strip() else ""
        return "running" if s == "true" else ("stopped" if s == "false" else "?")
    out = _sh(f"ssh -o ConnectTimeout=5 -o BatchMode=yes {svc['node']} "
              f"'systemctl is-active {svc['target']} 2>/dev/null'", timeout=10)
    s = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if s == "active":
        return "running"
    if s in ("inactive", "failed", "dead"):
        return "stopped"
    return "?"


def services_status():
    return [{"key": s["key"], "label": s["label"], "kind": s.get("kind", "-"), "node": s["node"],
             "crit": s.get("crit", "safe"), "desc": s.get("desc", ""), "note": s.get("note", ""),
             "state": service_status(s)} for s in MODEL_SERVICES]


def service_ctl(key, action):
    """Pausa (stop) o reanuda (start) un servicio whitelisteado. action ∈ {pause,resume}."""
    svc = _SVC_BY_KEY.get(key)
    if not svc:
        return False, f"servicio inválido: {key}"
    if action not in ("pause", "resume"):
        return False, f"acción inválida: {action}"
    if svc.get("crit") == "critical":   # doble seguridad: el backend NUNCA pausa un crítico
        return False, f"🔒 {svc['label']} es CRÍTICO — no se pausa desde el panel. {svc.get('note','')}"
    if svc.get("kind") == "docker":
        verb = "stop" if action == "pause" else "start"
        _sh(f"ssh -o ConnectTimeout=6 -o BatchMode=yes {svc['node']} "
            f"'docker {verb} {svc['target']}'", timeout=30)
    else:  # systemd
        verb = "stop" if action == "pause" else "start"
        _sh(f"ssh -o ConnectTimeout=6 -o BatchMode=yes {svc['node']} "
            f"'sudo systemctl {verb} {svc['target']}'", timeout=30)
    if action == "pause":
        return True, f"{svc['label']}: pausado (RAM liberada). Reanudalo cuando lo necesites."
    return True, f"{svc['label']}: reanudando — {svc.get('note','')}"


def spark_alive(node):
    """Encendido? Método FIABLE = TCP al puerto 22 (ping NO sirve: spark-2 filtra ICMP).
    SYN-ACK (open) = encendido; timeout/refused = apagado."""
    if node not in SPARKS:
        return False
    import socket
    target = SPARK_IPS.get(node, node)   # IP real, no el hostname (evita /etc/hosts viejo)
    try:
        with socket.create_connection((target, 22), timeout=1.5):
            return True
    except Exception:
        return False


def sparks_status():
    return {n: spark_alive(n) for n in SPARKS}


# ── Temperatura (William 16-jul: "agregar temperatura para saber cómo está") ────────
# Fuentes VERIFICADAS por efecto en los 4 GB10 (16-jul): GPU = nvidia-smi
# (query-gpu=temperature.gpu, °C entero); PLACA = /sys/class/thermal/thermal_zone*/temp
# (tipo acpitz, en mili-°C → /1000). Tomamos el MÁXIMO de las zonas acpitz: el punto más
# caliente es el que manda para saber si el equipo sufre, no el promedio (que lo diluye).
#
# Umbrales: heurística de operación, NO specs de NVIDIA. Medidos en reposo: GPU 39-50°C,
# placa 40-55°C. <60 fresco · 60-74 tibio · >=75 caliente (mirar). El GB10 throttlea bastante
# más arriba; el ámbar es para ENTERARSE temprano, no una alarma de daño.
TEMP_WARN, TEMP_HOT = 60, 75

# Un solo comando por nodo → GPU, placa y RAM libre en UNA pasada (antes: 1 ssh por nodo
# SOLO para la RAM, secuencial). Formato de salida: 3 líneas g=/t=/r=.
_STATS_CMD = (
    "echo g=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null | head -1); "
    "echo t=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1); "
    "echo r=$(free -g | awk '/Mem/{print $7}')"
)


def _parse_stats(out):
    """Parsea g=/t=/r=. Cada campo cae a None por separado: si la GPU no responde pero la
    placa sí, mostramos la placa en vez de tirar el nodo entero a '?'."""
    gpu = board = ram = None
    for line in (out or "").splitlines():
        line = line.strip()
        try:
            if line.startswith("g=") and line[2:].strip().isdigit():
                gpu = int(line[2:].strip())
            elif line.startswith("t=") and line[2:].strip().isdigit():
                board = int(line[2:].strip()) // 1000   # mili-°C → °C
            elif line.startswith("r=") and line[2:].strip().isdigit():
                ram = int(line[2:].strip())
        except Exception:
            pass
    return gpu, board, ram


def _node_stats(node):
    """Stats de UN nodo. El CENTRAL (.200) es ESTE host → se lee local, sin ssh (no depende
    de tener llave contra uno mismo). Los Spark: ssh con la whitelist, nunca input crudo."""
    if node == "central":
        gpu, board, ram = _parse_stats(_sh(_STATS_CMD, timeout=8))
        return {"node": "central", "label": "CENTRAL (.200)", "alive": True,
                "gpu": gpu, "board": board, "ram": ram}
    if node not in SPARKS:
        return {"node": node, "label": node, "alive": False, "gpu": None, "board": None, "ram": None}
    if not spark_alive(node):   # apagado → ni intentamos el ssh (colgaría hasta el timeout)
        return {"node": node, "label": node, "alive": False, "gpu": None, "board": None, "ram": None}
    # shlex.quote OBLIGATORIO, no cosmético: con comillas DOBLES el shell LOCAL expande los
    # $(nvidia-smi ...) antes de que ssh salga, y el Spark devuelve la temperatura del CENTRAL
    # como si fuera suya → los 4 nodos idénticos (bug real cazado el 16-jul en el test previo
    # al deploy: 4 nodos "51°/54°/88GB" y barrido de 0.4s = imposible con 3 ssh).
    out = _sh(f"ssh -o ConnectTimeout=5 -o BatchMode=yes {node} {shlex.quote(_STATS_CMD)}", timeout=12)
    gpu, board, ram = _parse_stats(out)
    return {"node": node, "label": node, "alive": True, "gpu": gpu, "board": board, "ram": ram}


def nodes_status():
    """Los 4 nodos EN PARALELO. Antes la RAM se juntaba con 3 ssh secuenciales (~hasta 30s
    en el peor caso) y bloqueaba el refresh; ahora es 1 ssh por nodo, todos a la vez →
    el panel tarda lo que el nodo MÁS lento, no la suma."""
    from concurrent.futures import ThreadPoolExecutor
    order = ["central"] + list(SPARKS)
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(_node_stats, order))


def cluster_ram_free(nodes=None):
    """SUMA la RAM libre de los 3 Spark (spark-2/3/4) = el clúster real. El central NO cuenta:
    no sirve al GLM. Reusa las stats ya juntadas si se las pasan (evita re-sshear)."""
    nodes = nodes if nodes is not None else nodes_status()
    vals = [n["ram"] for n in nodes if n["node"] in SPARKS and n.get("ram") is not None]
    return str(sum(vals)) if vals else "?"


LEGACY_PAGE = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Panel SOUL</title><style>
/* Bordes/espaciado UNIFICADOS (William 16-jul: "ordena los bordes bien ordenado"):
   1 sola escala de radios (12 tarjeta · 8 fila/control) y 1 solo color de borde
   (--bd, --bd-hot para lo peligroso). Nada de bordes sueltos inline. */
:root{--bd:#30363d;--bd-hot:#8b3a3a;--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--mut:#8b949e;
 --ok:#3fb950;--warn:#d29922;--hot:#f85149}
*{box-sizing:border-box;font-family:system-ui,sans-serif}
body{margin:0;background:var(--bg);color:var(--fg);padding:20px}
.wrap{max-width:640px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px;margin-bottom:16px}
.lbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.big{font-size:20px;font-weight:600;margin:4px 0}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;flex:none}
.on{background:var(--ok)}.off{background:var(--hot)}
select,button{font-size:15px;padding:10px 14px;border-radius:8px;border:1px solid var(--bd);
 background:#21262d;color:var(--fg);cursor:pointer}
select{width:100%;margin-bottom:10px}
button{font-weight:600}.b1{background:#238636;border-color:#238636}
.b2{background:#8957e5;border-color:#8957e5}.b3{background:#da3633;border-color:#da3633}
button:hover{filter:brightness(1.15)}button:disabled{opacity:.5;cursor:wait}
.row{display:flex;gap:10px;flex-wrap:wrap}.row button{flex:1}
/* fila = el ladrillo comun de nodos y servicios: mismo borde, mismo radio, mismo aire */
.item{border:1px solid var(--bd);border-radius:8px;padding:10px 12px;margin:8px 0}
.item.crit{border-color:var(--bd-hot)}
.item-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.nm{display:flex;align-items:center;min-width:0}
/* temperaturas: tabular-nums para que los digitos no bailen entre refresh */
.temps{display:flex;gap:6px;flex-wrap:wrap}
.tmp{font-size:12px;font-variant-numeric:tabular-nums;padding:3px 8px;border-radius:8px;
 border:1px solid var(--bd);color:var(--mut);white-space:nowrap}
.t-ok{color:var(--ok);border-color:#2ea04326}.t-warn{color:var(--warn);border-color:#d2992240}
.t-hot{color:var(--hot);border-color:#f8514966;font-weight:700}
#msg{margin-top:12px;font-size:14px;min-height:20px}
</style></head><body><div class=wrap>
<h1>🧠 Panel SOUL</h1>
<div class=sub>Cerebro de SPECTRE, temperatura y energía del clúster. Su alma y memorias persisten siempre.</div>

<div class=card>
 <div class=lbl>Cerebro actual</div>
 <div class=big id=cur>cargando…</div>
 <div id=st></div>
 <div class=lbl style=margin-top:10px>RAM libre del clúster</div>
 <div id=ram>…</div>
</div>

<div class=card>
 <div class=lbl>🌡️ Estado y temperatura de los nodos</div>
 <div class=sub style="margin:6px 0 0" id=tleg><b>GPU</b> (nvidia-smi) y <b>placa</b> (el punto más caliente del equipo).</div>
 <div id=nodes style="margin:4px 0">chequeando…</div>
</div>

<div class=card>
 <div class=lbl>Cambiar cerebro</div>
 <select id=sel></select>
 <button class=b2 onclick=doSwitch()>🔄 Cambiar a este cerebro</button>
</div>

<div class=card>
 <div class=lbl>Encender / Apagar el cerebro grande (GLM en el clúster)</div>
 <div class=row style=margin-top:8px>
  <button class=b1 onclick=doStart()>▶️ Prender GLM</button>
  <button class=b3 onclick=doStop()>⏹️ Apagar GLM (libera ~321GB)</button>
 </div>
</div>

<div class=card>
 <div class=lbl>Servicios de modelos (pausá para liberar RAM)</div>
 <div id=services style="margin:8px 0 4px">chequeando…</div>
</div>

<div class=card>
 <div class=lbl>Energía de los Sparks</div>
 <div class=lbl style="margin-top:8px">Prender los Sparks (Wake-on-LAN)</div>
 <div class=sub style="margin:6px 0 10px">⚠️ WoL no disponible por ahora: los Spark van por WiFi (no soporta WoL). Encendido FÍSICO hasta cablearlos por Ethernet.</div>
 <div class=row>
  <button class=b1 onclick="doSparkOn('spark-2')">🔌 spark-2</button>
  <button class=b1 onclick="doSparkOn('spark-3')">🔌 spark-3</button>
  <button class=b1 onclick="doSparkOn('spark-4')">🔌 spark-4</button>
 </div>
 <button class=b1 style="width:100%;margin-top:10px" onclick="doSparkOn('all')">🔌 Prender los 3 Sparks</button>

 <div class=lbl style=margin-top:16px>Dormir / apagar los Sparks (ahorro de energía)</div>
 <div class=sub style="margin:6px 0 10px">Apaga la máquina entera. Para reencender: botón físico o WoL (arriba).</div>
 <div class=row>
  <button class=b3 onclick="doSpark('spark-2')">💤 spark-2</button>
  <button class=b3 onclick="doSpark('spark-3')">💤 spark-3</button>
  <button class=b3 onclick="doSpark('spark-4')">💤 spark-4</button>
 </div>
 <button class=b3 style="width:100%;margin-top:10px" onclick="doSpark('all')">😴 Apagar los 3 Sparks (un click)</button>
</div>

<div class=card style="border-color:var(--bd-hot);background:#1a1113">
 <div class=lbl style="color:var(--hot)">🚨 Zona peligrosa — Apagar el CENTRAL (.200)</div>
 <div class=sub style="margin:6px 0 10px">Este es el equipo donde viven el <b>chat</b>, <b>TODOS los agentes</b> y <b>este mismo panel</b>. Si lo apagás se cae TODO y sólo se reenciende con el <b>botón FÍSICO</b> del equipo — no hay encendido remoto. Pide doble confirmación.</div>
 <button class=b3 style="width:100%" onclick="doCentralOff()">🚨 Apagar el CENTRAL (.200)</button>
</div>
<div id=msg></div>
</div><script>
async function api(p,m){const r=await fetch(p,{method:m||'GET'});return r.json()}
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
function msg(t){document.getElementById('msg').textContent=t}
async function refresh(){
 const s=await api('/api/status');
 document.getElementById('cur').textContent=s.name||s.model;
 document.getElementById('st').innerHTML='<span class="dot '+(s.healthy?'on':'off')+'"></span>'+(s.healthy?'activo':'apagado');
 const sel=document.getElementById('sel');const b=await api('/api/brains');
 sel.innerHTML=b.map(x=>'<option value=\\''+encodeURIComponent(JSON.stringify(x))+'\\'>'+x.name+'</option>').join('');
 refreshNodes();
 refreshServices();
}
// clase de color por umbral — los umbrales los manda el BACKEND (una sola fuente de
// verdad: si se ajustan en Python, el front sigue solo, no hay 2 numeros que se peleen)
function tclass(v,w,h){if(v===null||v===undefined)return '';return v>=h?'t-hot':(v>=w?'t-warn':'t-ok')}
function tbadge(label,v,w,h){
 if(v===null||v===undefined)return '<span class=tmp>'+label+' —</span>';
 return '<span class="tmp '+tclass(v,w,h)+'">'+label+' '+v+'°</span>';
}
async function refreshNodes(){
 try{const d=await api('/api/nodes');const w=d.warn,h=d.hot;
  // leyenda derivada de los MISMOS umbrales que pintan los badges — si se tocan en Python,
  // el texto sigue solo. Hardcodearla la volvia una 2da fuente de verdad que miente callada
  // (cazado el 16-jul probando con umbrales bajados: badges rojos y leyenda diciendo "<60").
  document.getElementById('tleg').innerHTML='<b>GPU</b> (nvidia-smi) y <b>placa</b> (el punto más caliente del equipo). '+
   'Verde &lt;'+w+'° · ámbar '+w+'-'+(h-1)+'° · rojo '+h+'°+';
  document.getElementById('ram').textContent=(d.ram_free||'?')+' GB libres';
  document.getElementById('nodes').innerHTML=(d.nodes||[]).map(n=>{
   const hot=(n.gpu>=h)||(n.board>=h);
   return '<div class="item'+(hot?' crit':'')+'">'+
    '<div class=item-hd>'+
     '<div class=nm><span class="dot '+(n.alive?'on':'off')+'"></span><b>'+n.label+'</b>'+
      '<span class=sub style="margin:0 0 0 8px">'+(n.alive?'🟢 encendido':'🔴 apagado')+'</span></div>'+
     (n.alive
       ? '<div class=temps>'+tbadge('🌡️ GPU',n.gpu,w,h)+tbadge('🔧 placa',n.board,w,h)+
         (n.ram!==null&&n.ram!==undefined?'<span class=tmp>🧠 '+n.ram+' GB</span>':'')+'</div>'
       : '<span class=sub>sin lectura</span>')+
    '</div></div>';
  }).join('');
 }catch(e){document.getElementById('nodes').textContent='no pude chequear los nodos';}
}
async function refreshServices(){
 try{const s=await api('/api/services');
  if(!s.length){document.getElementById('services').innerHTML='<span class=sub>No hay servicios de modelos registrados.</span>';return;}
  document.getElementById('services').innerHTML=s.map(x=>{
   const on=x.state==='running';const dot=on?'on':(x.state==='stopped'?'off':'');
   const crit=x.crit==='critical';
   const ctrl = crit
     ? '<span style="color:#f85149;font-weight:600;font-size:13px;white-space:nowrap">🔒 CRÍTICO — no pausar</span>'
     : (on
        ? '<button class=b3 style="padding:7px 12px" onclick="doService(\\''+x.key+'\\',\\'pause\\')">⏸️ Pausar</button>'
        : '<button class=b1 style="padding:7px 12px" onclick="doService(\\''+x.key+'\\',\\'resume\\')">▶️ Reanudar</button>');
   return '<div class="item'+(crit?' crit':'')+'">'+
     '<div class=item-hd>'+
      '<div class=nm><span class="dot '+dot+'"></span><b>'+x.label+'</b> '+
      '<span class=sub style="margin:0 0 0 6px">('+(on?'🟢 activo':(x.state==='stopped'?'🔴 pausado':'❓'))+' · '+x.node+')</span></div>'+
      ctrl+'</div>'+
     '<div class=sub style="margin-top:6px">'+(x.desc||'')+'</div>'+
     (x.note?'<div class=sub style="margin-top:4px;color:'+(crit?'#f85149':'#d29922')+'">'+(crit?'⛔ ':'⚠️ ')+x.note+'</div>':'')+
    '</div>';
  }).join('');
 }catch(e){document.getElementById('services').textContent='no pude chequear los servicios';}
}
async function doService(key,action){
 const s=await api('/api/services');const x=(s||[]).find(v=>v.key===key)||{};
 if(action==='pause'){
  if(!confirm('¿PAUSAR "'+(x.label||key)+'"?\\n\\n'+(x.desc||'')+'\\n\\n'+(x.note||'')+'\\n\\n¿Seguro?'))return;
 }
 msg((action==='pause'?'Pausando ':'Reanudando ')+(x.label||key)+'…');
 const r=await post('/api/service_ctl',{key:key,action:action});msg(r.msg||'listo');setTimeout(refreshServices,3000);
}
async function doSwitch(){const sel=document.getElementById('sel');const x=JSON.parse(decodeURIComponent(sel.value));
 msg('Cambiando cerebro a '+x.name+'…');const r=await post('/api/switch',x);msg(r.msg||'listo');refresh()}
async function doStop(){if(!confirm('Apagar el GLM y liberar la RAM? SPECTRE quedará sin cerebro hasta prenderlo.'))return;
 msg('Apagando GLM…');const r=await post('/api/stop');msg(r.msg||'listo');setTimeout(refresh,2000)}
async function doStart(){msg('Prendiendo GLM… (tarda ~15-20 min en cargar los 319GB)');const r=await post('/api/start');msg(r.msg||'lanzado');setTimeout(refresh,3000)}
async function doSpark(n){const q=n==='all'?'¿Apagar los 3 Sparks? Se apagan de verdad; para prenderlos apretás el botón físico.':'¿Apagar '+n+'? Se apaga de verdad; para prenderlo apretás el botón físico.';
 if(!confirm(q))return;msg('Enviando apagado a '+n+'…');const r=await post('/api/spark_off',{node:n});msg(r.msg||'listo');setTimeout(refresh,4000)}
async function doSparkOn(n){msg('Enviando magic packet a '+n+'…');const r=await post('/api/spark_on',{node:n});msg(r.msg||'listo');setTimeout(refresh,4000)}
async function doCentralOff(){
 if(!confirm('🚨 PELIGRO: vas a APAGAR el CENTRAL (.200).\\n\\nSe caen el CHAT, TODOS los agentes y ESTE panel.\\nSólo se reenciende con el botón FÍSICO del equipo (no hay encendido remoto).\\n\\n¿Continuar?'))return;
 const t=prompt('Confirmación final. Escribí exactamente:  APAGAR-CENTRAL\\n(para confirmar el apagado del central, o cancelá)');
 if(t!=='APAGAR-CENTRAL'){msg('Apagado del CENTRAL cancelado.');return;}
 msg('Apagando el CENTRAL… este panel va a dejar de responder en unos segundos.');
 const r=await post('/api/central_off',{confirm:'APAGAR-CENTRAL'});msg(r.msg||'enviado');
}
refresh();setInterval(refresh,15000);
</script></body></html>"""


# Panel SOUL v2 — misma telemetría/acciones de FABLE, composición visual alineada
# con la landing SOUL: negro profundo, violeta/cyan, jerarquía ancha y riesgo aislado.
PAGE = """<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=theme-color content="#050507">
<title>Panel SOUL</title><style>
:root{--bg:#050507;--s1:#0d0d12;--s2:#13131a;--s3:#191922;--line:rgba(255,255,255,.09);
--line2:rgba(255,255,255,.16);--fg:#f7f7fb;--mut:#9898a8;--dim:#686877;--violet:#8b5cf6;
--cyan:#22d3ee;--ok:#34d399;--warn:#fbbf24;--hot:#fb7185;--r:18px;--rs:12px}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;min-width:320px;background:var(--bg);color:var(--fg);
font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body:before{content:"";position:fixed;inset:0;pointer-events:none;z-index:-2;
background:radial-gradient(circle at 18% -5%,rgba(139,92,246,.16),transparent 31rem),
radial-gradient(circle at 85% 12%,rgba(34,211,238,.08),transparent 27rem)}
body:after{content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;opacity:.24;
background-image:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),
linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:42px 42px}
button,select{font:inherit}.shell{max-width:1280px;margin:auto;padding:0 28px 64px}
.topbar{height:72px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:12px}.mark{width:34px;height:34px;display:grid;place-items:center;
border:1px solid rgba(139,92,246,.42);border-radius:10px;background:rgba(139,92,246,.12);
color:#b9a0ff;font-size:18px;box-shadow:0 0 34px rgba(139,92,246,.2)}
.brand b{letter-spacing:-.02em}.brand span{color:var(--mut);font-weight:500}
.live{display:flex;align-items:center;gap:9px;color:var(--mut);font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none;background:var(--dim)}
.dot.on{background:var(--ok);box-shadow:0 0 0 4px rgba(52,211,153,.1),0 0 16px rgba(52,211,153,.6)}
.dot.off{background:var(--hot);box-shadow:0 0 0 4px rgba(251,113,133,.1)}
.hero{padding:56px 0 30px;display:flex;justify-content:space-between;align-items:flex-end;gap:32px}
.eyebrow{display:inline-flex;padding:7px 11px;border:1px solid rgba(139,92,246,.28);border-radius:999px;
color:#b9a0ff;background:rgba(139,92,246,.07);font-size:12px;font-weight:750;letter-spacing:.12em;text-transform:uppercase}
.plinks{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin-top:18px}
.plinks a{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border:1px solid rgba(139,92,246,.3);
border-radius:999px;color:#cfc3ff;background:rgba(139,92,246,.08);font-size:12.5px;font-weight:650;text-decoration:none;transition:.16s ease}
.plinks a:hover{border-color:rgba(34,211,238,.5);color:#bdf1fb;background:rgba(34,211,238,.08);transform:translateY(-1px)}
.plinks a.release{border-color:rgba(34,211,238,.42);color:#bdf1fb;background:rgba(34,211,238,.09)}
.plinks code{padding:7px 11px;border:1px solid var(--line2);border-radius:999px;background:var(--s3);
color:var(--cyan);font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
h1{margin:18px 0 10px;font-size:clamp(38px,6vw,68px);line-height:.95;letter-spacing:-.055em}
h1 span{background:linear-gradient(95deg,#a78bfa 8%,var(--cyan) 88%);-webkit-background-clip:text;
background-clip:text;color:transparent}.hero p{max-width:650px;margin:0;color:var(--mut);font-size:15px;line-height:1.65}
.updated{text-align:right;color:var(--dim);font-size:12px;white-space:nowrap}.updated b{display:block;color:var(--mut);font-size:13px;margin-bottom:4px}
.metrics{display:grid;grid-template-columns:1.65fr 1fr 1fr 1fr;gap:12px;margin:14px 0 20px}
.metric{position:relative;overflow:hidden;min-height:124px;padding:19px 20px;background:linear-gradient(145deg,rgba(20,20,28,.95),rgba(12,12,17,.95));
border:1px solid var(--line);border-radius:var(--rs)}.metric.primary:after{content:"";position:absolute;width:180px;height:180px;
right:-70px;top:-100px;border-radius:50%;background:rgba(139,92,246,.16)}
.label,.kicker{color:var(--mut);font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:750}
.value{position:relative;z-index:1;margin-top:16px;font-size:27px;font-weight:730;letter-spacing:-.035em;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.primary .value{font-size:23px}.value small{font-size:12px;color:var(--mut)}
.foot{margin-top:7px;color:var(--mut);font-size:12px;display:flex;align-items:center;gap:8px}
.layout{display:grid;grid-template-columns:minmax(0,1.62fr) minmax(330px,.85fr);gap:18px;align-items:start}
.stack{display:grid;gap:18px}.card{background:linear-gradient(145deg,rgba(17,17,24,.96),rgba(10,10,15,.98));
border:1px solid var(--line);border-radius:var(--r);box-shadow:0 24px 70px rgba(0,0,0,.42);overflow:hidden}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:21px 22px 16px;border-bottom:1px solid var(--line)}
.head h2{margin:5px 0 0;font-size:18px;letter-spacing:-.025em}.note{max-width:440px;color:var(--mut);
font-size:12px;line-height:1.55;text-align:right}.body{padding:18px 22px 22px}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-bottom:15px}
.legend span{display:flex;align-items:center;gap:6px}.legend i{width:6px;height:6px;border-radius:50%;background:var(--ok)}
.legend .warn{background:var(--warn)}.legend .hot{background:var(--hot)}
.node-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.node{min-height:142px;padding:16px;border:1px solid var(--line);border-radius:var(--rs);background:rgba(255,255,255,.018)}
.node.hot{border-color:rgba(251,113,133,.42);background:rgba(251,113,133,.035)}
.node-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.node-name{font-weight:680;font-size:13px;
display:flex;align-items:center;gap:9px}.node-state{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:21px}.stat{padding:9px 8px;
border-radius:9px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.055)}
.stat small{display:block;color:var(--dim);font-size:11px;letter-spacing:.06em;text-transform:uppercase}
.stat b{display:block;margin-top:4px;font-size:14px;font-variant-numeric:tabular-nums}
.t-ok{color:var(--ok)}.t-warn{color:var(--warn)}.t-hot{color:var(--hot)}
.service-list{display:grid;gap:9px}.service{padding:15px 16px;border:1px solid var(--line);border-radius:var(--rs);background:rgba(255,255,255,.018)}
.service.crit{border-color:rgba(251,113,133,.22)}.service-top{display:flex;align-items:center;justify-content:space-between;gap:14px}
.service-name{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:680}.service-meta{margin:8px 0 0 17px;
color:var(--mut);font-size:12px;line-height:1.5}.service-note{margin:7px 0 0 17px;color:var(--warn);font-size:11px;line-height:1.45}
.service.crit .service-note{color:var(--hot)}.lock{color:var(--hot);font-size:11px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.section+.section{border-top:1px solid var(--line);margin-top:20px;padding-top:20px}.section-title{font-size:13px;font-weight:680;margin:7px 0}
.copy{color:var(--mut);font-size:13px;line-height:1.55;margin:0 0 13px}
select{width:100%;height:44px;padding:0 38px 0 12px;background:var(--s3);color:var(--fg);border:1px solid var(--line2);
border-radius:10px;outline:none;margin:5px 0 9px}select:focus{border-color:rgba(139,92,246,.7);box-shadow:0 0 0 3px rgba(139,92,246,.12)}
.actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.actions.three{grid-template-columns:repeat(3,minmax(0,1fr))}
.btn{min-height:42px;padding:9px 12px;border-radius:10px;color:var(--fg);background:var(--s3);border:1px solid var(--line2);
font-weight:680;font-size:13px;cursor:pointer;transition:.18s ease}.btn:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.28);filter:brightness(1.12)}
.btn:active{transform:none}.btn:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}.btn:disabled{opacity:.48;cursor:not-allowed;transform:none;filter:none}
.btn.violet{background:linear-gradient(135deg,var(--violet),#6d42e8);border-color:transparent;box-shadow:0 8px 24px rgba(109,66,232,.22)}
.btn.green{background:rgba(52,211,153,.1);color:#8af0c8;border-color:rgba(52,211,153,.25)}
.btn.red{background:rgba(251,113,133,.08);color:#ff9aad;border-color:rgba(251,113,133,.25)}.wide{width:100%;margin-top:8px}
.wol{display:flex;gap:9px;padding:10px 11px;margin:10px 0;border:1px solid rgba(251,191,36,.15);border-radius:10px;
background:rgba(251,191,36,.045);color:#c7ad72;font-size:11px;line-height:1.45}
.danger{margin-top:18px;border-color:rgba(251,113,133,.26);background:linear-gradient(125deg,rgba(45,12,22,.7),rgba(14,9,13,.98))}
.danger .body{display:flex;align-items:center;justify-content:space-between;gap:28px;padding:22px 24px}.danger h2{margin:5px 0 7px;font-size:17px}
.danger p{max-width:770px;margin:0;color:#bd919a;font-size:12px;line-height:1.55}.danger .btn{min-width:225px}
.empty{padding:26px;color:var(--mut);text-align:center;font-size:12px}
#msg{position:fixed;left:50%;bottom:22px;z-index:20;max-width:min(680px,calc(100vw - 28px));transform:translate(-50%,24px);
padding:12px 16px;border:1px solid var(--line2);border-radius:12px;background:rgba(16,16,23,.97);box-shadow:0 18px 50px rgba(0,0,0,.5);
color:var(--fg);font-size:12px;line-height:1.45;opacity:0;pointer-events:none;transition:.22s ease}
#msg.show{opacity:1;transform:translate(-50%,0)}.skeleton{min-height:120px;border-radius:12px;
background:linear-gradient(90deg,rgba(255,255,255,.025),rgba(255,255,255,.06),rgba(255,255,255,.025));background-size:220% 100%;animation:pulse 1.5s infinite}
@keyframes pulse{to{background-position:-220% 0}}
@media(max-width:960px){.metrics{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}
.danger .body{align-items:flex-start;flex-direction:column}.danger .btn{width:100%}}
@media(max-width:620px){.shell{padding:0 16px 42px}.topbar{height:64px}.brand span{display:none}.hero{padding:38px 0 20px;align-items:flex-start;flex-direction:column}
.updated{text-align:left}.metrics{grid-template-columns:1fr 1fr;gap:8px}.metric{min-height:108px;padding:15px}.value,.primary .value{font-size:20px}
.head{flex-direction:column;padding:18px 17px 14px}.note{text-align:left}.body{padding:15px 17px 18px}.node-grid{grid-template-columns:1fr}
.actions.three{grid-template-columns:1fr}.live span:last-child{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
</style></head><body><div class=shell>
<header class=topbar><div class=brand><div class=mark>◈</div><b>SEAL <span>/ Infraestructura</span></b></div>
<div class=live><span class="dot on"></span><span>Telemetría en vivo</span></div></header>
<section class=hero><div><div class=eyebrow>Infraestructura SOUL · clúster, modelos y energía</div><h1>Panel <span>SOUL</span></h1>
<p style="max-width:650px;margin:0 0 12px;font-size:clamp(17px,2.2vw,21px);line-height:1.4;font-weight:600;color:var(--fg);border-left:3px solid var(--violet);padding-left:14px;letter-spacing:-.01em">El cerebro puede cambiar. <em style="font-style:normal;background:linear-gradient(95deg,#a78bfa,var(--cyan));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent">El alma, la memoria y la identidad permanecen.</em></p>
<p>Supervisa el clúster y controla su energía desde una sola superficie.</p>
<div class=plinks><a id=coreInstaller class=release href="https://github.com/sknaider/soul-framework/releases/latest" target=_blank rel=noopener title="Descargar la última versión de SOUL Core">&#11015; Core básico · Descargar</a><a id=platformInstaller class=release href="https://github.com/sknaider/soul-platform/releases/latest" target=_blank rel=noopener title="Descargar la última versión de SOUL Platform">&#11015; Platform completo · Descargar</a><a href="https://pypi.org/project/soul-framework/" target=_blank rel=noopener>&#128230; PyPI</a><a href="https://github.com/sknaider/soul-framework/blob/main/docs/quickstart.md" target=_blank rel=noopener>&#128214; Docs</a><code>pip install soul-framework</code></div></div>
<div class=updated><b>Actualización automática</b><span id=updated>conectando…</span></div></section>
<section class=metrics aria-label="Resumen del sistema">
<div class="metric primary"><div class=label>Cerebro actual</div><div class=value id=cur>cargando…</div><div class=foot id=st><span class=dot></span>consultando estado</div></div>
<div class=metric><div class=label>RAM libre del clúster</div><div class=value><span id=ram>—</span> <small>GB</small></div><div class=foot>spark-2 · 3 · 4</div></div>
<div class=metric><div class=label>Nodos en línea</div><div class=value id=nodeCount>— / 4</div><div class=foot>Central + Sparks</div></div>
<div class=metric><div class=label>Pico térmico</div><div class=value id=tempPeak>—°</div><div class=foot id=tempFoot>esperando sensores</div></div>
</section>
<main class=layout><div class=stack>
<section class=card><div class=head><div><div class=kicker>Clúster físico</div><h2>Estado y temperatura</h2></div><div class=note id=tleg>GPU y placa · leyendo umbrales</div></div>
<div class=body><div class=legend><span><i></i>Normal</span><span><i class=warn></i>Atención</span><span><i class=hot></i>Caliente</span></div>
<div class=node-grid id=nodes><div class=skeleton></div><div class=skeleton></div></div></div></section>
<section class=card><div class=head><div><div class=kicker>Cargas de inferencia</div><h2>Servicios de modelos</h2></div>
<div class=note>Los servicios críticos están protegidos. Pausa solo cargas seguras para recuperar RAM.</div></div>
<div class=body><div class=service-list id=services><div class=skeleton></div></div></div></section></div>
<aside class="card control"><div class=head><div><div class=kicker>Operaciones</div><h2>Control del cerebro</h2></div></div><div class=body>
<section class=section><div class=kicker>Motor de razonamiento</div><div class=section-title>Cambiar cerebro</div>
<p class=copy>Elige otro modelo sin alterar la identidad ni las memorias de SPECTRE.</p><select id=sel aria-label="Elegir cerebro"></select>
<button class="btn violet wide" onclick=doSwitch()>Cambiar a este cerebro</button></section>
<section class=section><div class=kicker>GLM del clúster</div><div class=section-title>Modelo grande</div>
<p class=copy>La carga tarda 15–20 minutos. Apagarlo libera aproximadamente 321 GB.</p>
<div class=actions><button class="btn green" onclick=doStart()>▶ Prender GLM</button><button class="btn red" onclick=doStop()>■ Apagar GLM</button></div></section>
<section class=section><div class=kicker>Energía de los Sparks</div><div class=section-title>Encender nodos</div>
<div class=wol><span>⚠</span><span>Wake-on-LAN no está disponible mientras los Spark usen Wi‑Fi. El encendido requiere botón físico hasta cablearlos.</span></div>
<div class="actions three"><button class="btn green" disabled title="Requiere botón físico mientras el nodo use Wi-Fi">Encender spark-2</button><button class="btn green" disabled title="Requiere botón físico mientras el nodo use Wi-Fi">Encender spark-3</button>
<button class="btn green" disabled title="Requiere botón físico mientras el nodo use Wi-Fi">Encender spark-4</button></div><button class="btn green wide" disabled title="Wake-on-LAN no disponible por Wi-Fi">Encendido remoto no disponible</button></section>
<section class=section><div class=section-title>Dormir nodos</div><p class=copy>Apaga la máquina completa. Para recuperarla necesitarás el botón físico.</p>
<div class="actions three"><button class="btn red" onclick="doSpark('spark-2')">Dormir spark-2</button><button class="btn red" onclick="doSpark('spark-3')">Dormir spark-3</button>
<button class="btn red" onclick="doSpark('spark-4')">Dormir spark-4</button></div><button class="btn red wide" onclick="doSpark('all')">Dormir los 3 Sparks</button></section>
</div></aside></main>
<section class="card danger"><div class=body><div><div class="kicker t-hot">Zona de seguridad crítica</div><h2>Apagar el CENTRAL (.200)</h2>
<p>Este equipo sostiene el chat, todos los agentes y el propio Panel SOUL. Al apagarlo se cae toda la infraestructura y solo puede reencenderse con el botón físico. La acción exige doble confirmación.</p></div>
<button class="btn red" onclick=doCentralOff()>Apagar el CENTRAL</button></div></section>
<div id=msg role=status aria-live=polite></div></div><script>
const $=id=>document.getElementById(id);
const API_BASE=location.pathname.startsWith('/panel-soul')?'/panel-soul':'';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path){const r=await fetch(API_BASE+path,{cache:'no-store',credentials:'include'}),d=await r.json();if(!r.ok)throw new Error(d.msg||d.error||'Error '+r.status);return d}
async function post(path,body){const r=await fetch(API_BASE+path,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}),d=await r.json();if(!r.ok)throw new Error(d.msg||d.error||'Error '+r.status);return d}
let toastTimer,refreshing=false;
function msg(text,error){const el=$('msg');el.textContent=text;el.style.borderColor=error?'rgba(251,113,133,.45)':'rgba(139,92,246,.42)';
el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),7000)}
function tclass(v,w,h){if(v===null||v===undefined)return '';return v>=h?'t-hot':(v>=w?'t-warn':'t-ok')}
function stat(label,value,cls){return '<div class=stat><small>'+label+'</small><b class="'+(cls||'')+'">'+value+'</b></div>'}
function renderStatus(s){$('cur').textContent=s.name||s.model||'sin cerebro';$('st').innerHTML='<span class="dot '+(s.healthy?'on':'off')+'"></span>'+(s.healthy?'Activo y respondiendo':'Endpoint sin respuesta')}
function renderBrains(brains){const sel=$('sel'),old=sel.value;sel.innerHTML=(brains||[]).map(x=>'<option value="'+encodeURIComponent(JSON.stringify(x))+'">'+esc(x.name)+'</option>').join('');
if(old&&[...sel.options].some(o=>o.value===old))sel.value=old}
function renderNodes(d){const nodes=d.nodes||[],w=d.warn,h=d.hot;$('ram').textContent=d.ram_free||'—';$('nodeCount').textContent=nodes.filter(n=>n.alive).length+' / '+nodes.length;
const temps=nodes.flatMap(n=>[n.gpu,n.board]).filter(v=>v!==null&&v!==undefined),peak=temps.length?Math.max(...temps):null;
$('tempPeak').textContent=peak===null?'—°':peak+'°';$('tempPeak').className='value '+tclass(peak,w,h);
$('tempFoot').textContent=peak===null?'sin lectura térmica':(peak>=h?'requiere atención':peak>=w?'temperatura elevada':'operación normal');
$('tleg').textContent='GPU + placa · normal <'+w+'° · atención '+w+'–'+(h-1)+'° · caliente ≥'+h+'°';
$('nodes').innerHTML=nodes.map(n=>{const hot=(n.gpu!==null&&n.gpu>=h)||(n.board!==null&&n.board>=h);
const gpu=n.gpu===null||n.gpu===undefined?'—':n.gpu+'°',board=n.board===null||n.board===undefined?'—':n.board+'°',ram=n.ram===null||n.ram===undefined?'—':n.ram+' GB';
return '<article class="node'+(hot?' hot':'')+'"><div class=node-head><div class=node-name><span class="dot '+(n.alive?'on':'off')+'"></span>'+esc(n.label)+
'</div><div class=node-state>'+(n.alive?'en línea':'sin conexión')+'</div></div><div class=stats>'+stat('GPU',gpu,tclass(n.gpu,w,h))+stat('Placa',board,tclass(n.board,w,h))+stat('RAM libre',ram,'')+'</div></article>'}).join('')||'<div class=empty>No hay nodos registrados.</div>'}
function renderServices(s){if(!s.length){$('services').innerHTML='<div class=empty>No hay servicios registrados.</div>';return}
$('services').innerHTML=s.map(x=>{const on=x.state==='running',crit=x.crit==='critical',state=on?'on':(x.state==='stopped'?'off':'');
const ctrl=crit?'<span class=lock>◆ Protegido</span>':(on?'<button class="btn red" onclick="doService(\\''+x.key+'\\',\\'pause\\')">Pausar</button>':'<button class="btn green" onclick="doService(\\''+x.key+'\\',\\'resume\\')">Reanudar</button>');
return '<article class="service'+(crit?' crit':'')+'"><div class=service-top><div class=service-name><span class="dot '+state+'"></span>'+esc(x.label)+'</div>'+ctrl+
'</div><div class=service-meta>'+esc(on?'activo':'pausado')+' · '+esc(x.node)+' · '+esc(x.desc||'')+'</div>'+(x.note?'<div class=service-note>'+(crit?'Protección activa · ':'Atención · ')+esc(x.note)+'</div>':'')+'</article>'}).join('')}
function renderReleases(items){(items||[]).forEach(item=>{const el=$(item.product==='core'?'coreInstaller':'platformInstaller');if(!el)return;
const target=item.installer_url||item.release_url;el.href=target.startsWith('/')?API_BASE+target:target;el.textContent='⬇ '+item.label+' · Descargar '+(item.version||'latest');
el.title=item.direct?'Descarga directa desde la release oficial':'Abrir la última release disponible'})}
async function refreshReleases(){try{renderReleases(await api('/api/releases'))}catch(e){/* Los href /latest siguen funcionando como fallback. */}}
async function refresh(){if(refreshing)return;refreshing=true;try{const [status,brains,nodes,services]=await Promise.all([api('/api/status'),api('/api/brains'),api('/api/nodes'),api('/api/services')]);
renderStatus(status);renderBrains(brains);renderNodes(nodes);renderServices(services);$('updated').textContent='última lectura · '+new Date().toLocaleTimeString('es-PE',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}
catch(e){msg('No pude actualizar el Panel SOUL: '+e.message,true);$('updated').textContent='conexión interrumpida'}finally{refreshing=false}}
async function act(label,fn,delay){msg(label+'…');try{const r=await fn();msg(r.msg||'Listo');setTimeout(refresh,delay||1500)}catch(e){msg(e.message,true)}}
async function doService(key,action){try{const services=await api('/api/services'),svc=services.find(x=>x.key===key)||{};
if(action==='pause'&&!confirm('¿Pausar "'+(svc.label||key)+'"?\\n\\n'+(svc.desc||'')+'\\n\\n'+(svc.note||'')+'\\n\\n¿Seguro?'))return;
await act(action==='pause'?'Pausando '+(svc.label||key):'Reanudando '+(svc.label||key),()=>post('/api/service_ctl',{key:key,action:action}),3000)}catch(e){msg(e.message,true)}}
async function doSwitch(){const value=$('sel').value;if(!value)return msg('No hay otro cerebro disponible.',true);const brain=JSON.parse(decodeURIComponent(value));
await act('Cambiando cerebro a '+brain.name,()=>post('/api/switch',brain),900)}
async function doStop(){if(!confirm('¿Apagar GLM y liberar la RAM? SPECTRE quedará sin cerebro grande hasta que vuelvas a prenderlo.'))return;
await act('Apagando GLM',()=>post('/api/stop'),2200)}
async function doStart(){await act('Prendiendo GLM · la carga tarda 15–20 minutos',()=>post('/api/start'),2800)}
async function doSpark(node){const q=node==='all'?'¿Apagar los 3 Sparks? Solo podrás reencenderlos con el botón físico.':'¿Apagar '+node+'? Solo podrás reencenderlo con el botón físico.';
if(!confirm(q))return;await act('Enviando apagado a '+node,()=>post('/api/spark_off',{node:node}),4000)}
async function doSparkOn(node){await act('Intentando encender '+node,()=>post('/api/spark_on',{node:node}),4000)}
async function doCentralOff(){if(!confirm('PELIGRO: vas a apagar el CENTRAL (.200).\\n\\nSe caen el chat, todos los agentes y este panel. Solo se reenciende con el botón físico.\\n\\n¿Continuar?'))return;
const token=prompt('Confirmación final. Escribe exactamente: APAGAR-CENTRAL');if(token!=='APAGAR-CENTRAL')return msg('Apagado del CENTRAL cancelado.');
await act('Apagando el CENTRAL',()=>post('/api/central_off',{confirm:'APAGAR-CENTRAL'}),1000)}
refresh();refreshReleases();setInterval(refresh,15000);setInterval(refreshReleases,900000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'self'; form-action 'none'",
        )

    def _j(self, code, o):
        b = json.dumps(o).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self._security_headers()
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _html(self):
        b = PAGE.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self._security_headers()
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _download(self, filename, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self._security_headers()
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_user(self):
        """Validate the shared SEAL session without duplicating JWT/session logic."""
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            morsel = cookies.get("seal_token")
            if not morsel or not morsel.value:
                return None
            req = urllib.request.Request(
                CHAT_AUTH_URL,
                headers={"Accept": "application/json", "Cookie": f"seal_token={morsel.value}"},
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                payload = json.load(response)
            user = payload.get("user") if isinstance(payload, dict) else None
            if not isinstance(user, dict) or str(user.get("role", "")).lower() not in CONTROL_ROLES:
                return None
            return user
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            return None

    def _deny(self, code=401, message="Sesión administrativa de SEAL requerida"):
        return self._j(code, {"ok": False, "error": message})

    def _valid_control_origin(self):
        """CSRF barrier for browser mutations from Studio or direct Panel SOUL."""
        origin = self.headers.get("Origin", "")
        try:
            parsed = urlsplit(origin)
            host = (parsed.hostname or "").lower()
            port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
        except ValueError:
            return False
        return (
            parsed.scheme in {"http", "https"}
            and port in CONTROL_ORIGIN_PORTS
            and bool(re.fullmatch(r"(?:localhost|127\.0\.0\.1|192\.168\.68\.200|100\.75\.201\.110)", host))
        )

    def do_GET(self):
        try:
            if not self._auth_user():
                return self._deny()
            if self.path == "/" or self.path.startswith("/index"):
                return self._html()
            if self.path == "/api/status":
                # Sin ram_free: la RAM viene de /api/nodes (que ya sshea los nodos en paralelo).
                # Antes /api/status disparaba SU PROPIO barrido ssh → trabajo duplicado por refresh.
                url, model = current_brain()
                return self._j(200, {"url": url, "model": model, "name": _shim_brain_name(),
                                     "healthy": brain_healthy(url)})
            if self.path == "/api/brains":
                return self._j(200, list_brains())
            if self.path == "/api/nodes":
                nodes = nodes_status()
                return self._j(200, {"nodes": nodes, "ram_free": cluster_ram_free(nodes),
                                     "warn": TEMP_WARN, "hot": TEMP_HOT})
            if self.path == "/api/sparks":
                return self._j(200, sparks_status())
            if self.path == "/api/services":
                return self._j(200, services_status())
            if self.path == "/api/releases":
                return self._j(200, releases_status())
            return self._j(404, {"error": "nf"})
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception as e:
            try: self._j(500, {"error": str(e)})
            except Exception: pass

    def do_POST(self):
        try:
            if not self._auth_user():
                return self._deny()
            if not self._valid_control_origin():
                return self._deny(403, "Origen de control no autorizado")
            n = int(self.headers.get("Content-Length", 0))
            if n < 0 or n > 4096 or "application/json" not in self.headers.get("Content-Type", ""):
                return self._deny(400, "Solicitud de control inválida")
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/switch":
                url = body.get("url", GLM_URL); model = body.get("model", "glm")
                open(BRAIN_CONF, "w").write(f"{url}\n{model}\n")
                return self._j(200, {"msg": f"Cerebro cambiado a {body.get('name', model)}. "
                                            "SPECTRE ya piensa con él, lo sabe, y su alma sigue intacta."})
            if self.path == "/api/stop":
                _sh("ssh -o ConnectTimeout=6 -o BatchMode=yes spark-2 "
                    "'tmux kill-session -t glm_serve 2>/dev/null; pkill -f \"llama-server.*GLM\" 2>/dev/null'")
                return self._j(200, {"msg": "GLM apagado. RAM del clúster liberada (~321GB). "
                                            "SPECTRE queda sin cerebro grande hasta que lo prendas."})
            if self.path == "/api/start":
                # Catch (JARVIS): no se puede SSH a un Spark apagado. Si algún nodo está OFF,
                # avisar claro en vez de fallar en silencio — hay que prenderlos FÍSICO primero.
                off = [n for n in SPARKS if not spark_alive(n)]
                if off:
                    return self._j(200, {"msg": "⚠️ No puedo prender GLM: " + ", ".join(off) +
                        " está(n) APAGADO(S). Prendé los 3 Sparks con su botón FÍSICO primero "
                        "(no hay encendido remoto: sin WoL por WiFi). Cuando el semáforo los muestre "
                        "🟢 ENCENDIDOS, apretá de nuevo y levanto GLM."})
                _sh("ssh -o ConnectTimeout=6 -o BatchMode=yes spark-2 "
                    "'bash /home/nombre/IA/modelos/serve_glm_cluster.sh >/tmp/glm_serve.log 2>&1 &'", timeout=15)
                return self._j(200, {"msg": "GLM prendiéndose (carga ~15-20 min). Te aviso el panel cuando esté activo."})
            if self.path == "/api/spark_off":
                node = body.get("node", "")
                if node == "all":
                    msgs = [spark_off(n)[1] for n in SPARKS]
                    return self._j(200, {"msg": "Apagando los 3 Sparks 💤 — " + " · ".join(msgs) +
                                         " · Para reencender: botón físico de cada Spark."})
                ok, m = spark_off(node)
                return self._j(200 if ok else 400, {"msg": m + (" · Reencender = botón físico." if ok else "")})
            if self.path == "/api/spark_on":
                node = body.get("node", "")
                if node == "all":
                    msgs = [spark_on(n)[1] for n in SPARKS]
                    return self._j(200, {"msg": "Despertando los 3 Sparks 🔌 — " + " · ".join(msgs)})
                ok, m = spark_on(node)
                return self._j(200, {"msg": m})
            if self.path == "/api/central_off":
                ok, m = central_off(body.get("confirm", ""))
                return self._j(200 if ok else 400, {"msg": m})
            if self.path == "/api/service_ctl":
                ok, m = service_ctl(body.get("key", ""), body.get("action", ""))
                return self._j(200 if ok else 400, {"msg": m})
            return self._j(404, {"error": "nf"})
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception as e:
            try: self._j(500, {"error": str(e)})
            except Exception: pass

    def log_message(self, *a):
        pass


def _shim_brain_name():
    try:
        with urllib.request.urlopen(f"{SHIM_URL}/health", timeout=3) as r:
            json.load(r)
        url, _ = current_brain()
        h = url.replace("/chat/completions", "/models")
        with urllib.request.urlopen(h, timeout=4) as r:
            return json.load(r)["data"][0]["id"]
    except Exception:
        return current_brain()[1]


if __name__ == "__main__":
    print(f"[spectre-dashboard] :{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
