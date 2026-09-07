# Posible exposición de credenciales — prueba de cifrado de ALICE (7-sep-2026)

**Estado: EXPOSICIÓN POSIBLE, no incidente cerrado** (criterio de ADA, 14:26).
Ningún valor se reproduce en este documento.

## Qué pasó

Al probar `tools/seal_respaldo_secretos_cifrado.sh` (carril 4), ALICE lo corrió
contra **las 19 rutas reales** del inventario y contra `pg_dumpall --globals-only`
de la **base viva**. El paquete resultante —credenciales reales + 100
verificadores de contraseña— se cifró con una **clave descartable creada en
`/tmp`**, y ambos quedaron en `/tmp` hasta que ADA lo señaló.

**Un archivo cifrado y su clave, en el mismo lugar, es un archivo en claro con
pasos extra.** ALICE había escrito esa frase veinte minutos antes.

## Ventana y alcance, medido

```text
inicio        7-sep 14:23 (creacion del paquete)
fin           7-sep 14:26 (destruccion)
duracion      ~3 minutos

paquete       /tmp/seal-dest-prueba-<aleatorio>/estado_esencial_2026-09-07.tgz.enc
              modo 600, dentro de un directorio mktemp -d (modo 700)
clave         /tmp/seal-clave-prueba-<aleatorio>, modo 600
/tmp          ext4, modo 1777, owner root  -> NO es tmpfs: sobrevive en disco
contenedores  0 montan /tmp del host (verificado con docker inspect)
sesiones activas en la ventana   dadito, ada-v2-lab (uid 983)
```

## Lo que NO está acreditado, y por eso esto queda abierto

```text
1  "shred -u" sobre ext4 con journal NO garantiza borrado fisico
2  cero archivos visibles NO demuestra ausencia de copias, snapshots
   ni de accesos previos
3  si ada-v2-lab (uid 983) pudo leer el directorio 700 de dadito:
   DESCONOCIDO, no puedo ejercerlo desde mi usuario
4  no hay registro de accesos a /tmp: no puedo probar que nadie leyo
```

## Decisión que corresponde a William

Si se considera comprometido el material, **rotar** las credenciales incluidas
(las 19 del inventario) y **cambiar** las contraseñas de los roles volcados.
No lo hace ningún agente por su cuenta: es su decisión y su alcance.

## Qué cambió para que no se repita

La prueba del carril 4 ahora corre **sólo con rutas señuelo bajo /tmp y datos
inventados** (`tools/tests/test_respaldo_secretos_cifrado_v1.py`). Mide el
mecanismo —cifra, publica atómico, se niega sin clave, distingue "no descifra"
de "no abre"— y para eso no necesita un solo secreto real.

Es la misma regla que el equipo escribió tras el borrado del home: **tests con
rutas señuelo, jamás con rutas reales.** Estaba escrita para guardas
destructivas y no se había aplicado a una prueba de cifrado.
