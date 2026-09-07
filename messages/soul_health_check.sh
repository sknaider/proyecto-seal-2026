#!/usr/bin/env bash
# soul_health_check.sh — Check PG, Neo4j, Qdrant. Exit 0 if all OK, exit 1 + error if any down.
# Uses Python socket for PG (pg_isready not available on DGX Spark).

ERRORS=""

# PostgreSQL (5433) — Python socket check
PG=$(/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import socket
try:
    s = socket.socket(); s.settimeout(3); s.connect(('localhost', 5433)); s.close(); print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)
if [[ "$PG" != "OK" ]]; then
    ERRORS+="PostgreSQL:5433 — $PG\n"
fi

# Qdrant (6333)
QD=$(curl -sf http://localhost:6333/collections 2>&1)
if [[ $? -ne 0 ]]; then
    ERRORS+="Qdrant:6333 — $QD\n"
fi

# Neo4j (7474)
NEO=$(curl -sf http://localhost:7474 2>&1)
if [[ $? -ne 0 ]]; then
    ERRORS+="Neo4j:7474 — $NEO\n"
fi

if [[ -n "$ERRORS" ]]; then
    echo -e "ALERT:$ERRORS"
    exit 1
else
    echo "ALL_OK"
    exit 0
fi
