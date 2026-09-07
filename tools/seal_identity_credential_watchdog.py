#!/usr/bin/env python3
"""Detect a Store-A credential outage before an agent discovers it by booting mute.

Why this exists
---------------
On 2026-08-27 all four agents booted as ``external``: the launcher seeded
``<AGENT>.token`` without the ``<AGENT>.token.meta.json`` that
``SEAL_TOKEN_LIFECYCLE_MODE=ENFORCE`` requires, so ``token_owner()`` resolved to
``None`` for every one of them.  It was caught by an agent tripping over a
``deny`` — the same way the 8-ago and 26-ago outages were caught.  Three human
detections in a row is a signal about the detection layer, not about the bugs.

``seal-identity-heartbeat`` does NOT cover this: it watches personality drift
(``turns``/``relational``), and ``grep -cE 'token_owner|valid_tokens'`` over it
returns 0.  The word "identity" means two different things in this system —
continuity of personality, and session credential — and only the first had a
detector.  This is the second.

Two design rules, both learned the hard way today
-------------------------------------------------
1. **Mirror the authority, do not re-implement it.**  The check imports the very
   functions the MCP server calls, and reads ``SEAL_TOKEN_LIFECYCLE_MODE`` from
   the *live server process* rather than from its own environment.  ALICE's
   sandbox reported a healthy token because it defaulted to ``OFF`` while the
   server ran ``ENFORCE`` — a checker that trusts its own default measures a
   system nobody is running.
2. **Prove you can fail before you report healthy.**  Every run first plants a
   deliberately broken fixture in a private temp dir and requires the check to
   reject it.  If that self-test does not fail as expected, the run reports
   ``UNMEASURABLE`` (exit 3) instead of green — an instrument that cannot detect
   the defect must never be read as evidence of its absence.

Exit codes: 0 healthy · 1 outage detected · 3 unmeasurable.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MEMORY_DIR = "/home/dadito/IA/proyecto-seal/memory"
sys.path.insert(0, MEMORY_DIR)

import seal_identity_tokens as sit  # noqa: E402

DEFAULT_AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS")


def _known_agents() -> tuple[str, ...]:
    """The roster the MCP server itself recognizes, read from its source.

    C6 says mirror the authority instead of re-declaring its constants: a second
    copy of this list would drift the moment someone adds an agent, and the
    watchdog would go on reporting confident coverage of a roster that no longer
    matches.  Parsed rather than imported because importing the server module
    would pull in its whole runtime.
    """
    try:
        source = Path(MEMORY_DIR, "mcp_server_v4.py").read_text()
        for line in source.splitlines():
            if line.startswith("_KNOWN_AGENTS"):
                literal = line.split("=", 1)[1].strip()
                if literal.startswith("frozenset("):
                    literal = literal[len("frozenset("):-1]
                return tuple(sorted(ast.literal_eval(literal)))
    except (OSError, SyntaxError, ValueError, IndexError):
        pass
    return DEFAULT_AGENTS


KNOWN_AGENTS = _known_agents()
EXIT_OK, EXIT_OUTAGE, EXIT_UNMEASURABLE = 0, 1, 3


def select_default_roster(
    token_dir: Path,
    *,
    known_agents: tuple[str, ...] = KNOWN_AGENTS,
    default_agents: tuple[str, ...] = DEFAULT_AGENTS,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Choose only identities the live MCP authority can actually resolve.

    Token files may also belong to isolated gateways or deliberately external
    seats (currently ALICE-V2 and FABLE JUEZ).  Treating every ``*.token`` as a
    private SOUL identity turns intentional fail-closed isolation into an
    outage.  Unknown files remain explicit in ``external_tokens``; they are not
    silently counted as healthy or broken MCP agents.
    """
    on_disk = tuple(
        sorted(
            path.name[: -len(".token")].upper()
            for path in token_dir.glob("*.token")
        )
    ) if token_dir.is_dir() else ()
    authority = set(known_agents)
    agents = tuple(sorted((set(on_disk) & authority) | (set(default_agents) & authority)))
    unmonitored = tuple(sorted(authority - set(agents)))
    external_tokens = tuple(sorted(set(on_disk) - authority))
    return agents, unmonitored, external_tokens


