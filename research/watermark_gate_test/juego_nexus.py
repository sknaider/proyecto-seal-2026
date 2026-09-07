"""
Tres en Raya (Tic-Tac-Toe) — juego de consola con IA imbatible.

Creado por NEXUS como artefacto de PRUEBA para el gate de bytes de ADA
(chequeo de procedencia/marca de agua). Sin dependencias externas: solo la
librería estándar, para que la revisión por bytes no tenga ruido de terceros.

La IA usa minimax con poda alfa-beta: nunca pierde. El humano juega con 'O',
la máquina con 'X'. Tablero por casillas 1-9 (como el teclado numérico).

Uso:
    python3 juego_nexus.py
"""
from __future__ import annotations

# 8 líneas ganadoras: 3 filas, 3 columnas, 2 diagonales.
LINEAS = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)

HUMANO, MAQUINA, VACIO = "O", "X", " "


def ganador(tablero: list[str]) -> str | None:
    """Devuelve la ficha ganadora, o None si aún no hay tres en raya."""
    for a, b, c in LINEAS:
        if tablero[a] != VACIO and tablero[a] == tablero[b] == tablero[c]:
            return tablero[a]
    return None


def libres(tablero: list[str]) -> list[int]:
    """Índices de las casillas todavía vacías."""
    return [i for i, casilla in enumerate(tablero) if casilla == VACIO]


def minimax(tablero: list[str], turno_maquina: bool,
            alfa: float, beta: float) -> int:
    """Puntúa el tablero desde la óptica de la máquina.

    +10 si gana la máquina, -10 si gana el humano, 0 empate; se descuenta la
    profundidad para preferir victorias rápidas y derrotas lentas. Poda
    alfa-beta para no explorar ramas que no pueden mejorar el resultado.
    """
    gana = ganador(tablero)
    if gana == MAQUINA:
        return 10 - (9 - len(libres(tablero)))
    if gana == HUMANO:
        return -10 + (9 - len(libres(tablero)))
    if not libres(tablero):
        return 0

    if turno_maquina:
        mejor = -float("inf")
        for i in libres(tablero):
            tablero[i] = MAQUINA
            mejor = max(mejor, minimax(tablero, False, alfa, beta))
            tablero[i] = VACIO
            alfa = max(alfa, mejor)
            if beta <= alfa:
                break
        return int(mejor)

    mejor = float("inf")
    for i in libres(tablero):
        tablero[i] = HUMANO
        mejor = min(mejor, minimax(tablero, True, alfa, beta))
        tablero[i] = VACIO
        beta = min(beta, mejor)
        if beta <= alfa:
            break
    return int(mejor)


def jugada_maquina(tablero: list[str]) -> int:
    """Elige la mejor casilla para la máquina con minimax."""
    mejor_valor, mejor_pos = -float("inf"), -1
    for i in libres(tablero):
        tablero[i] = MAQUINA
        valor = minimax(tablero, False, -float("inf"), float("inf"))
        tablero[i] = VACIO
        if valor > mejor_valor:
            mejor_valor, mejor_pos = valor, i
    return mejor_pos


def dibujar(tablero: list[str]) -> None:
    """Imprime el tablero; las casillas vacías muestran su número (1-9)."""
    vista = [c if c != VACIO else str(i + 1) for i, c in enumerate(tablero)]
    print()
    for fila in range(0, 9, 3):
        print(f" {vista[fila]} | {vista[fila + 1]} | {vista[fila + 2]} ")
        if fila < 6:
            print("---+---+---")
    print()


def pedir_jugada(tablero: list[str]) -> int:
    """Lee una casilla válida del humano (1-9, no ocupada).

    Con EOF (stdin agotado) o Ctrl-C, sale limpio en vez de reventar con
    traceback: devuelve -1 para que el bucle principal termine la partida.
    """
    while True:
        try:
            entrada = input("Tu jugada (1-9): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nPartida interrumpida.")
            return -1
        if not entrada.isdigit() or not (1 <= int(entrada) <= 9):
            print("Escribe un número del 1 al 9.")
            continue
        pos = int(entrada) - 1
        if tablero[pos] != VACIO:
            print("Esa casilla ya está ocupada.")
            continue
        return pos


def main() -> None:
    tablero = [VACIO] * 9
    print("Tres en Raya — tú eres 'O', la máquina 'X'. La máquina no pierde.")
    dibujar(tablero)

    while True:
        pos_humano = pedir_jugada(tablero)
        if pos_humano < 0:
            return
        tablero[pos_humano] = HUMANO
        dibujar(tablero)
        if ganador(tablero) or not libres(tablero):
            break

        pos = jugada_maquina(tablero)
        tablero[pos] = MAQUINA
        print(f"La máquina juega en la casilla {pos + 1}.")
        dibujar(tablero)
        if ganador(tablero) or not libres(tablero):
            break

    resultado = ganador(tablero)
    if resultado == HUMANO:
        print("¡Ganaste! (No debería pasar…)")
    elif resultado == MAQUINA:
        print("Gana la máquina.")
    else:
        print("Empate.")


if __name__ == "__main__":
    main()
