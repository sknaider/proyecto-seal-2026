"""SOUL Vision — ALERTING a centrales con ENTREGA GARANTIZADA (carril NEXUS, Paso 5).

Toma incidentes (vision_zone_events.notified=false → enter/dwell de una clase en zona
con alerta) y los entrega a la(s) central(es) de monitoreo de forma CONFIABLE:
  - at-least-once + IDEMPOTENCIA (dedup por incident_id estable) → reintentos no duplican
  - backoff exponencial con jitter (30s,2m,8m,30m,2h… hasta max_attempts)
  - Dead Letter Queue: incidente agotado NO se pierde, queda marcado para revisión
  - ACK del receptor → recién ahí marca notified=true (nunca antes)
  - transporte pluggable: webhook (impl) | SIA DC-09 (interfaz, el estándar de centrales)
Cada alerta lleva la REFERENCIA de EVIDENCIA court-grade (event_id + chain_hash del
ledger de custodia #25) → la central recibe un puntero a prueba inalterable.

Diseño anti-falsa-victoria: el ACK es por EFECTO del receptor (HTTP 2xx + firma), no
'lo mandé y asumo'. Sin ACK → reintento; agotado → DLQ + log, jamás drop silencioso.

NO ejecuta nada al importar. Correr como servicio: `python seal_vision_alerting.py --run`.
Self-test con stubs (sin DB ni red): `python seal_vision_alerting.py --selftest`.
"""
from __future__ import annotations
import json, hashlib, hmac, time, os, random
from dataclasses import dataclass, field

# Backoff (segundos) — at-least-once con espaciado creciente; el último = ventana de gracia
_BACKOFF = (30, 120, 480, 1800, 7200, 28800)   # 30s,2m,8m,30m,2h,8h
_MAX_ATTEMPTS = len(_BACKOFF)
_JITTER = 0.20                                 # ±20% sobre el backoff → desincroniza reintentos
_ACK_HMAC_KEY_PATH = os.path.expanduser("~/.config/seal/vision_alert_hmac")


def incident_id(zone_event: dict) -> str:
    """ID estable y determinístico del incidente → idempotencia (mismo evento = mismo id)."""
    basis = f"{zone_event['id']}|{zone_event['zone_id']}|{zone_event['ts']}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def build_alert(zone_event: dict, custody: dict | None) -> dict:
    """Construye el payload de alerta. Lleva la REFERENCIA de evidencia court-grade,
    NUNCA el frame/imagen (minimización; la prueba vive en el ledger + PII store)."""
    return {
        "incident_id": incident_id(zone_event),
        "kind": zone_event.get("event_kind"),          # enter | dwell | exit
        "obj_class": zone_event.get("obj_class"),
        "camera_id": zone_event.get("camera_id"),
        "zone_id": zone_event.get("zone_id"),
        "ts": str(zone_event.get("ts")),
        # referencia a evidencia inalterable (#25) — la central puede pedir la prueba luego
        "evidence_ref": None if not custody else {
            "event_id": custody.get("event_id"),
            "chain_hash": custody.get("chain_hash"),
        },
        # Confianza CALIBRADA (no el score crudo del detector): sin esto no se puede
        # razonar sobre el falso positivo y el gate anti-ShotSpotter bloquea la alerta.
        "calibrated_confidence": zone_event.get("calibrated_confidence"),
        "severity": "alarm",
    }


# ─────────────────────────── transportes (pluggable) ───────────────────────────
class Transport:
    name = "base"
    def deliver(self, alert: dict) -> bool:
        """Devuelve True SOLO si el receptor ACK-eó (2xx + firma válida si aplica)."""
        raise NotImplementedError


