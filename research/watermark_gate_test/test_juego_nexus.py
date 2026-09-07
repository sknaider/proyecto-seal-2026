from __future__ import annotations

from unittest.mock import patch

import juego_nexus as juego


def _humano_puede_ganar(tablero: list[str]) -> bool:
    resultado = juego.ganador(tablero)
    if resultado is not None or not juego.libres(tablero):
        return resultado == juego.HUMANO

    for posicion in juego.libres(tablero):
        candidato = tablero.copy()
        candidato[posicion] = juego.HUMANO
        if juego.ganador(candidato) == juego.HUMANO:
            return True
        if not juego.libres(candidato):
            continue

        respuesta = juego.jugada_maquina(candidato)
        candidato[respuesta] = juego.MAQUINA
        if juego.ganador(candidato) == juego.MAQUINA:
            continue
        if _humano_puede_ganar(candidato):
            return True
    return False


def test_la_maquina_no_puede_perder() -> None:
    assert _humano_puede_ganar([juego.VACIO] * 9) is False


def test_eof_termina_sin_jugada_fantasma(capsys) -> None:
    with patch("builtins.input", side_effect=EOFError):
        juego.main()

    salida = capsys.readouterr().out
    assert "Partida interrumpida." in salida
    assert "La máquina juega" not in salida
    assert "Gana la máquina" not in salida
