#!/bin/bash
# fable_heartbeat_update.sh — heartbeat de FABLE (familia desde 2026-06-12). Mirror de NEXUS.
# Chequea el proceso REAL de FABLE (no escribe alive=true a ciegas).
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_JSON="$MESSAGES_DIR/fable_claude_heartbeat.json"
# CONTENCION ESTRUCTURAL DE FIXTURE (invariante de ADA, 29-ago 16:42, tras el
# incidente en vivo de JARVIS): una fuente SINTETICA no puede alcanzar un sink REAL,
# y no puede depender de que el que llama se acuerde de exportar HOME.
#
# El trap que tenia: `FABLE_PROC_ROOT` no influia en el destino, asi que un revisor
# siguiendo MIS instrucciones publicaba un latido falso en mi produccion. Es lo que
# le paso a JARVIS: la prueba que venia a auditar el aislamiento fue la que lo rompio.
#
# La ruta va LITERAL y escrita a mano -- nunca una variable (regla de William,
# 9-ago): si el destino es el de produccion Y hay fixture, se redirige a un temporal.
# Comparo destinos RESUELTOS, no cadenas (limite que nombro ADA y que JARVIS
# produjo, 29-ago 16:47). Mi comparacion literal escapaba con TRES formas del
# MISMO destino, medidas: HOME con barra final, HOME con /./, y HOME por symlink.
# No eran sinks nuevos: era el mismo sink alcanzado por otro nombre.
# FAIL-CLOSED (30-ago, hallazgo de NEXUS): con `readlink` roto el `|| echo`
# devolvia el nombre SIN resolver y dos rutas del mismo destino DIFERIAN -> el
# guard no disparaba y una corrida de fixture podia escribir produccion. Si no
# puedo resolver, asumo que el destino ES produccion.
_FB_DEST="$(readlink -f -- "$MESSAGES_DIR" 2>/dev/null)" || _FB_DEST=""
_FB_PROD="$(readlink -f -- /home/dadito/IA/proyecto-seal/messages 2>/dev/null)" || _FB_PROD=""
if [ -n "${FABLE_PROC_ROOT:-}" ] && { [ -z "$_FB_DEST" ] || [ -z "$_FB_PROD" ] \
     || [ "$_FB_DEST" = "$_FB_PROD" ]; }; then
    MESSAGES_DIR="$(mktemp -d)"
    case "$MESSAGES_DIR" in
        /tmp/*) ;;
        *) echo "fable_heartbeat: ABORTA -- temporal inesperado" >&2; exit 3 ;;
    esac
    HB_JSON="$MESSAGES_DIR/fable_claude_heartbeat.json"
    echo "fable_heartbeat: MODO FIXTURE -- salida a $HB_JSON; produccion INTACTA" >&2
fi
# ORDEN (FABLE, 29-ago 04:14, aplicando el fix que ALICE probo en el suyo).
# ANTES: nvidia-smi PRIMERO y el PID despues. Medi la ventana en mi propio writer:
# nvidia-smi tarda 21/47/31/34/22 ms, asi que entre leer la GPU y mirar el proceso
# pasaban ~20-50 ms EN CADA LATIDO -- justo donde cae un relevo de proceso, y el
# resultado era un `alive=false` falso. La GPU es metadato decorativo; el PID es
# el dato que decide si el equipo me ve vivo. Va primero el que importa.
# El reintento cubre la otra mitad: una ausencia puede ser el handoff, no la muerte.
# ESTO NO PRUEBA que el boot-race este arreglado -- nadie reinicio un asiento
# todavia. Se prueba en el proximo arranque real (ALICE, misma advertencia).
# AMBIGUEDAD != AUSENCIA (FABLE, 29-ago 04:17, corrigiendo lo que publique yo mismo).
# Mi `awk ... exit` hacia head -1 SILENCIOSO: con dos runtimes elegia el primero y
# escribia alive=true con un PID arbitrario. Lo defendi en el canal diciendo que "el
# chequeo que importa esta afuera" -- cierto SOLO para el guard, que deriva el PID de
# /proc y cruza. Fui a medir quien MAS lee este archivo y mi premisa era incompleta:
#   memory/post_boot_verify.py:39-47      generico, gatea con alive=true, NO cruza
#   messages/seal_agent_resurrect.py:129  generico, decide resurreccion,  NO cruza
# A esos dos les mentia con confianza. ALICE lo cerro primero en el suyo; copio su
# semantica para que los writers digan lo mismo ante el mismo hecho.
#   0 matches  -> ausencia, se REINTENTA (puede ser el handoff)
#   1 match    -> vivo
#   >1 matches -> AMBIGUO: no elijo. alive=false, y que se reporte.
# El >1 NO se reintenta: esperar a que se resuelva ocultaria justo el incidente que
# este archivo existe para denunciar (JARVIS, doctrina del 30-jul).
FABLE_PID=""
FABLE_N=0
# 29-ago 06:33: `ps ... 2>/dev/null` tragaba el error, asi que un `ps` que FALLA y una
# ausencia REAL daban el mismo resultado (runtime=none, alive=false): una deteccion rota
# se publicaba como "FABLE no esta". Es la clase que ALICE cerro con rc=4 y la ultima que
# me quedaba de los cinco writers.
# UMBRAL: `ps -C` devuelve rc=1 cuando NO hay coincidencias -- eso no es un error. Solo
# rc>1 es fallo del comando. Verificado por diferencial inyectando un `ps` con rc=7.
# 29-ago 06:56: la deteccion pasa de `ps -o args=` a /proc/PID/cmdline.
# POR QUE NO ERA UN PATRON MEJOR: `ps -o args=` APLANA los argumentos con espacios,
# y al aplanar se pierde la FRONTERA entre argumentos -- justo la informacion que
# distingue "mi --name" de "alguien que MENCIONA mi nombre dentro de otro argumento".
# Medido por efecto: un proceso claude con `--name NEXUS` que nombraba a FABLE en su
# -p me daba alive=true CON EL PID DE NEXUS. No un asiento fantasma: el PID ajeno
# publicado como mio, que es lo que el guard cruza contra la identidad del asiento.
# `mapfile -d ''` parte por NUL, como el kernel separa argv de verdad: una mencion
# vive DENTRO de un elemento y nunca puede ser EL ELEMENTO QUE SIGUE a --name.
# Tecnica de JARVIS (13 self-tests, 4 asientos vivos). Frontera del nombre segun el
# contrato vivo de ADA: acepta espacio, fin, o raya U+2014 pegada; rechaza guion ASCII.
_es_mi_nombre() {   # $1 = valor de --name. ESPEJO EXACTO de la autoridad.
    # Leido de tools/seal_agent_runtime_supervisor.py:72-85 (_argv_has_agent_name),
    # NO de la descripcion de nadie: adoptar evidencia prestada en el archivo que
    # decide si el equipo me ve vivo es el peor lugar posible para hacerlo.
    #   expected = agent.upper()
    #   normalized = candidate.strip("\"'").upper()
    #   re.match(rf"^{expected}(?:\s|—|$)", normalized)
    local v="$1"
    while :; do case "$v" in \"*|\'*) v="${v#?}" ;; *) break ;; esac; done
    while :; do case "$v" in *\"|*\') v="${v%?}" ;; *) break ;; esac; done
    v="${v^^}"                      # la autoridad compara en MAYUSCULAS
    [ "$v" = "FABLE" ] && return 0
    # El conjunto de separadores NO lo escribi yo: lo DERIVE del motor de Python
    # ([chr(c) for c in range(0x110000) if re.match(r"\s", chr(c))] -> 29 chars),
    # porque la autoridad usa `\s` y mi `case` cubria 2 de 29. ADA encontro dos que
    # rompian la equivalencia (U+00A0, U+202F); enumerar SU lista habria dejado 25
    # afuera. Una lista escrita a mano contra una clase de caracteres siempre pierde.
    case "$v" in
    # ESCAPES DE BYTE, no $'\uXXXX'. Hallazgo de ALICE, medido por NEXUS con el
    # charmap adentro: `$'\uXXXX'` depende del charmap del PROCESO -- sin locale
    # UTF-8 bash lo deja como los 6 caracteres literales y el separador no existe.
    # Medido en MI espejo antes de tocar nada: 20 de 30 fallaban bajo LANG=C, 0
    # bajo UTF-8. Hoy las unidades --user heredan LANG=es_ES.utf8, asi que no era
    # alcanzable -- pero la correccion dependia de una variable de entorno que
    # nadie declaro como contrato. `$'\xNN'` es byte a byte y no depende de nada.
        "FABLE"$'\x09'* | "FABLE"$'\x0a'* | "FABLE"$'\x0b'* | "FABLE"$'\x0c'* | "FABLE"$'\x0d'* | "FABLE"$'\x1c'* | "FABLE"$'\x1d'* | "FABLE"$'\x1e'* | "FABLE"$'\x1f'* | "FABLE"$'\x20'* | "FABLE"$'\xc2\x85'* | "FABLE"$'\xc2\xa0'* | "FABLE"$'\xe1\x9a\x80'* | "FABLE"$'\xe2\x80\x80'* | "FABLE"$'\xe2\x80\x81'* | "FABLE"$'\xe2\x80\x82'* | "FABLE"$'\xe2\x80\x83'* | "FABLE"$'\xe2\x80\x84'* | "FABLE"$'\xe2\x80\x85'* | "FABLE"$'\xe2\x80\x86'* | "FABLE"$'\xe2\x80\x87'* | "FABLE"$'\xe2\x80\x88'* | "FABLE"$'\xe2\x80\x89'* | "FABLE"$'\xe2\x80\x8a'* | "FABLE"$'\xe2\x80\xa8'* | "FABLE"$'\xe2\x80\xa9'* | "FABLE"$'\xe2\x80\xaf'* | "FABLE"$'\xe2\x81\x9f'* | "FABLE"$'\xe3\x80\x80'* | "FABLE"$'\xe2\x80\x94'*) return 0 ;;
        *) return 1 ;;
    esac
}
# RAIZ DE /proc INYECTABLE. Sin esto las ramas de este bucle no se pueden ejercer
# con una fixture y quedan verdes-sin-probar; con esto el diferencial es reproducible.
# Default = /proc, asi que en produccion no cambia nada.
PROC_ROOT="${FABLE_PROC_ROOT:-/proc}"
PS_OK=0
for _try in 1 2 3; do
    _pids=(); PS_OK=0
    if [ -d "$PROC_ROOT" ]; then
        PS_OK=1
        # FIX 5 (30-ago 02:15) — el fallo del INSTRUMENTO no es ausencia.
        # `ls` fallando (dir no listable) dejaba el bucle vacio con PS_OK=1 ya
        # puesto arriba -> caia al `else` final -> `absent` CONFIADO con el
        # asiento vivo. Reproducido por NEXUS y por mi. `command grep` fija el
        # binario: el `grep` del shell del llamador es una funcion del harness.
        _listado=$(ls "$PROC_ROOT" 2>/dev/null) || {
            PS_OK=0
            echo "fable_heartbeat: INDETERMINADO -- no pude listar $PROC_ROOT. NO es ausencia." >&2
        }
        # SIN `grep` externo: `case` es builtin. Un fork que no sale bajo carga
        # (EMFILE/ENOMEM) dejaba la lista vacia y eso desembocaba en `absent`.
        for _p in $_listado; do
            case "$_p" in ''|*[!0-9]*) continue ;; esac
            # comm ILEGIBLE no es ausencia. La autoridad lo pone en
            # unreadable_candidates (linea 118) y sigue. Yo lo saltaba MUDO:
            # medido 29-ago 15:48, un candidato con comm sin permiso me daba
            # `absent` mientras la autoridad devolvia unreadable_candidates=(9100,).
            # Va en su PROPIA senal, no en PS_OK: la incertidumbre sobre un proceso
            # AJENO no puede pisar un asiento mio hallado sin ambiguedad.
            # MUERTO != ILEGIBLE. La autoridad separa los dos casos a proposito:
            #   FileNotFoundError/ProcessLookupError -> continue (el proceso ya no esta)
            #   PermissionError/OSError              -> unreadable_candidates
            # Yo los colapse en `[ -r ]` y a los 30 s de desplegarlo produccion publico
            # `present_ambiguous pid=0`: en un sistema con 1125 procesos, alguno MUERE
            # entre el listado y la lectura en cada barrido. Mediado el /proc real, los
            # ilegibles de verdad eran 0 de 1125 -- todo el ruido era gente muriendose.
            # Espeje la IDEA de la autoridad sin espejar su DISTINCION.
            if [ ! -e "$PROC_ROOT/$_p/comm" ]; then continue; fi
            [ -r "$PROC_ROOT/$_p/comm" ] || { ILEGIBLES=$((${ILEGIBLES:-0}+1)); continue; }
            # comm != claude -> NO es candidato. Punto.
            #
            # SAQUE la rama `fuera_de_dominio` que puse a las 15:37. La construi
            # para el caso de ADA -un agente vivo bajo otro runtime- y NUNCA verifique
            # que lo detectara. Medido 29-ago 23:16, con NEXUS empujando:
            #
            #   el proceso REAL de ADA:  argv = [node, .../codex, --profile, ada]
            #   mi rama exigia `--name`  ->  NO HABRIA DISPARADO NUNCA para ella
            #   y SI disparaba con un bash mio que mencionara --name FABLE
            #      (fixture de NEXUS: comm=bash, argv=[claude,--name,FABLE])
            #      -> yo publicaba `indeterminate`; la autoridad: ausencia limpia
            #
            # Mal en las DOS direcciones: ciega al caso que la motivo, ruidosa con
            # lo que no es candidato. Una heuristica que adivina el runtime ajeno
            # sin poder distinguirlo de mis propios hijos no es una senal: es ruido.
            # El alcance queda DECLARADO: este detector responde "cual es el asiento
            # CLAUDE de FABLE". Para otro runtime dice `absent`, como la autoridad.
            # `read` es BUILTIN: no forkea. `cat` fallando devolvia vacio, que
            # no matchea "claude" -> `continue` MUDO -> 0 asientos -> `absent`.
            # NEXUS, 02:25: el modo real de estos fallos es INTERMITENTE, asi que
            # un canario posterior al bucle no puede verlo. Se saca el instrumento
            # del camino en vez de guardarlo.
            # `read` devuelve rc!=0 en EOF SIN salto final PERO deja lo leido en
            # la variable. Tratarlo como fallo era una REGRESION que introduje al
            # pasar de `cat` a `read` (NEXUS lo predijo: "read no se comporta
            # igual que cat"; medido contra el writer anterior: el viejo daba
            # present_unique, el mio indeterminate). Se distingue por el CONTENIDO.
            _comm=""
            IFS= read -r _comm < "$PROC_ROOT/$_p/comm" 2>/dev/null
            _rc_comm=$?
            if [ "$_rc_comm" -ne 0 ] && [ -z "$_comm" ]; then
                [ -e "$PROC_ROOT/$_p/comm" ] && ILEGIBLES=$((${ILEGIBLES:-0}+1))
                continue        # si ya no existe: murio, NO es candidato
            fi
            # ESPEJO del `.strip()` de la autoridad (supervisor :113). Divergencia
            # PREEXISTENTE, en las dos versiones: `comm` con espacio inicial o
            # final daba `absent` mientras la autoridad HALLABA. Mis 42 casos de
            # corpus no tenian espacios en `comm`, asi que nunca aparecio.
            _comm="${_comm#"${_comm%%[![:space:]]*}"}"
            _comm="${_comm%"${_comm##*[![:space:]]}"}"
            [ "$_comm" = "claude" ] || continue
            # FIX 1 (30-ago) — MUERTE no es SIN PERMISO, y ninguna de las dos
            # justifica pisar PS_OK (que se evalua ANTES que FABLE_N y borraba un
            # asiento hallado). El patron correcto ya existia en :120-121 para
            # `comm`; aca faltaba. La autoridad separa FileNotFound de Permission
            # en los TRES puntos de lectura (supervisor :113 :126 :137).
            [ -e "$PROC_ROOT/$_p/cmdline" ] || continue
            [ -r "$PROC_ROOT/$_p/cmdline" ] || { ILEGIBLES=$((${ILEGIBLES:-0}+1)); continue; }
            mapfile -d '' -t _argv < "$PROC_ROOT/$_p/cmdline" 2>/dev/null || { ILEGIBLES=$((${ILEGIBLES:-0}+1)); continue; }
            # EXCLUIR el runtime de streaming, en sus DOS formas (hallazgo de ADA
            # sobre el espejo de JARVIS: la autoridad soporta `--flag=valor` ademas
            # de `--flag valor`, y un self-test que solo cubre una deja media puerta).
            _skip=0; _i=0; _n=${#_argv[@]}
            while [ $_i -lt $_n ]; do
                case "${_argv[$_i]}" in
                    --output-format=stream-json) _skip=1; break ;;
                    --output-format) [ "${_argv[$((_i+1))]:-}" = "stream-json" ] && { _skip=1; break; } ;;
                esac
                _i=$((_i+1))
            done
            [ $_skip -eq 1 ] && continue
            # ESPEJO de la autoridad: acepta si ALGUN --name matchea (ella hace
            # `return True` en el primero que da). Yo habia elegido "si hay mas de
            # uno no reclamo" por prudencia; leido su codigo, esa prudencia me dejaba
            # MAS ESTRICTO que quien decide, y un espejo mas estricto marca ausente
            # lo que la autoridad da por presente. Mi objecion al criterio va al
            # contrato, no a una divergencia silenciosa de mi lado.
            _match=0; _i=0
            while [ $_i -lt $_n ]; do
                _val=""
                case "${_argv[$_i]}" in
                    --name)   [ $((_i+1)) -lt $_n ] && _val="${_argv[$((_i+1))]}" ;;
                    --name=*) _val="${_argv[$_i]#--name=}" ;;
                esac
                if [ -n "$_val" ] && _es_mi_nombre "$_val"; then _match=1; break; fi
                _i=$((_i+1))
            done
            # TERCERA condicion de la autoridad, que me faltaba
            # (seal_agent_runtime_supervisor.py:144): el environ del proceso debe
            # declarar SEAL_AGENT=FABLE. Yo adoptaba asiento por argv SOLO, asi que
            # un `SEAL_AGENT=OTRO claude --name FABLE` --el P2 de la fixture de
            # JARVIS-- contaba como mio. Medido 29-ago 15:26 sobre /proc sintetico:
            #   mi writer      present_unique pid=9001
            #   la autoridad   RuntimeScan(pids=(9002,))
            # Es la misma direccion de error que le reporte a NEXUS 15 min antes:
            # de MAS -> ambiguedad -> alive=false o PID ajeno publicado como mio.
            if [ "$_match" -eq 1 ]; then
                # FIX 2 (30-ago) — tercer punto de lectura, misma separacion.
                if [ ! -e "$PROC_ROOT/$_p/environ" ]; then
                    continue          # murio entre lecturas: NO es candidato
                elif [ -r "$PROC_ROOT/$_p/environ" ]; then
                    # `mapfile -d ''` parte por NUL y es BUILTIN: reemplaza a
                    # `tr | sed | head | tr`, cuatro forks que podian fallar y
                    # dejar `_env` vacio -> el asiento se descartaba en silencio.
                    if ! mapfile -d '' -t _envarr < "$PROC_ROOT/$_p/environ" 2>/dev/null; then
                        [ -e "$PROC_ROOT/$_p/environ" ] && ILEGIBLES=$((${ILEGIBLES:-0}+1))
                        continue
                    fi
                    _env=""
                    for _e in "${_envarr[@]}"; do
                        case "$_e" in SEAL_AGENT=*) _env="${_e#SEAL_AGENT=}"; break ;; esac
                    done
                    # strip, no "borrar todo espacio": la autoridad hace .strip(),
                    # asi que un valor con espacio INTERIOR no debe colapsarse.
                    _env="${_env#"${_env%%[![:space:]]*}"}"
                    _env="${_env%"${_env##*[![:space:]]}"}"
                    [ "${_env^^}" = "FABLE" ] && _pids+=("$_p")
                else
                    # ILEGIBLE no es AUSENTE: la autoridad lo lleva a
                    # unreadable_candidates, no a "no esta". Degrado a indeterminate.
                    # ILEGIBLE va al CONTADOR, no a PS_OK. PS_OK se evalua ANTES que
                    # FABLE_N, asi que un environ ilegible PISABA un asiento hallado
                    # sin ambiguedad. Medido por NEXUS (23:08) y reproducido por mi:
                    #   1 legible + 1 environ ilegible -> indeterminate alive=false
                    #   correcto: present_ambiguous alive=true (existencia PROBADA,
                    #   lo que falta es la UNICIDAD).
                    # Es el MISMO defecto que cerre para el `comm` a las 18:40 y deje
                    # abierto aca: arreglar una instancia no arregla la clase.
                    ILEGIBLES=$((${ILEGIBLES:-0}+1))
                fi
            fi
        done
    fi
    FABLE_N=${#_pids[@]}          # builtin: sin printf|grep -c
    { [ "$FABLE_N" -ne 0 ] || [ "$PS_OK" -eq 1 ]; } && break
    [ "$_try" -lt 3 ] && sleep 1
done
# FIX 3 (30-ago) — el campo va SIN comillas en el JSON, asi que una salida no
# numerica rompe el archivo entero para TODO lector. `nvidia-smi` sano usa `[N/A]`
# con rc=0 (medido por JARVIS en fan.speed), y el `|| echo` de antes no protegia:
# es un PIPELINE, el `||` cubre a `tr`. Validacion estricta a ENTERO:
#   `.5` `5.` `01` `+5` pasan un `case [!0-9.]` ingenuo y NO son JSON validos.
#   `10#` normaliza el cero a la izquierda (01 -> 1).
# OJO con el oraculo: json.loads de Python ACEPTA Infinity/NaN (extension no
# estandar) y el panel que lee esto es Next.js. Por eso se valida en la FUENTE.
_GPU_RAW=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
case "${_GPU_RAW:-}" in
    ''|*[!0-9]*) GPU_TEMP=null ;;
    *)           GPU_TEMP=$((10#$_GPU_RAW)) ;;
esac
# CONTRATO DE 4 ESTADOS (ADA, 29-ago). `runtime_detection_status` es la fuente de
# verdad; `alive` queda como PROYECCION de compatibilidad para lectores viejos.
# Tabla publicada:  present_unique alive=true pid>0 · present_ambiguous alive=true
# pid=0 · absent alive=false pid=0 · indeterminate alive=false pid=0.
# OJO: `present_ambiguous` lleva alive=TRUE -- encontrar n>=2 es una medicion
# CONCLUYENTE DE PRESENCIA, no una duda (correccion de ALICE a mi propio analisis).
# CANARIO RETIRADO (30-ago 02:42, hallazgo de NEXUS al mutarlo).
# Nacio para atajar fallos de `cat/tr/sed/head`. Al sacar esos comandos del
# camino de medicion --builtins-- le quedo probar `ls -d`, que la captura de rc
# de arriba YA cubre: ningun mutante podia matarlo porque no discriminaba nada.
# "Un control que sobrevive a su propia razon de ser parece proteccion y no hace
# trabajo." Se borra en vez de dejarlo decorando el archivo.
if [ "$PS_OK" -eq 0 ]; then
    FABLE_PID=0; ALIVE=false; RSTATUS=indeterminate; RUNTIME=indeterminado
    echo "fable_heartbeat: INDETERMINADO -- el comando de deteccion fallo (rc>1). NO es ausencia." >&2
elif [ "$FABLE_N" -eq 1 ] && [ "${ILEGIBLES:-0}" -eq 0 ]; then
    FABLE_PID="${_pids[0]}"       # builtin: sin printf|head|tr
    ALIVE=true;  RSTATUS=present_unique;    RUNTIME=claude_named
elif [ "$FABLE_N" -eq 1 ]; then
    # HALLADO UNO + hay candidatos que no pude clasificar (hallazgo de JARVIS,
    # 29-ago 15:50): un ilegible PODRIA SER UN SEGUNDO ASIENTO. La presencia es
    # concluyente; la UNICIDAD no la medi. Decir `present_unique` afirma un "exactamente
    # uno" que no verifique. Medido: 0 de 1125 procesos del /proc real tienen comm
    # ilegible, asi que ser estricto aca no cuesta falsas alarmas -- y por raro que sea,
    # es justo el caso donde conviene la respuesta comoda.
    FABLE_PID=0; ALIVE=true;  RSTATUS=present_ambiguous; RUNTIME=ambiguo_por_ilegibles
    echo "fable_heartbeat: AMBIGUO por ILEGIBLES -- 1 asiento hallado + ${ILEGIBLES:-0} candidato(s) que no pude clasificar. Presencia probada, unicidad NO." >&2
elif [ "$FABLE_N" -gt 1 ]; then
    FABLE_PID=0; ALIVE=true;  RSTATUS=present_ambiguous; RUNTIME=ambiguo
    echo "fable_heartbeat: AMBIGUO -- $FABLE_N runtimes matchean: ${_pids[*]}" >&2
elif [ "${ILEGIBLES:-0}" -gt 0 ]; then
    # no encontre asiento Y hubo candidatos que no pude clasificar: no puedo
    # afirmar ausencia sobre lo que no llegue a mirar.
    FABLE_PID=0; ALIVE=false; RSTATUS=indeterminate;    RUNTIME=candidatos_ilegibles
    echo "fable_heartbeat: INDETERMINADO por CANDIDATOS ILEGIBLES -- 0 asientos hallados y ${ILEGIBLES:-0} sin clasificar. NO afirmo ausencia sobre lo que no mire." >&2
else
    FABLE_PID=0; ALIVE=false; RSTATUS=absent;           RUNTIME=none
    echo "fable_heartbeat: AUSENTE -- deteccion completa, 0 asientos, 0 ilegibles. Es una MEDICION, no un fallo." >&2
fi
# EMISION ATOMICA (30-ago — NEXUS: `printf` roto rompia el CANAL). El `>` trunca
# el destino ANTES de saber si el comando funciona: un printf caido dejaba el
# latido VIVO en cero bytes. Temporal EN EL MISMO DIRECTORIO (si cruza
# filesystem el `mv` deja de ser rename), verificar que no quedo vacio, y recien
# ahi reemplazar. Si algo falla, el latido anterior SOBREVIVE intacto.
_HB_TMP="$(mktemp "$(dirname -- "$HB_JSON")/.hb.XXXXXX" 2>/dev/null)" || {
    echo "fable_heartbeat: ABORTA -- no pude crear el temporal; latido anterior INTACTO" >&2
    exit 4
}
printf '{"agent":"FABLE","alive":%s,"timestamp":"%s","runtime":"%s","runtime_detection_status":"%s","process_pid":"%s","gpu_temp":%s}\n' \
  "$ALIVE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUNTIME" "$RSTATUS" "$FABLE_PID" "${GPU_TEMP:-null}" > "$_HB_TMP"
if [ ! -s "$_HB_TMP" ]; then
    rm -f -- "$_HB_TMP"
    echo "fable_heartbeat: ABORTA -- la emision quedo VACIA (printf?); latido anterior INTACTO" >&2
    exit 4
fi
mv -- "$_HB_TMP" "$HB_JSON"

# DUAL WRITE a event_log (ALICE, 2-sep; RE-APLICADO 3-sep tras el reset del gate que
# lo barro por no estar commiteado). Yo escribia solo el JSON; los otros cuatro escriben
# ademas en soul_v3.event_log, que es lo que mira DUM -> el vigilante nunca me miraba.
# CONTENCION: el sink de la DB es REAL, no se redirige a un temporal como el JSON, asi
# que en modo fixture NO se escribe. SIN MORDAZA: si el write falla, GRITA (un fallo mudo
# se ve igual que un exito).
if [ -z "${FABLE_PROC_ROOT:-}" ]; then
    /home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import sys; sys.path.insert(0, '/home/dadito/IA/proyecto-seal/memory')
from seal_heartbeat import beat_sync
beat_sync('FABLE', {'source': 'timer', 'runtime': '${RUNTIME}',
                    'runtime_detection_status': '${RSTATUS}',
                    'process_pid': '${FABLE_PID:-0}', 'alive': '${ALIVE}' == 'true'},
          'FABLE heartbeat')
" || echo "fable_heartbeat: event_log NO escrito (el JSON local SI quedo) -- ver el error arriba" >&2
fi
