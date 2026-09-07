#!/bin/bash
# BUILD SOUL en DaditoGamer (OPCIÓN D: contenedor byte-idéntico a central, sin sudo).
# Receta EXACTA copiada de central (docker inspect seal-memory-db): imagen, user=seal, db=seal_memory.
# Seguridad FABLE: bind SOLO 127.0.0.1 (#1); borrar el dump plano tras restore (#2).
# Targets de verificación (central): memories=103476, soul_v3 tablas=176, fable.emotional=32, agentes=43.
set -u
IMAGE=timescale/timescaledb-ha:pg17
DUMP=/home/dadito/soul_full.dump
CNAME=seal-memory-db
echo "=== BUILD SOUL (OPCIÓN D) — inicio ==="

echo "-- 1) pull imagen (misma que central) --"
docker pull "$IMAGE" 2>&1 | tail -2

echo "-- 2) correr contenedor (bind SOLO 127.0.0.1 = FABLE#1; user/db = central) --"
docker rm -f "$CNAME" 2>/dev/null || true
docker run -d --name "$CNAME" \
  -e POSTGRES_USER=seal \
  -e POSTGRES_DB=seal_memory \
  -e POSTGRES_PASSWORD=seal_local_soul_pw \
  -p 127.0.0.1:5432:5432 \
  -v seal-pg-data:/home/postgres/pgdata/data \
  "$IMAGE" >/dev/null
sleep 2
echo "  $(docker ps --filter name=$CNAME --format '{{.Names}} | {{.Status}} | {{.Ports}}')"

echo "-- 3) esperar Postgres ready --"
for i in $(seq 1 40); do
  if docker exec "$CNAME" pg_isready -U seal -d seal_memory >/dev/null 2>&1; then echo "  ready tras $((i*3))s"; break; fi
  sleep 3
done

echo "-- 4) restaurar el ALMA COMPLETA (dump exacto de central) --"
docker cp "$DUMP" "$CNAME":/tmp/soul_full.dump
docker exec "$CNAME" pg_restore -U seal -d seal_memory --no-owner --no-privileges /tmp/soul_full.dump 2>&1 | tail -12
echo "  (warnings de pg_restore son normales; lo que vale es el conteo por efecto abajo)"

echo ""
echo "=== VERIFICACIÓN POR EFECTO (copia EXACTA vs central) ==="
echo -n "extensiones: "; docker exec "$CNAME" psql -U seal -d seal_memory -tAc "SELECT string_agg(extname||' '||extversion,', ' ORDER BY extname) FROM pg_extension"
echo -n "soul_v3 tablas (central=176): "; docker exec "$CNAME" psql -U seal -d seal_memory -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='soul_v3' AND table_type='BASE TABLE'"
echo -n "memories total (central=103476): "; docker exec "$CNAME" psql -U seal -d seal_memory -tAc "SELECT count(*) FROM soul_v3.memories"
echo -n "fable.emotional_memory (central=32): "; docker exec "$CNAME" psql -U seal -d seal_memory -tAc "SELECT count(*) FROM fable.emotional_memory"
echo -n "agentes (central=43): "; docker exec "$CNAME" psql -U seal -d seal_memory -tAc "SELECT count(*) FROM soul_v3.agents"
echo -n "schemas app: "; docker exec "$CNAME" psql -U seal -d seal_memory -tAc "SELECT string_agg(schema_name,', ') FROM information_schema.schemata WHERE schema_name IN ('soul_v3','fable','gtl','ocean_test')"

echo ""
echo "-- 5) SEGURIDAD: borrar dump plano (FABLE#2) --"
docker exec "$CNAME" rm -f /tmp/soul_full.dump && echo "  dump borrado del container ✓"
rm -f "$DUMP" && echo "  dump borrado del host WSL2 ✓"
echo "-- 6) bind check (debe ser 127.0.0.1, FABLE#1) --"
docker port "$CNAME"
echo "=== BUILD SOUL — fin ==="
