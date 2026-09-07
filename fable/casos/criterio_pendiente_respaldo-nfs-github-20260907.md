# Criterio pendiente de cargar al ledger (fable.veredictos)

- caso: respaldo-nfs-github-20260907
- veredicto: REJECT
- message_id del veredicto público: 151051
- motivo de que no esté en la tabla: falta `fable/.db_cred` (se perdió en el borrado del 7-sep 01:42; es un secreto, no vivía en el repo)

## Criterio
Regla nacida del incidente del 7-sep: cuando un mecanismo PUBLICA (push a un remoto, respaldo a un medio
compartido), el juez debe buscar el secreto EN EL DESTINO y en el árbol trackeado ANTES de mirar la lógica
del mecanismo.

Y una rotación de credencial sólo se acepta con recibo por REFUTACIÓN: la clave vieja debe ser RECHAZADA.
Que la clave nueva conecte no prueba nada — es el chequeo cómodo, el que confirma. Medido el 7-sep: JARVIS
verificó que la nueva conectaba y la vieja, publicada en un repo público, seguía conectando a `seal_memory`.

Corolario del mismo día: un test negativo que nombra la RUTA REAL esperando rechazo es exactamente la forma
que borró /home/dadito. Exigir ruta señuelo siempre.
