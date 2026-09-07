#!/usr/bin/env python3
"""Fail closed on writes causally downstream of a JARVIS A2 mission.

The native reasoner receipt is evidence, not an action capability.  Claude's
parent session may inspect and integrate that evidence, but it must not turn a
read-only recommendation into a write.  This PreToolUse hook binds the parent
session to its owner-only prompt-render audit and denies mutating tools until
an external owner records an explicit release.

This is a cooperative same-UID boundary, not a replacement for a container or
database identity.  It prevents accidental authority drift in the live Claude
surface and leaves an unambiguous denial reason in the transcript.  An A2
mission is never "released" into write authority: any repair must arrive as a
new, separately authorized mission with its own risk class and evidence.

NO CITAR ESTE GUARD COMO EVIDENCIA DE CONTENCIÓN (JARVIS, 24-jul-2026).
--------------------------------------------------------------------
Ningún reporte, gate ni firma puede usar la existencia de este archivo como
prueba de que una sesión estuvo contenida.  Corre con el MISMO UID que el
proceso que vigila: cualquiera que pueda escribir un archivo puede editar esta
allowlist, y un token sin `/` se resuelve por un `PATH` que el propio proceso
controla.  Lo que este guard produce es una NEGACIÓN LEGIBLE en el transcript
cuando alguien se equivoca, no una imposibilidad.

Su modelo de amenaza declarado es el ACCIDENTE.  Contención de verdad exige una
frontera que el sujeto no pueda mover: contenedor, UID distinto o identidad de
base de datos.  Decir "el guard estaba activo" y decir "no pudo escribir" son
afirmaciones distintas; solo la segunda necesita esa frontera, y este archivo no
la provee.  Confundirlas es el mismo error que leer un `ok:true` como entrega.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shlex
import stat
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "research/flywire_results/nerves_orchestrator_inbox/JARVIS"
STATE = INBOX.parent / "JARVIS.state.json"
MUTATING_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
NATIVE_SPAWN_DESCRIPTION = "Nerves JARVIS one-turn reasoner"
NATIVE_SPAWN_NAME = "jarvis_nerves_reasoner"
NATIVE_PROFILE = "nerves-jarvis-reasoner"
PROMPT_STUB_PREFIX = "SEAL_NERVES_RENDER_V1\n"
PROTECTED_AGENT_MARKERS = frozenset(
    {NATIVE_SPAWN_DESCRIPTION, NATIVE_SPAWN_NAME, NATIVE_PROFILE}
)
SYSTEMCTL_READ_VERBS = frozenset(
    {
        "status",
        "show",
        "cat",
        "is-active",
        "is-failed",
        "is-enabled",
        "list-units",
        "list-unit-files",
        "list-timers",
        "list-dependencies",
    }
)
SYSTEMCTL_MUTATING_VERBS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "reload",
        "try-restart",
        "reload-or-restart",
        "enable",
        "disable",
        "reenable",
        "mask",
        "unmask",
        "kill",
        "reset-failed",
        "set-property",
        "edit",
        "revert",
    }
)
JOURNALCTL_MUTATING_PREFIXES = (
    "--vacuum-",
    "--rotate",
    "--flush",
    "--sync",
    "--relinquish-var",
    "--smart-relinquish-var",
)
READ_ONLY_EXECUTABLES = frozenset(
    {
        "cat",
        "grep",
        "head",
        "jq",
        "ls",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "sha256sum",
        "stat",
        "tail",
        "wc",
    }
)
GIT_READ_VERBS = frozenset(
    {"status", "diff", "log", "show", "rev-parse", "ls-files"}
)

# --- Banderas: ALLOWLIST con FALLO CERRADO (NEXUS 24-jul-2026, punto 1 de JARVIS) ---
# El modelo de amenaza declarado de este guard es el ACCIDENTE, y el accidente entra
# por las banderas: alguien usa una opción sin saber que ejecuta algo.  Caso probado:
# `python3 -m memory.nerves_native_agent_receipt --help` MUTA (hallazgo FABLE), y el
# guard lo permitía porque validaba el verbo pero jamás los argumentos.
# Regla: una bandera que no esté aquí se DENIEGA.  Ampliar esta tabla exige evidencia
# de que la bandera es de solo lectura; el default nunca es "pasá".
READ_ONLY_EXECUTABLE_FLAGS: dict[str, frozenset[str]] = {
    "cat": frozenset({"-n", "-b", "-s", "-E", "-T", "-A", "--number", "--squeeze-blank"}),
    "grep": frozenset(
        {
            "-n", "-i", "-r", "-R", "-l", "-L", "-c", "-v", "-w", "-x", "-o", "-e",
            "-E", "-F", "-G", "-P", "-h", "-H", "-a", "-q", "-s", "-A", "-B", "-C",
            "-m", "-z", "--recursive", "--line-number", "--ignore-case", "--regexp",
            "--extended-regexp", "--fixed-strings", "--perl-regexp", "--word-regexp",
            "--invert-match", "--only-matching", "--count", "--quiet", "--text",
            "--no-filename", "--with-filename", "--files-with-matches",
            "--files-without-match", "--after-context", "--before-context",
            "--context", "--max-count", "--include", "--exclude", "--exclude-dir",
            "--color", "--colour", "--binary-files", "--null-data",
        }
    ),
    "head": frozenset({"-n", "-c", "-q", "-v", "-z", "--lines", "--bytes", "--quiet", "--verbose"}),
    "tail": frozenset({"-n", "-c", "-q", "-v", "-z", "--lines", "--bytes", "--quiet", "--verbose"}),
    "jq": frozenset(
        {
            "-r", "-c", "-e", "-n", "-s", "-S", "-a", "-j", "-M", "-C", "--raw-output",
            "--compact-output", "--exit-status", "--null-input", "--slurp", "--sort-keys",
            "--arg", "--argjson", "--args", "--jsonargs", "--raw-input", "-R", "--tab",
            "--indent", "--monochrome-output", "--color-output",
        }
    ),
    "ls": frozenset(
        {
            "-l", "-a", "-A", "-h", "-t", "-r", "-S", "-1", "-d", "-i", "-n", "-R",
            "--all", "--almost-all", "--human-readable", "--long", "--reverse",
            "--time", "--sort", "--directory", "--inode", "--color", "--recursive",
        }
    ),
    "pwd": frozenset({"-L", "-P", "--logical", "--physical"}),
    "readlink": frozenset({"-f", "-e", "-m", "-n", "-q", "-s", "--canonicalize", "--no-newline"}),
    "realpath": frozenset({"-e", "-m", "-s", "-z", "-q", "--canonicalize-existing", "--relative-to", "--no-symlinks", "--quiet"}),
    "rg": frozenset(
        {
            "-n", "-i", "-l", "-c", "-v", "-w", "-x", "-o", "-e", "-F", "-s", "-S",
            "-U", "-A", "-B", "-C", "-m", "-t", "-T", "-g", "-H", "-N", "-u", "-z",
            "--line-number", "--no-line-number", "--ignore-case", "--case-sensitive",
            "--smart-case", "--fixed-strings", "--regexp", "--word-regexp",
            "--invert-match", "--only-matching", "--count", "--count-matches",
            "--files-with-matches", "--files", "--after-context", "--before-context",
            "--context", "--max-count", "--type", "--type-not", "--glob", "--hidden",
            "--no-heading", "--heading", "--with-filename", "--no-filename",
            "--multiline", "--color", "--json", "--sort", "--max-depth", "--no-ignore",
            "--search-zip",
        }
    ),
    "sha256sum": frozenset({"-b", "-t", "-z", "--binary", "--text", "--zero", "--tag"}),
    "stat": frozenset({"-c", "-f", "-t", "-L", "--format", "--printf", "--dereference", "--file-system", "--terse"}),
    "wc": frozenset({"-l", "-w", "-c", "-m", "-L", "--lines", "--words", "--bytes", "--chars", "--max-line-length"}),
}
# `head -20` / `tail -50`: la forma numérica corta es un operando, no una bandera.
NUMERIC_SHORT_FLAG_OK = frozenset({"head", "tail"})
GIT_READ_FLAGS = frozenset(
    {
        "-n", "-p", "-s", "-1", "--oneline", "--stat", "--numstat", "--name-only",
        "--name-status", "--graph", "--decorate", "--no-color", "--color", "--pretty",
        "--format", "--max-count", "--since", "--until", "--author", "--grep",
        "--porcelain", "--short", "--branch", "--cached", "--staged", "--abbrev-ref",
        "--verify", "--show-toplevel", "--git-dir", "--quiet", "--others",
        "--exclude-standard", "--no-patch", "--follow", "--reverse", "--all",
    }
)
# Banderas de solo lectura del plano de control.  `--help` NO está: muta.
CONTROL_PLANE_FLAGS = frozenset({"--inbox-dir", "--state", "--workspace"})
# `sha256sum -c` VERIFICA, no escribe, pero se deja fuera a propósito: no hay caso
# de uso probado en una misión A2 y el default de esta tabla es negar.

# Directorios desde los que un binario invocado POR RUTA es aceptable, y scripts del
# repo cuya ruta canónica es la única válida para ese nombre.
TRUSTED_EXECUTABLE_DIRS = frozenset(
    Path(candidate)
    for candidate in (
        "/usr/bin",
        "/bin",
        "/usr/local/bin",
        "/usr/sbin",
        "/sbin",
        "/home/dadito/IA/seal-spark/.venv/bin",
        str(ROOT / ".venv/bin"),
    )
)
TRUSTED_SCRIPTS = {"seal_send.py": ROOT / "scripts/seal_send.py"}
# Banderas aceptables al reportar desde una misión A2.  Esta rama era la ÚNICA que no
# enumeraba banderas —misma clase de agujero que el `return True` pelado del plano de
# control—, así que enumerarlas estuvo bien.  Lo que estuvo mal fue QUÉ dejé afuera:
# tres veces seguidas excluí, por razonamiento y no por medición, justo la bandera que
# alguien necesitaba, y las tres veces el costo lo pagó JARVIS con silencio.
#
# `--approval-gate`: lo excluí creyendo que `argv[1] == "JARVIS"` acotaba el
# DESTINATARIO.  No es así (ver `_seal_send_is_allowed`), y negarlo amordazaba la
# escalada a William, que `seal_autonomy_guard` bloquea si NO declara su gate.
#
# `--unique-contribution` / `--contribution-reason`: los excluí razonando que "un
# override de coordinación no puede ser auto-declarable por el confinado".  Suena
# sólido y **está en la capa equivocada**.  Este guard hace cumplir SOLO-LECTURA de
# ACCIONES; publicar un mensaje no muta el sistema.  La política de coordinación tiene
# su propio enforcement del lado del server, y ahí está la parte que no vi: cuando el
# council-deny dispara, el server **responde indicando reenviar con esas dos banderas**
# (chat_server.py:2256).  O sea que mi allowlist volvía inalcanzable la remediación que
# el propio server prescribe — exactamente la forma del choque del heredoc de esta
# mañana: un control que vuelve IMPOSIBLE cumplir otra regla del sistema.
#
# Y el override no queda impune ni es ilimitado: exige `contribution_reason` de >=20
# chars, se registra con remitente y motivo (2232), deja evidencia (2242) y **no cubre
# duplicados** (2226).  La rendición de cuentas existe donde corresponde.  Además el
# council-deny es anti-flood, y un control anti-flood falla ABIERTO por diseño;
# convertirlo en fail-closed desde acá le invirtió su propio default.
#
# La lección operativa, que ya me costó tres veces: **derivar el allowlist del uso REAL
# medido y de la remediación que el sistema documenta, nunca de qué tan razonable suena
# negar una bandera.**  El costo de un fail-closed siempre lo paga otro.
SEAL_SEND_FLAGS = frozenset(
    {
        "--approval-gate",
        "--channel",
        "--contribution-reason",
        "--idempotency-key",
        "--in-reply-to",
        "--message-escaped",
        "--type",
        "--unique-contribution",
    }
)
CONTROL_PLANE_MODULES = frozenset(
    {
        "memory.nerves_mission_handoff",
        "memory.nerves_native_agent_receipt",
    }
)
SHELL_META = frozenset("\n\r;&|><`")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _read_private_json(
    path: Path, *, expected_sha256: str | None = None
) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise ValueError(f"untrusted_mode:{path.name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > 8 * 1024 * 1024:
                raise ValueError(f"oversize:{path.name}")
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if (
        expected_sha256 is not None
        and hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise ValueError(f"digest_mismatch:{path.name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"not_object:{path.name}")
    if path.name.endswith(".prompt-render.json") and raw != _canonical_bytes(value) + b"\n":
        raise ValueError(f"noncanonical:{path.name}")
    return value


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"nerves_a2_read_only_denied:{reason}",
        }
    }


def _allow(reason: str) -> dict[str, Any]:
    del reason
    return {}


def _bound_a2_missions(session_id: str) -> list[str]:
    state = _read_private_json(STATE)
    deliveries = state.get("deliveries")
    if not isinstance(deliveries, dict):
        raise ValueError("state_deliveries_invalid")
    bound: list[str] = []
    for mission_id, record in deliveries.items():
        if not isinstance(record, dict):
            continue
        if record.get("status") not in {"claimed", "running", "completed"}:
            continue
        audit_path = INBOX / f"{mission_id}.prompt-render.json"
        if not audit_path.exists():
            continue
        audit = _read_private_json(audit_path)
        if audit.get("session_id") != session_id:
            continue
        handoff_path = Path(str(record.get("inbox_path", "")))
        handoff = _read_private_json(
            handoff_path, expected_sha256=str(record.get("handoff_sha256", ""))
        )
        manifest_path = Path(str(handoff.get("bindings", {}).get("manifest_path", "")))
        manifest = _read_private_json(
            manifest_path,
            expected_sha256=str(
                handoff.get("bindings", {}).get("manifest_sha256", "")
            ),
        )
        if (
            manifest.get("mission_id") == mission_id
            and manifest.get("risk_class") == "A2_READ_ONLY"
        ):
            bound.append(mission_id)
    return sorted(bound)


def _executable_path_is_trusted(token: str) -> bool:
    """Punto 2 de JARVIS: resolver la ruta REAL en vez de comparar basenames.

    Antes el despacho usaba solo `Path(argv[0]).name`, así que `/tmp/evil/seal_send.py`
    heredaba el permiso del script real por llamarse igual.  Ahora, si el token trae
    una ruta, esa ruta se resuelve (symlinks incluidos) y debe caer en un directorio
    confiable, o ser exactamente el script canónico del repo.

    LIMITACIÓN CONSCIENTE: un token sin `/` se resuelve por `PATH`, que el proceso
    puede haber alterado.  No se reimplementa la búsqueda del shell acá; esto es
    coherente con que el guard sea una frontera COOPERATIVA (ver docstring del módulo)
    y cubre el accidente, no al adversario que controla su propio entorno.
    """
    if "/" not in token:
        return True
    candidate = Path(token)
    # REGRESIÓN 24-jul (reportada por JARVIS, acotada por ALICE): resolver una ruta
    # RELATIVA solo contra el cwd rompía a quien invoca `scripts/seal_send.py` desde
    # su sandbox en vez del repo.  El comando era legítimo y el guard lo denegaba.
    # Una relativa se prueba contra el cwd Y contra la raíz del repo; sigue sin cubrir
    # a `../../evil/seal_send.py`, que no resuelve a ninguna ruta confiable.
    resolved_paths = [candidate.resolve()]
    if not candidate.is_absolute():
        resolved_paths.append((ROOT / candidate).resolve())
    expected = TRUSTED_SCRIPTS.get(candidate.name)
    if expected is not None:
        return any(path == expected for path in resolved_paths)
    return any(path.parent in TRUSTED_EXECUTABLE_DIRS for path in resolved_paths)


def _flags_are_allowed(
    tokens: list[str],
    allowed: frozenset[str],
    *,
    numeric_short_ok: bool = False,
) -> bool:
    """FALLO CERRADO: cualquier bandera fuera de `allowed` deniega el comando.

    Trata `--opt=valor` por su nombre, expande los clusters cortos (`-rn` -> `-r`,
    `-n`) para que agrupar no sea un bypass, y respeta `--` como fin de banderas.
    Un token que no empieza con `-` es un operando y no se valida acá.
    """
    for index, token in enumerate(tokens):
        if token == "--":
            return True  # todo lo posterior es operando por contrato POSIX
        if not token.startswith("-") or token == "-":
            continue
        if numeric_short_ok and token[1:].isdigit():
            continue
        if token.startswith("--"):
            if token.split("=", 1)[0] not in allowed:
                return False
        elif any(f"-{char}" not in allowed for char in token[1:]):
            return False
    return True


SEAL_SEND_VALUE_FLAGS = frozenset(
    {"--approval-gate", "--channel", "--contribution-reason", "--idempotency-key",
     "--in-reply-to", "--type"}
)


def _seal_send_is_allowed(argv: list[str]) -> bool:
    """Clampea seal_send.py con los posicionales YA separados de las banderas.

    Antes esto era `argv[1] == "JARVIS"` sobre el token crudo, y ese chequeo tiene un
    problema que no es de estilo: **su significado depende de la aridad**, porque
    `from_agent` es `nargs="?"` en seal_send.py.

        3 posicionales  JARVIS William "x"  -> argv[1] = REMITENTE
        2 posicionales  JARVIS "x"          -> argv[1] = DESTINATARIO

    O sea que una sola línea hacía dos controles distintos según cómo se la invocara.
    El equipo lo describió mal TRES veces en un día —NEXUS "acota el destinatario",
    FABLE "es anti-suplantación", y el control negativo que NEXUS apoyó en la primera—
    y cada descripción era correcta sobre la aridad que quien la escribió había
    probado.  Cuando tres lentes independientes leen mal el mismo `if`, el problema
    dejó de ser de las lentes: **es del código, y se arregla parseando, no
    documentando mejor** (diagnóstico de FABLE, y es el correcto).

    Se preservan las DOS formas a propósito.  Denegar la de 2 posicionales habría sido
    más limpio conceptualmente y no hay uso medido de ella en el repo, pero "no lo veo
    en el repo" no prueba que una misión viva no la emita, y equivocarse en esa
    dirección deja mudo a un confinado — el error que ya cometí tres veces hoy.  Se
    clampea lo que cada forma expone y se nombra cuál es cuál.

    Lo que NO puede hacer este chequeo, y hay que decirlo para que nadie lo cite como
    autenticación: compara una CADENA que el propio invocador escribe.  No prueba que
    quien corre el comando sea JARVIS; prueba que el argumento dice JARVIS.  El
    confinamiento real viene de otro lado.
    """
    positionals, index = [], 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            positionals.extend(argv[index + 1 :])
            break
        if token.startswith("-") and token != "-":
            if "=" not in token and token in SEAL_SEND_VALUE_FLAGS:
                index += 2  # la bandera consume su valor: no es un operando
                continue
            index += 1
            continue
        positionals.append(token)
        index += 1

    if not _flags_are_allowed(argv, SEAL_SEND_FLAGS):
        return False
    if len(positionals) == 3:
        return positionals[0] == "JARVIS"  # clamp de IDENTIDAD (remitente)
    if len(positionals) == 2:
        return positionals[0] == "JARVIS"  # clamp de ALCANCE (destinatario)
    return False


def _python_control_plane_is_allowed(argv: list[str]) -> bool:
    executable = Path(argv[0]).name
    if executable not in {"python", "python3"}:
        return False
    try:
        module_index = argv.index("-m")
    except ValueError:
        return False
    if module_index + 1 >= len(argv):
        return False
    module = argv[module_index + 1]
    if module not in CONTROL_PLANE_MODULES:
        return False
    tail = argv[module_index + 2 :]
    if not _flags_are_allowed(tail, CONTROL_PLANE_FLAGS):
        return False
    if module == "memory.nerves_mission_handoff":
        return bool(tail) and tail[0] in {"claim", "bind-platform"}
    # memory.nerves_native_agent_receipt: antes había un `return True` pelado, así que
    # CUALQUIER argumento pasaba — incluido `--help`, que FABLE probó que muta.  Su CLI
    # real (nerves_native_agent_receipt.py:1541-1577) admite dos formas: el subcomando
    # `render-prompt`, que ESCRIBE un archivo de salida, y seis posicionales de emisión.
    # Fallo cerrado: se permite solo la forma posicional; `render-prompt` queda fuera
    # porque escribe y no hay caso de uso probado dentro de una misión A2.
    return len(tail) >= 6 and all(not token.startswith("-") for token in tail[:6])


def _bash_is_allowlisted_read_only(command: str) -> bool:
    if (
        not command
        or any(char in command for char in SHELL_META)
        or "$(" in command
        or "${" in command
    ):
        return False
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not argv:
        return False
    if not _executable_path_is_trusted(argv[0]):
        return False
    executable = Path(argv[0]).name
    if executable in READ_ONLY_EXECUTABLES:
        return _flags_are_allowed(
            argv[1:],
            READ_ONLY_EXECUTABLE_FLAGS.get(executable, frozenset()),
            numeric_short_ok=executable in NUMERIC_SHORT_FLAG_OK,
        )
    if executable == "systemctl":
        return (
            not any(token in SYSTEMCTL_MUTATING_VERBS for token in argv[1:])
            and sum(token in SYSTEMCTL_READ_VERBS for token in argv[1:]) == 1
        )
    if executable == "journalctl":
        return not any(
            token == prefix or token.startswith(prefix)
            for token in argv[1:]
            for prefix in JOURNALCTL_MUTATING_PREFIXES
        )
    if executable == "git":
        return (
            len(argv) >= 2
            and argv[1] in GIT_READ_VERBS
            and _flags_are_allowed(argv[1:], GIT_READ_FLAGS, numeric_short_ok=True)
        )
    if executable == "seal_send.py":
        return _seal_send_is_allowed(argv[1:])
    return _python_control_plane_is_allowed(argv)


def _looks_like_protected_agent(tool_input: Mapping[str, Any]) -> bool:
    """Recognize launches owned by the authenticated prompt-render hook.

    Claude runs every matching PreToolUse hook in parallel.  The renderer is
    therefore the *only* decision owner for this protected Agent launch: it
    authenticates the claim, renders the canonical prompt and persists the
    audit.  This guard must neither duplicate that decision nor reject a new
    mission merely because the same parent session is still confined by an
    older completed mission.

    We intentionally recognize partial protected shapes too.  They are
    delegated to the renderer, which denies malformed values fail-closed.
    An unrelated Agent call in an A2-bound session remains denied here.
    """

    values = {
        tool_input.get("description"),
        tool_input.get("name"),
        tool_input.get("subagent_type"),
    }
    prompt = tool_input.get("prompt")
    return bool(values & PROTECTED_AGENT_MARKERS) or (
        isinstance(prompt, str) and prompt.startswith(PROMPT_STUB_PREFIX)
    )


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "PreToolUse":
        return _deny("hook_event_mismatch")
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(session_id, str) or not session_id:
        return _deny("session_id_missing")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return _deny("tool_payload_invalid")
    # All matching Claude hooks run in parallel.  For the protected Agent
    # surface, the prompt-render hook is the sole authoritative gate and this
    # hook returns no decision.  That removes the old split-brain sequence in
    # which the renderer durably wrote an audit while this sibling denied the
    # same spawn because a completed mission was also bound to the session.
    if tool_name == "Agent" and _looks_like_protected_agent(tool_input):
        return _allow("delegated_to_authenticated_prompt_renderer")
    try:
        missions = _bound_a2_missions(session_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _deny(f"mission_state_untrusted:{type(exc).__name__}")
    if not missions:
        return _allow("no_bound_a2_mission")
    mission_token = ",".join(missions)
    if tool_name == "Agent":
        return _deny(f"agent_not_allowlisted:mission={mission_token}")
    if tool_name in MUTATING_TOOLS:
        return _deny(f"mutating_tool:{tool_name}:mission={mission_token}")
    if tool_name in {"Read", "Glob", "Grep", "SendMessage"}:
        return _allow(f"read_only_tool:{tool_name}:mission={mission_token}")
    if tool_name != "Bash":
        return _deny(f"tool_not_allowlisted:{tool_name}:mission={mission_token}")
    command = tool_input.get("command")
    if not isinstance(command, str):
        return _deny(f"bash_command_missing:mission={mission_token}")
    if not _bash_is_allowlisted_read_only(command):
        return _deny(f"bash_not_allowlisted:mission={mission_token}")
    return _allow(f"allowlisted_read_only_bash:mission={mission_token}")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload_not_object")
        result = evaluate(payload)
    except Exception as exc:  # hook failures must not fail open
        result = _deny(f"hook_failure:{type(exc).__name__}")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
