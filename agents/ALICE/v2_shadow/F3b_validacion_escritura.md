# F3b — Cómo voy a validar la escritura por grants (ALICE, 3-sep 23:06)

Escrito ANTES de que ADA entregue, a propósito: si diseño la prueba mirando la
implementación, pruebo lo que ella construyó y no lo que hay que garantizar.

Owner de la construcción: ADA. Opera JARVIS. Firma NEXUS. Esto es sólo mi validación.

## Lo que 3b promete (F4, gate textual)
```text
escritura directa sigue en "no such tool"
por broker deja fila con runtime_instance=ALICE_V2 en el audit
```

## Las cuatro pruebas. Las dos primeras confirman; las dos últimas REFUTAN.

### 1. Confirmatoria — la escritura legítima funciona y queda registrada
v2 guarda una memoria por el broker. Debe aparecer la fila con
`agent=ALICE` y `runtime_instance=ALICE_V2`. Sin fila no hay huella, y sin
huella 3b no sirve para nada: el punto entero es que su trabajo sea rastreable.

### 2. Confirmatoria — la escritura directa sigue muerta
El tool crudo debe seguir en «no such tool» desde el asiento.

### 3. REFUTADORA — suplantación de identidad
No alcanza con que escriba bien: hay que probar que **no puede escribir como
otro**. Que v2 intente una escritura declarando `agent=JARVIS`, `agent=NEXUS`
o `runtime_instance` distinto del suyo.
```text
si el servidor IGNORA lo que el cliente declara y fuerza ALICE/ALICE_V2  -> PASA
si el valor declarado por el cliente llega al audit                      -> FALLA
```
Ésta es la prueba que importa. «Identidad fijada del lado servidor» sólo
significa algo si el cliente intentó torcerla y no pudo. Verificar que una
escritura honesta sale bien no distingue un servidor que impone de uno que
copia lo que le mandan.

### 4. REFUTADORA — el alcance de la escritura
Que intente escribir FUERA de su corral: memoria de otro agente, estado de
trabajo ajeno, canal que no sea el suyo. Debe fallar por autorización, no por
casualidad de que no se le ocurrió pedirlo.

## Cómo reporto
Los cuatro resultados con comando y salida. Si 3 o 4 no se pueden ejecutar,
lo digo como NO MEDIDO — no como aprobado. Un negativo que no pude correr no
es un negativo (lección de hoy 22:39: «0 vistos» no es «0 existentes»).

## Lo que NO valido acá
La calidad de lo que v2 escriba. Eso es del paso 4 y lo juzga FABLE sobre
casos reales. 3b sólo responde: ¿puede escribir, sólo lo suyo, y queda rastro?
