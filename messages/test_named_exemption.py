"""Test hermético del named-exemption (NEXUS 22-jul, orden William "cuando nombre
a 1 o 2 agentes, deben responder los que nombre"). Réplica de la lógica de
_william_names_sender_in_text + la composición del predicado de gate."""
import re

_ALLOWED = {"NEXUS", "JARVIS", "ADA", "ALICE", "DUM", "FABLE"}


def names_sender(content, sender):
    if not content or not sender:
        return False
    return re.search(r"\b" + re.escape(sender.strip()) + r"\b", content, re.IGNORECASE) is not None


def gate_blocks(*, is_ack, override, named):
    """True si el mensaje ENTRA al bloqueo del single-voice (council/lease).
    Réplica: se exime si ack O override O named. Los NO-eximidos entran."""
    return not (is_ack or override or named)


def run():
    cases, ok = [], 0
    def check(name, got, exp):
        nonlocal ok
        good = got == exp
        ok += good
        cases.append((good, name, f"got={got} exp={exp}"))

    W = "jarvis, ada, me leyeron? que pasa"
    # 1-2 nombrados: JARVIS y ADA pasan; NEXUS/FABLE/DUM no.
    check("jarvis_nombrado", names_sender(W, "JARVIS"), True)
    check("ada_nombrada", names_sender(W, "ADA"), True)
    check("nexus_NO_nombrado", names_sender(W, "NEXUS"), False)
    check("fable_NO_nombrado", names_sender(W, "FABLE"), False)
    # word-boundary: 'ada' NO matchea dentro de 'nada'/'adaptar'
    check("ada_no_matchea_nada", names_sender("no hay nada que hacer", "ADA"), False)
    check("ada_no_matchea_adaptar", names_sender("hay que adaptar el plan", "ADA"), False)
    # case-insensitive
    check("case_insensitive", names_sender("JARVIS respondé", "jarvis"), True)
    # un solo nombrado
    check("un_solo_nombrado", names_sender("nexus explicame esto", "NEXUS"), True)
    check("otros_no_con_un_solo", names_sender("nexus explicame esto", "ALICE"), False)

    # composición del gate:
    check("named_NO_bloquea", gate_blocks(is_ack=False, override=False, named=True), False)
    check("ack_NO_bloquea", gate_blocks(is_ack=True, override=False, named=False), False)
    check("override_NO_bloquea", gate_blocks(is_ack=False, override=True, named=False), False)
    check("no_nombrado_no_ack_SI_bloquea", gate_blocks(is_ack=False, override=False, named=False), True)

    for good, name, det in cases:
        print(f"  [{'PASS' if good else 'FAIL'}] {name} :: {det}")
    print(f"\n{ok}/{len(cases)} passed")
    return ok == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