def live_server_lifecycle_mode(
    proc_root: Path = Path("/proc"),
) -> tuple[str | None, int | None]:
    """Read SEAL_TOKEN_LIFECYCLE_MODE from the running MCP server, not from us.

    Returns ``(mode, pid)``; ``(None, None)`` when no server process is readable,
    which is itself a reason to report UNMEASURABLE rather than to assume a mode.
    Two or more candidates is the SAME answer: an ambiguous identification cannot
    produce a credential verdict.
    """
    matches: list[tuple[str, int]] = []
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            # ANCLA: el que EJECUTA el server, no el que lo MENCIONA.
            # El substring sobre el cmdline matchea cualquier shell que nombre el
            # archivo (un `sed memory/mcp_server_v4.py`, un `grep`, el prompt de un
            # agente).  Si ese impostor no lleva la variable, el `return "OFF"` de
            # abajo se dispara sobre EL y el watchdog adopta un modo permisivo del
            # proceso equivocado: bajo OFF los fixtures rotos resuelven "sanos" y el
            # self-test sale UNMEASURABLE.  Cual gana depende del orden de
            # `/proc.iterdir()`, que no esta garantizado -> veredicto por loteria.
            # Misma clase que el `pgrep -f` que este mes nos dio un verde falso.
            # (NEXUS, 28-ago-2026, tras el UNMEASURABLE de las 23:40.)
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            args = [a.decode(errors="replace") for a in argv if a]
            if not any(a.endswith("mcp_server_v4.py") for a in args[1:]):
                continue
            try:
                exe = os.readlink(proc / "exe")
            except (OSError, PermissionError):
                continue
            if "python" not in os.path.basename(exe):
                continue
            environ = (proc / "environ").read_bytes().decode(errors="replace")
        except (OSError, PermissionError):
            continue
        mode = "OFF"  # server running with the variable unset
        for entry in environ.split("\0"):
            if entry.startswith("SEAL_TOKEN_LIFECYCLE_MODE="):
                mode = entry.split("=", 1)[1].strip().upper()
                break
        matches.append((mode, int(proc.name)))

    # AMBIGUEDAD == NO MEDIBLE, no "el primero".  Endurecer el patron solo mueve
    # la frontera de que impostor entra; mientras la regla sea "tomo el primero
    # que matchea", el siguiente disfraz vuelve a ganar -- y gana en silencio,
    # porque un unico match falso es indistinguible de un unico match bueno.
    # Con `/proc` iterando en orden de PID, el impostor gana justo despues de que
    # el server reinicia y queda con el PID mas alto: falla determinista, no azar.
    # (FABLE lo senalo sobre mi primer parche; NEXUS, 28-ago-2026.)
    if len(matches) != 1:
        return None, None
    return matches[0]


