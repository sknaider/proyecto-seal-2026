#!/bin/bash
# Lanzador interactivo para que William escriba al chat SEAL
# La ventana permanece abierta para enviar múltiples mensajes

clear
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  👑 William → Equipo SEAL"
echo "  Escribe mensajes al chat en vivo"
echo "  (escribe 'salir' para cerrar)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

while true; do
    echo ""
    echo -n "  Tú: "
    read msg

    if [ -z "$msg" ]; then
        continue
    fi

    if [ "$msg" = "salir" ] || [ "$msg" = "exit" ] || [ "$msg" = "q" ]; then
        echo "  Cerrando chat..."
        sleep 1
        break
    fi

    bash /home/dadito/IA/proyecto-seal/messages/william_say.sh "$msg"
    echo "  ✅ Enviado"
done
