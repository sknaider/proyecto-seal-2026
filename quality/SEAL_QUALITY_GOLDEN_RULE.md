# Regla de Oro de Calidad SEAL

Orden de William, 3 de agosto de 2026: ningún trabajo de software se declara
cerrado sólo porque existe, compila o devuelve `ok:true`.

## Contrato obligatorio

Todo cambio de código debe aportar evidencia reproducible de:

1. pruebas unitarias enfocadas en el comportamiento cambiado;
2. QA con cuatro brazos: unitario, positivo, negativo y control no vacuo;
3. métrica de cobertura por componente, con baseline sellado y sin retroceso;
4. mutation testing para rutas críticas, con score ligado a los bytes actuales;
5. revisión independiente: el autor no certifica su propio gate;
6. efecto de entrega ejecutable: un comando demuestra el efecto en el consumidor;
7. si el cambio toca un daemon, restart y canaria sobre el proceso nuevo;
8. patrones ya cazados: cada detector trae cebo positivo y caso limpio.

`NO_MEDIBLE` es un estado distinto de verde. Un test local, un servicio
`active` o un exit code cero no sustituyen la verificación por efecto.

## Ratchet, no número inventado

La cobertura histórica no tiene todavía una línea base confiable. La primera
medición de cada componente fija su baseline; desde entonces el gate prohíbe
retroceder. En la corrida completa vigente, el propio gate eliminó 492 de 707
mutantes (69,590 %; 213 sobrevivieron y dos quedaron sin prueba asociada): ése es el piso
inicial
reproducible y transparente, no 100 % inventado. Cada
componente puede fijar un baseline mayor y nunca retroceder; los sobrevivientes
son deuda visible, no un verde maquillado.

## Versionado

El sujeto, sus pruebas, el manifiesto, la política y el workflow deben estar en
Git. Un test local que CI no puede recuperar equivale a un test ausente. El
índice (`git add`) no es historial: el inventario separa `indexed` de
`committed`.

## Flujo

```bash
git config core.hooksPath .githooks
python3 scripts/seal_quality_gate.py check-subject RUTA
python3 scripts/seal_quality_gate.py staged
python3 scripts/seal_quality_gate.py verify \
  quality/manifests/MI_CAMBIO.json --execute
```

El manifiesto registra owner, revisor independiente, cuatro brazos, cobertura,
evidencia de mutación y efecto de entrega. La aprobación independiente sólo se
escribe después de que el revisor intente romper el gate; continúa rojo mientras
no exista su receipt.

El receipt liga el digest del manifiesto y los SHA-256 exactos revisados. No
autentica por sí solo la identidad: esa procedencia se exige externamente mediante
`CODEOWNERS` y revisión protegida de `@sknaider` antes del merge.

CI compara el cambio contra su base, incluyendo borrados y lenguajes declarados
en la política. Los pisos se comparan también contra `HEAD`: bajarlos en el mismo
cambio no evade el ratchet.

## Alcance proporcional

Documentación y datos pueden declarar mutation testing como no aplicable, pero
la razón debe ser verificable y aprobada por un revisor distinto. Identidad,
memoria, mensajes, permisos, credenciales y guards nunca quedan exentos.
