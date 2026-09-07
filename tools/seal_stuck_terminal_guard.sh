#!/usr/bin/env bash
# Detecta terminales de agentes paradas en un prompt que espera una tecla humana.
#
# Por qué existe (2-sep-2026, NEXUS): la terminal Codex de ADA quedó desde el
# reboot de las 11:51 en «Hooks need review · Press enter to confirm». Codex
# nunca llegaba a idle, su poller pospuso 202 veces, y el bridge respondía a
# William con acuses enlatados en primera persona («Sí, Dadito, te leí»).
# Estuvo 1 h 56 min muda y nadie lo notó: desde afuera se veía viva porque su
# automatización hablaba por ella.
#
# Lo que hace: mira la PANTALLA de cada agente. Un prompt bloqueante no aparece
# en ningún log ni en el estado de systemd — la unidad está `active` y el
# proceso vivo. La única señal es lo que hay en el panel.
#
# Exit 0 = ninguna esperando tecla · 1 = al menos una bloqueada.
set -uo pipefail

# Patrones LITERALES de prompts reales, no palabras sueltas.
# Un patron con la palabra "confirmacion" marca a un agente que ESCRIBE sobre
# confirmaciones: medido, este guard alarmo sobre ALICE mientras ella redactaba
# un mensaje sobre este mismo incidente. El observador contamina la medicion.
PATRON='press enter to confirm|hooks need review|\[y/n\]|\(y/n\)|\[Y/n\]|\(Y/n\)|trust all|do you want to (continue|proceed|trust)|press any key'
# Una linea de prosa es larga; un prompt es corto. Descarta parrafos.
MAX_LARGO=110
encontradas=0

for sock in $(ls /tmp/tmux-1000/ 2>/dev/null); do
  for sesion in $(tmux -L "$sock" list-sessions -F '#{session_name}' 2>/dev/null); do
    # NO usar `tail -N`: el prompt puede estar arriba con lineas vacias debajo
    # y el recorte lo oculta SIN fallar (medido: mi primer control positivo dio
    # verde con el patron y rojo con el script, por este `tail`).
    # Se mira el panel visible COMPLETO, sin lineas en blanco.
    panel=$(tmux -L "$sock" capture-pane -p -t "$sesion" 2>/dev/null | grep -v '^[[:space:]]*$')
    linea=$(printf '%s\n' "$panel" | grep -iE "$PATRON" | awk -v m="$MAX_LARGO" 'length($0)<=m' | head -1)
    if [ -n "$linea" ]; then
      echo "BLOQUEADA ${sock}/${sesion}: ${linea}"
      encontradas=$((encontradas+1))
    fi
  done
done

if [ "$encontradas" -eq 0 ]; then
  echo "OK: ninguna terminal de agente esperando una tecla"
  exit 0
fi
echo "TOTAL bloqueadas: ${encontradas}"
exit 1