def check_live_path(agent: str, token_dir: Path, *, timeout: float = 15.0) -> dict:
    """Second modality: exercise the real MCP path instead of inspecting files.

    Everything else in this watchdog reads the same surface — the token files and
    `token_owner` over them.  Three checks against one surface are one measurement
    with three names (the lesson SPECTRE taught us, and the three identical regexes
    a day later).  This asks a different question, through a different channel:
    *does the running server actually resolve me as myself?*

    It can fail with perfect files — a stale session binding, a broker policy, a
    transport fault — and it can pass while a file check is confused.  That is the
    point: agreement between two modalities is evidence, agreement within one is not.

    Only ever uses THIS agent's own credential.  Probing with a teammate's token
    would be reading a secret that is not ours to hold, and the coverage it buys
    does not justify that.
    """
    result: dict = {"agent": agent, "modality": "live_mcp_path"}
    # La credencial sale del ENTORNO de este proceso, no del disco.
    #
    # Antes leía `<agent>.token` con el agente que dijera `SEAL_AGENT`: correrlo con
    # `SEAL_AGENT=ADA` sondeaba con el token de ADA. O sea que mi "sólo uso mi propia
    # credencial" era una convención sostenida por una variable de entorno, no una
    # restricción — y lo descubrí probando el tercer caso de mi propia verificación.
    #
    # `SEAL_SESSION_TOKEN` es el secreto que el launcher ya me dio: usarlo no lee nada
    # que no tuviera, y hace estructuralmente imposible sondear con el de un compañero.
    token = os.environ.get("SEAL_SESSION_TOKEN", "").strip()
    if not token:
        result.update(ok=None, reason="no SEAL_SESSION_TOKEN in this process environment")
        return result
    # Esta función NO abre ningún archivo de token — ni el propio.
    #
    # Mi primer intento comparaba el env contra `<agent>.token` para negarse a sondear
    # con una credencial ajena. ADA señaló el residuo: para comparar, igual ABRÍA el
    # archivo del otro. Un control que se salta después de haber leído el secreto no
    # protege el secreto, protege la conciencia del que lo escribió.
    #
    # El único secreto que toca es el que este proceso ya tiene en su entorno, y quién
    # es su dueño lo decide el SERVIDOR: si pido el estado de un agente que no soy, la
    # autoridad me lo niega. Eso es exactamente lo que un chequeo de identidad debe
    # hacer — preguntarle a quien decide, no deducirlo leyendo.

    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "credential-watchdog", "version": "1"}},
    })
    curl = ["curl", "-s", "--max-time", str(int(timeout)), "-D", "-",
            "-X", "POST", "http://127.0.0.1:8771/mcp",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-H", "Accept: application/json, text/event-stream",
            "-d", body]
    try:
        proc = subprocess.run(curl, capture_output=True, text=True, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(ok=None, reason=f"transport unavailable: {type(exc).__name__}")
        return result
    if proc.returncode != 0 or "mcp-session-id" not in proc.stdout.lower():
        # Server down or not speaking MCP: UNMEASURABLE, never a credential verdict.
        result.update(ok=None, reason="no mcp-session-id in response (server down?)")
        return result

    sid = ""
    for line in proc.stdout.splitlines():
        if line.lower().startswith("mcp-session-id:"):
            sid = line.split(":", 1)[1].strip()
            break
    # El handshake MCP tiene TRES pasos, no dos: initialize → notifications/initialized
    # → tools/call.  Omitir el del medio dejaba la sesión a medio abrir y el server
    # respondía algo que mi parser leía como frontera de privacidad — una explicación
    # elegante para un handshake incompleto.  El curl manual sí lo enviaba, y por eso
    # funcionaba a mano y no en el código: **probé el endpoint, no el camino del caller.**
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    try:
        subprocess.run([*curl[:-2], "-H", f"mcp-session-id: {sid}", "-d", note],
                       capture_output=True, text=True, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(ok=None, reason=f"handshake failed: {type(exc).__name__}")
        return result

    call = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "working_state_get",
                                  "arguments": {"agent": agent}}})
    try:
        proc2 = subprocess.run(
            [*curl[:-2], "-H", f"mcp-session-id: {sid}", "-d", call],
            capture_output=True, text=True, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(ok=None, reason=f"call failed: {type(exc).__name__}")
        return result

    out = proc2.stdout
    # "blocked for external" is the exact signature of an unresolved identity: the
    # Bearer authenticated the transport but the broker could not name its owner.
    # OJO con el orden: un error de herramienta viaja DENTRO de `result`, con
    # `isError: true`.  Mi primera versión buscaba `"result"` y contaba como éxito
    # un `[PRIVACY] NEXUS→ADA blocked` — la frontera del server funcionando, leída
    # como identidad sana.  Un campo que parece evidencia: hay que mirar dónde se
    # decide, no que el nombre esté presente.
    # Clasificar por la ESTRUCTURA de la respuesta, no por grep sobre el payload.
    #
    # Mi primera versión buscaba los marcadores como substrings en todo el output y se
    # equivocó de la forma más instructiva posible: dio "frontera de privacidad" sobre
    # una respuesta EXITOSA, porque el texto `[PRIVACY]` venía dentro de mi propio
    # working_state — lo había guardado ahí minutos antes, al correr la prueba cruzada.
    # El marcador estaba en los DATOS, no en el veredicto. Es la misma lección de
    # `n_live_tup` y del `Result=success`: buscá dónde se DECIDE, no dónde aparece.
    payload = None
    for line in out.splitlines():
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        result.update(ok=None, reason="unrecognised response shape")
        return result
    res = payload.get("result")
    if not isinstance(res, dict):
        result.update(ok=None, reason=f"no result field (keys: {sorted(payload)})")
        return result
    if not res.get("isError"):
        result.update(ok=True, reason="server answered a private tool for this agent")
        return result
    # Sólo acá el texto es un veredicto del servidor, no contenido del agente.
    text = " ".join(c.get("text", "") for c in res.get("content", [])
                    if isinstance(c, dict))
    if "blocked for external" in text:
        result.update(ok=False, reason="server resolved this session as 'external'")
    elif "[PRIVACY]" in text:
        result.update(ok=None,
                      reason="privacy boundary refused a cross-agent probe (server is correct)")
    else:
        result.update(ok=None, reason=f"tool error unrelated to identity: {text[:80]}")
    return result


def check_agents(token_dir: Path, agents: tuple[str, ...], mode: str) -> list[dict]:
    """Resolve each agent's owner exactly as the server's broker would."""
    previous = os.environ.get("SEAL_TOKEN_LIFECYCLE_MODE")
    os.environ["SEAL_TOKEN_LIFECYCLE_MODE"] = mode
    try:
        rows = []
        for agent in agents:
            token_path = token_dir / f"{agent}.token"
            meta_path = token_dir / f"{agent}.token.meta.json"
            owner = None
            token_present = token_path.exists()
            if token_present:
                try:
                    token = token_path.read_text().strip()
                    owner = sit.token_owner([token_dir], agents, token)
                except OSError:
                    owner = None
            rows.append(
                {
                    "agent": agent,
                    "token_present": token_present,
                    "meta_present": meta_path.exists(),
                    "resolved_owner": owner,
                    "healthy": owner == agent,
                }
            )
        return rows
    finally:
        if previous is None:
            os.environ.pop("SEAL_TOKEN_LIFECYCLE_MODE", None)
        else:
            os.environ["SEAL_TOKEN_LIFECYCLE_MODE"] = previous


def self_test(mode: str) -> dict:
    """Plant known-bad fixtures and require the check to reject each one.

    The cases are the ones that actually deceived us: a token whose sidecar is
    missing (the 27-ago outage) and a token whose sidecar describes a *different*
    token (what a reseed leaves behind).  A green that cannot go red is not a
    green.
    """
    sandbox = Path(tempfile.mkdtemp(prefix="seal_cred_watchdog_selftest."))
    try:
        sandbox.chmod(0o700)
        results = {}

        # Case A — token without meta: must NOT resolve an owner under ENFORCE.
        token_a = sandbox / "PROBEA.token"
        token_a.write_text(secrets.token_hex(32) + "\n")
        token_a.chmod(0o600)
        rows = check_agents(sandbox, ("PROBEA",), mode)
        results["missing_meta_rejected"] = not rows[0]["healthy"]

        # Case B — meta describing a different token (a stale reseed).
        token_b = sandbox / "PROBEB.token"
        token_b.write_text(secrets.token_hex(32) + "\n")
        token_b.chmod(0o600)
        meta_b = sandbox / "PROBEB.token.meta.json"
        meta_b.write_text(
            json.dumps(
                {
                    "schema": sit.SCHEMA,
                    "agent": "PROBEB",
                    "mode": "CURRENT",
                    "generation": 1,
                    "token_sha256": "0" * 64,  # deliberately not this token
                    "issued_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "enforced": True,
                }
            )
        )
        meta_b.chmod(0o600)
        rows = check_agents(sandbox, ("PROBEB",), mode)
        results["mismatched_meta_rejected"] = not rows[0]["healthy"]

        # Case C — a token whose meta was written by the PRODUCTION writer: must go
        # GREEN.  Without this case the whole self-test is satisfied by a checker
        # that is simply always red: cases A and B only prove it can say "broken".
        # Caught by JARVIS, who sabotaged `healthy` to False on a copy and watched
        # the self-test pass while the run announced a false outage for all four
        # agents.  FABLE's rule has a twin: a detector tested only against bad
        # cases is not tested either — it cannot tell "detects well" from
        # "always screams".  Both floors are required, neither alone is enough.
        token_c = sandbox / "PROBEC.token"
        token_c.write_text(secrets.token_hex(32) + "\n")
        token_c.chmod(0o600)
        writer = Path(__file__).resolve().parent / "seal_identity_ensure_meta.py"
        try:
            subprocess.run(
                [sys.executable, str(writer), "--agent", "PROBEC",
                 "--token-dir", str(sandbox), "--quiet"],
                check=True, capture_output=True, timeout=30,
            )
            rows = check_agents(sandbox, ("PROBEC",), mode)
            results["healthy_token_accepted"] = rows[0]["healthy"]
        except (OSError, subprocess.SubprocessError) as exc:
            # A self-test that cannot run its own fixture must REPORT that, not
            # raise: an unhandled traceback exits 120 and systemd files it as a
            # unit failure, which reads as "the watchdog is broken" instead of
            # "the watchdog could not measure".  Those need different responses.
            results["healthy_token_accepted"] = False
            results["healthy_case_error"] = f"{type(exc).__name__}: {exc}"

        results["passed"] = all(results.values())
        return results
    finally:
        # sandbox is always a mkdtemp path under the system temp dir; the guard
        # keeps a future edit from ever pointing this at something else.
        if sandbox.is_dir() and sandbox.name.startswith("seal_cred_watchdog_selftest."):
            shutil.rmtree(sandbox, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--token-dir", type=Path, default=Path("/run/user/1000/seal"))
    parser.add_argument("--agent", action="append", dest="agents")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    # C9 — do not police a fixed roster while the universe is bigger.  This list
    # was hardcoded to four while the MCP server's _KNOWN_AGENTS holds six: a
    # broken token for DUM or SPECTRE would have been invisible, and the run would
    # still have printed a confident OK.  Derive the roster from the tokens that
    # actually exist, and SAY what is being left out — a silent cap reads exactly
    # like full coverage.  (Found by auditing this watchdog against FABLE's
    # detector acceptance contract; the class was written from other cases.)
    if args.agents:
        agents = tuple(a.strip().upper() for a in args.agents)
        unmonitored: tuple[str, ...] = ()
    else:
        agents, unmonitored, external_tokens = select_default_roster(args.token_dir)
    if args.agents:
        external_tokens = ()

    mode, pid = live_server_lifecycle_mode()
    report: dict = {"token_dir": str(args.token_dir), "server_pid": pid,
                    "lifecycle_mode": mode,
                    "external_tokens": list(external_tokens)}

    if mode is None:
        report["result"] = "UNMEASURABLE"
        report["reason"] = "no readable mcp_server_v4.py process; refusing to assume a mode"
        print(json.dumps(report, indent=2) if args.json else
              f"UNMEASURABLE — {report['reason']}")
        return EXIT_UNMEASURABLE

    probe = self_test(mode)
    report["self_test"] = probe
    if not probe["passed"]:
        report["result"] = "UNMEASURABLE"
        # Name which floor gave way: "cannot go red" and "cannot go green" are
        # different faults and need different fixes.
        failed = [k for k, v in probe.items() if k != "passed" and v is False]
        if "healthy_token_accepted" in failed:
            report["reason"] = ("self-test rejected a KNOWN-GOOD token: the check cannot go "
                                "green, so its red means nothing")
        else:
            report["reason"] = ("self-test accepted a KNOWN-BAD fixture: the check cannot see "
                                "the defect it exists to catch")
        report["failed_cases"] = failed
        print(json.dumps(report, indent=2) if args.json else
              f"UNMEASURABLE — {report['reason']} ({probe})")
        return EXIT_UNMEASURABLE

    rows = check_agents(args.token_dir, agents, mode)
    report["agents"] = rows
    # Segunda modalidad: sólo sobre la credencial propia (ver check_live_path).
    me = os.environ.get("SEAL_AGENT", "").strip().upper()
    if me in agents:
        report["live_path"] = check_live_path(me, args.token_dir)
    report["unmonitored"] = list(unmonitored)
    broken = [r["agent"] for r in rows if not r["healthy"]]
    report["result"] = "OUTAGE" if broken else "OK"

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"credential watchdog: {report['result']} "
              f"(mode={mode} from server pid={pid})")
        for row in rows:
            print(f"  {row['agent']:<8} token={row['token_present']!s:<5} "
                  f"meta={row['meta_present']!s:<5} owner={row['resolved_owner']}")
        # C9: never let the roster be narrower than the universe without saying so.
        if unmonitored:
            print(f"  not covered ({len(unmonitored)} of {len(KNOWN_AGENTS)} known to the "
                  f"server hold no token): {', '.join(unmonitored)}")
        if external_tokens:
            print("  external token files (outside MCP private-identity roster): "
                  f"{', '.join(external_tokens)}")
        # Cuántas modalidades corrieron DE VERDAD en esta corrida.  La segunda depende
        # del server vivo, así que es la primera que se pierde — y se pierde justo en
        # el escenario degradado donde más falta hace (FABLE, 28-ago).  Sin esta línea,
        # una corrida con una sola modalidad se lee igual que una con dos: el lector
        # cree tener corroboración cruzada cuando tiene una medición sola.
        lp = report.get("live_path")
        if lp is None:
            pass  # no es este agente: la modalidad 2 no aplica, no se anuncia cobertura
        elif lp.get("ok") is None:
            print(f"  [1 de 2 modalidades] la vía viva NO pudo medir ({lp.get('reason')}). "
                  f"Lo de arriba sale sólo de inspeccionar archivos.")
        else:
            estado = "coincide" if lp["ok"] == (not broken) else "DISCREPA con los archivos"
            print(f"  [2 de 2 modalidades] vía viva para {lp['agent']}: "
                  f"ok={lp['ok']} — {estado}")
        if broken:
            print(f"  -> {len(broken)} agent(s) cannot resolve identity: {', '.join(broken)}")
            print("     they will boot as 'external' and every private tool will be denied")
    return EXIT_OUTAGE if broken else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