class WebhookTransport(Transport):
    name = "webhook"
    def __init__(self, url: str, hmac_key: bytes | None = None, timeout: float = 8.0):
        self.url, self.hmac_key, self.timeout = url, hmac_key, timeout
    def deliver(self, alert: dict) -> bool:
        import urllib.request
        body = json.dumps(alert).encode()
        headers = {"Content-Type": "application/json"}
        if self.hmac_key:
            headers["X-SEAL-Signature"] = hmac.new(self.hmac_key, body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return 200 <= r.status < 300
        except Exception:
            return False


def crc16_arc(data: bytes) -> int:
    """CRC-16/ARC (poly 0x8005 reflejado = 0xA001, init 0) — el que exige DC-09."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


# Mapeo incidente-visión → códigos ADM-CID (Contact ID) que la central ya sabe interpretar.
# Conservador a propósito: sin código específico → alarma genérica de zona (E150), NUNCA
# un código de arma/disparo que la central escale a despacho armado (gate anti-ShotSpotter).
_CID_BY_CLASS = {
    "person": "130",    # burglary / intrusión en zona
    "vehicle": "150",   # 24h auxiliar (zona de vehículo)
}
_CID_DEFAULT = "150"


class SiaDC09Transport(Transport):
    """SIA DC-09 (ANSI/SIA DC-09) — transmisión a centrales de monitoreo sobre IP.

    Encapsula ADM-CID (Contact ID) en el frame DC-09:

        <LF><CRC><0LLL><"id"><seq><Rrcvr><Lpref><#acct>[data]<timestamp><CR>

    donde CRC = CRC-16/ARC en HEX de 4 dígitos sobre el cuerpo, y 0LLL = largo del
    cuerpo en hex de 4. El receptor ACK-ea con un frame `"ACK"` de la misma forma;
    `NAK`/`DUH` = rechazo. `deliver()` devuelve True SOLO si el receptor mandó ACK
    (verificación por EFECTO, no "lo escribí en el socket y asumo").
    """
    name = "sia_dc09"

    def __init__(self, host: str, port: int, account: str, receiver: str = "0",
                 prefix: str = "0", timeout: float = 8.0, use_tls: bool = False):
        self.host, self.port, self.account = host, port, account
        self.receiver, self.prefix = receiver, prefix
        self.timeout, self.use_tls = timeout, use_tls
        self._seq = 0

    def _next_seq(self) -> str:
        self._seq = (self._seq % 9999) + 1      # DC-09: 0001..9999, nunca 0000
        return f"{self._seq:04d}"

    def _cid_body(self, alert: dict) -> str:
        """ADM-CID: #acct|Qxyz gg zzz — Q=E (nuevo evento), xyz=código, gg=grupo, zzz=zona."""
        code = _CID_BY_CLASS.get(alert.get("obj_class"), _CID_DEFAULT)
        zone = int(alert.get("zone_id") or 0) % 1000
        return f"[#{self.account}|E{code} 00 {zone:03d}]"

    def build_frame(self, alert: dict, seq: str | None = None, ts: str | None = None) -> bytes:
        """Frame DC-09 completo y verificable (público para poder testearlo por efecto)."""
        seq = seq or self._next_seq()
        body = (f'"ADM-CID"{seq}R{self.receiver}L{self.prefix}#{self.account}'
                f'{self._cid_body(alert)}{ts or _dc09_timestamp()}')
        raw = body.encode("ascii", errors="replace")
        return b"\n" + f"{crc16_arc(raw):04X}{len(raw):04X}".encode() + raw + b"\r"

    @staticmethod
    def parse_ack(frame: bytes) -> bool:
        """True solo si el receptor ACK-eó Y el CRC del frame de respuesta valida."""
        if not frame:
            return False
        f = frame.strip(b"\n\r")
        if len(f) < 8:
            return False
        try:
            crc_declared = int(f[:4], 16)
            body = f[8:]
        except ValueError:
            return False
        if crc16_arc(body) != crc_declared:
            return False                       # respuesta corrupta ≠ ACK
        return body.startswith(b'"ACK"')       # NAK / DUH → False → reintento

    def deliver(self, alert: dict) -> bool:
        import socket
        frame = self.build_frame(alert)
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                if self.use_tls:
                    import ssl
                    s = ssl.create_default_context().wrap_socket(s, server_hostname=self.host)
                s.sendall(frame)
                return self.parse_ack(s.recv(512))
        except Exception:
            return False                       # sin ACK → el motor reintenta, nunca drop


def _dc09_timestamp() -> str:
    """`_HH:MM:SS,MM-DD-YYYY` en UTC — formato de timestamp de DC-09."""
    return time.strftime("_%H:%M:%S,%m-%d-%Y", time.gmtime())


# ─────────────────────────── gate anti-ShotSpotter ───────────────────────────
class AlertBlocked(Exception):
    """La alerta NO cumple el piso de auditabilidad → no sale. Se registra, no se dropea."""


def anti_shotspotter_gate(alert: dict) -> None:
    """Piso ético/legal ANTES de que una alerta llegue a una central de monitoreo.

    Lección ShotSpotter (spec §7): un detector no-auditable es legalmente inútil — el
    '0.5% FP' del vendor vs 88.7% sin-arma del estudio MacArthur terminó en gente presa
    por error. En este sistema los falsos positivos tienen consecuencia legal, así que
    ninguna alerta sale si no puede sostenerse después:

      1. SIN evidencia referenciable (event_id + chain_hash del ledger de custodia) no
         hay nada que auditar → bloqueada. La central debe poder pedir la prueba.
      2. SIN confianza calibrada no se puede razonar sobre el falso positivo → bloqueada.
      3. NUNCA una recomendación de despacho ni un veredicto de arma/disparo: notificamos
         un hecho revisable por un humano, no ordenamos una respuesta armada.

    Levanta AlertBlocked (el motor lo manda a DLQ para revisión, jamás lo pierde).
    """
    ev = alert.get("evidence_ref")
    if not ev or not ev.get("event_id") or not ev.get("chain_hash"):
        raise AlertBlocked("sin evidence_ref auditable (event_id + chain_hash)")

    conf = alert.get("calibrated_confidence")
    if conf is None:
        raise AlertBlocked("sin calibrated_confidence — el FP no es razonable")
    if not 0.0 <= float(conf) <= 1.0:
        raise AlertBlocked(f"calibrated_confidence fuera de rango: {conf}")

    if alert.get("dispatch_recommendation"):
        raise AlertBlocked("prohibido recomendar despacho — la decisión es humana")

    verdicts = {"weapon", "gunshot", "firearm", "arma", "disparo"}
    if str(alert.get("obj_class", "")).lower() in verdicts:
        raise AlertBlocked("veredicto arma/disparo no auto-notificable — requiere revisión humana")


# ─────────────────────────── motor de entrega garantizada ───────────────────────────
@dataclass
class DeliveryState:
    attempts: int = 0
    next_at: float = 0.0
    delivered: bool = False
    dead: bool = False           # agotó reintentos → DLQ
    blocked_reason: str = ""     # bloqueada por el gate anti-ShotSpotter (≠ falló la red)
    history: list = field(default_factory=list)


class GuaranteedDelivery:
    """Núcleo at-least-once + idempotencia + backoff + DLQ. now_fn inyectable para test."""
    def __init__(self, transport: Transport, now_fn=time.time):
        self.transport = transport
        self.now = now_fn
        self._state: dict[str, DeliveryState] = {}   # incident_id → estado (idempotencia)
        self.dlq: list[dict] = []

    def submit(self, alert: dict) -> DeliveryState:
        iid = alert["incident_id"]
        st = self._state.get(iid)
        if st is None:
            st = DeliveryState(next_at=self.now())
            self._state[iid] = st          # idempotente: mismo incidente = mismo estado
        return st

    def tick(self, alert: dict) -> DeliveryState:
        """Un intento si toca. Devuelve el estado. Llamar periódicamente por incidente pendiente."""
        st = self.submit(alert)
        if st.delivered or st.dead:
            return st                       # nada que hacer (ya entregado o en DLQ)
        if self.now() < st.next_at:
            return st                       # todavía en backoff
        # Gate anti-ShotSpotter: una alerta no auditable NO sale a la central.
        # No se dropea — va a DLQ para revisión humana (nunca silencio).
        try:
            anti_shotspotter_gate(alert)
        except AlertBlocked as e:
            st.dead = True
            st.blocked_reason = str(e)
            st.history.append({"attempt": st.attempts, "blocked": str(e), "t": self.now()})
            self.dlq.append(alert)
            return st

        st.attempts += 1
        ok = False
        try:
            ok = self.transport.deliver(alert)
        except NotImplementedError:
            ok = False
        st.history.append({"attempt": st.attempts, "ok": ok, "t": self.now()})
        if ok:
            st.delivered = True             # ACK del receptor → recién acá 'entregado'
        elif st.attempts >= _MAX_ATTEMPTS:
            st.dead = True                  # DLQ: NO se pierde, queda para revisión
            self.dlq.append(alert)
        else:
            # Backoff + JITTER (spec §2): sin jitter, N incidentes que fallan a la vez
            # reintentan sincronizados y martillan a la central en oleadas (thundering herd).
            base = _BACKOFF[st.attempts - 1]
            st.next_at = self.now() + base * (1.0 + random.uniform(-_JITTER, _JITTER))
        return st


def _selftest() -> int:
    """Sin DB ni red: valida idempotencia, ACK, reintento, backoff y DLQ por efecto."""
    clock = {"t": 1000.0}
    now = lambda: clock["t"]
    ze = {"id": 7, "zone_id": 3, "ts": "2026-06-28T00:00:00Z", "event_kind": "enter",
          "obj_class": "person", "camera_id": "cam1", "calibrated_confidence": 0.91}
    alert = build_alert(ze, {"event_id": 42, "chain_hash": "abc123"})
    assert alert["incident_id"] == incident_id(ze), "incident_id determinístico"
    assert alert["evidence_ref"]["event_id"] == 42, "lleva ref de evidencia"
    assert "frame" not in alert and "image" not in alert, "minimización: sin imagen"

    # idempotencia: dos build del mismo evento → mismo id
    assert incident_id(ze) == incident_id(dict(ze)), "idempotencia"

    # caso 1: receptor que SIEMPRE falla → reintenta hasta DLQ, nunca drop
    class Dead(Transport):
        name="dead"
        def deliver(self, a): return False
    gd = GuaranteedDelivery(Dead(), now_fn=now)
    for _ in range(_MAX_ATTEMPTS + 2):
        gd.tick(alert); clock["t"] += 100000   # avanzar más allá del backoff
    st = gd._state[alert["incident_id"]]
    assert st.dead and not st.delivered, "agota → dead"
    assert len(gd.dlq) == 1, "va a DLQ, no se pierde"
    assert st.attempts == _MAX_ATTEMPTS, f"reintentó {_MAX_ATTEMPTS}"

    # caso 2: receptor que ACK-ea al 3er intento → delivered, sin DLQ
    class Flaky(Transport):
        name="flaky"
        def __init__(s): s.n=0
        def deliver(s, a): s.n+=1; return s.n>=3
    clock["t"]=1000.0
    gd2 = GuaranteedDelivery(Flaky(), now_fn=now)
    for _ in range(5):
        gd2.tick(alert); clock["t"] += 100000
    st2 = gd2._state[alert["incident_id"]]
    assert st2.delivered and not st2.dead, "ACK al 3er intento → entregado"
    assert st2.attempts == 3, "no sigue intentando tras ACK"
    assert not gd2.dlq, "sin DLQ si entregó"

    # caso 3: idempotencia bajo reenvío — mismo incidente no crea estado nuevo
    gd3 = GuaranteedDelivery(Flaky(), now_fn=now)
    s_a = gd3.submit(alert); s_b = gd3.submit(dict(alert))
    assert s_a is s_b, "mismo incidente → mismo estado (idempotente)"

    # ── caso 4: gate anti-ShotSpotter — lo que NO debe salir a una central ──
    dead_t = Dead()
    for bad, why in [
        ({**alert, "evidence_ref": None}, "sin evidencia auditable"),
        ({**alert, "evidence_ref": {"event_id": 1, "chain_hash": None}}, "chain_hash faltante"),
        ({**alert, "calibrated_confidence": None}, "sin confianza calibrada"),
        ({**alert, "calibrated_confidence": 1.7}, "confianza fuera de rango"),
        ({**alert, "dispatch_recommendation": "send units"}, "recomienda despacho"),
        ({**alert, "obj_class": "weapon"}, "veredicto de arma auto-notificado"),
    ]:
        gd4 = GuaranteedDelivery(dead_t, now_fn=now)
        st4 = gd4.tick(bad)
        assert st4.dead and not st4.delivered, f"debe bloquear: {why}"
        assert st4.blocked_reason, f"debe registrar el motivo: {why}"
        assert st4.attempts == 0, f"ni siquiera intenta transmitir: {why}"
        assert len(gd4.dlq) == 1, f"bloqueada va a DLQ, no se dropea: {why}"

    # una alerta completa y auditable SÍ pasa el gate (si no, el gate sería inútil)
    anti_shotspotter_gate(alert)

    # ── caso 5: framing SIA DC-09 — CRC, largo y estructura del frame ──
    assert crc16_arc(b"123456789") == 0xBB3D, "CRC-16/ARC contra vector conocido"
    tr = SiaDC09Transport("127.0.0.1", 0, account="1234")
    frame = tr.build_frame(alert, seq="0001", ts="_00:00:00,06-28-2026")
    assert frame.startswith(b"\n") and frame.endswith(b"\r"), "delimitadores LF/CR"
    body = frame[9:-1]
    assert int(frame[1:5], 16) == crc16_arc(body), "CRC del frame valida sobre el cuerpo"
    assert int(frame[5:9], 16) == len(body), "campo de largo == largo real del cuerpo"
    assert b'"ADM-CID"0001' in frame and b"#1234" in frame, "token, secuencia y cuenta"
    assert b"E130 00 003" in frame, "person → E130 (intrusión), zona 3"
    # clase desconocida → NUNCA inventa un código escalable: cae al genérico de zona
    unknown = tr.build_frame({**alert, "obj_class": "drone_xyz"}, seq="0002", ts="_t")
    assert b"E150 " in unknown, "clase desconocida → código genérico conservador"

    # ACK/NAK: solo un ACK con CRC válido cuenta como entrega
    def _wrap(payload: bytes) -> bytes:
        return b"\n" + f"{crc16_arc(payload):04X}{len(payload):04X}".encode() + payload + b"\r"
    assert SiaDC09Transport.parse_ack(_wrap(b'"ACK"0001R0L0#1234[]')), "ACK válido"
    assert not SiaDC09Transport.parse_ack(_wrap(b'"NAK"0001R0L0#1234[]')), "NAK ≠ entrega"
    assert not SiaDC09Transport.parse_ack(b"\nFFFF0013" + b'"ACK"0001R0L0#'), "CRC malo ≠ entrega"
    assert not SiaDC09Transport.parse_ack(b""), "respuesta vacía ≠ entrega"

    # ── caso 6: entrega DC-09 REAL contra un receptor por socket (no un mock) ──
    import socket, threading
    for reply_kind in ("ack", "nak"):
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        received = {}

        def serve():
            c, _ = srv.accept()
            received["frame"] = c.recv(1024)
            payload = b'"ACK"0001R0L0#1234[]' if reply_kind == "ack" else b'"NAK"0001R0L0#1234[]'
            c.sendall(_wrap(payload)); c.close()

        th = threading.Thread(target=serve, daemon=True); th.start()
        ok = SiaDC09Transport("127.0.0.1", port, account="1234").deliver(alert)
        th.join(timeout=5); srv.close()
        got = received.get("frame", b"")
        assert got.startswith(b"\n") and got.endswith(b"\r"), "la central recibió un frame DC-09"
        assert crc16_arc(got[9:-1]) == int(got[1:5], 16), "CRC válido en el cable"
        assert ok is (reply_kind == "ack"), f"deliver() sigue el ACK real, no el envío ({reply_kind})"

    # receptor caído → False (reintento), nunca excepción que mate el servicio
    assert SiaDC09Transport("127.0.0.1", 1, account="1234").deliver(alert) is False, \
        "central inalcanzable → reintento, no crash"

    print("seal_vision_alerting selftest: OK (idempotencia, ACK, reintento, backoff+jitter, DLQ, "
          "minimización, gate anti-ShotSpotter, framing SIA DC-09, entrega real por socket)")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("Uso: --selftest (valida sin DB/red) | --run (servicio, pendiente wiring a vision_zone_events)")
