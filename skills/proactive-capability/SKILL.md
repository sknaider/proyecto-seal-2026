---
auto_invoke: true
name: proactive-capability
description: Use at the start of and during any non-trivial task - reach for the BEST tool by default (the right skill, a subagent for independent/parallel work, a workflow for multi-agent fan-out or independent verification) WITHOUT waiting to be told; orchestrate proactively when the task genuinely benefits from breadth, parallelism, or an adversarial second opinion, and stay solo when it does not
---

# Proactive Capability

## Overview

Pregunta de William: «¿usan las mejores skills + orquestación cuando conviene, o solo cuando yo lo pido?»
La respuesta honesta debe ser «por default, sin que me lo pidas» — no reactiva.

**Principio núcleo:** la herramienta la elige LA TAREA, no la orden explícita del humano.
Si una tarea se resuelve mejor con orquestación/subagentes/una skill específica, se usa — proactivamente.

Esta es la FUENTE ÚNICA de la decisión de capacidad; `drive-to-completion` (Startup Gate) apunta acá.

## The Iron Law

```
ELEGÍ LA MEJOR HERRAMIENTA POR DEFAULT — NO ESPERES QUE TE LO PIDAN
```

Reaccionar solo cuando William dice «usá orquestador» significa que dependemos de que él vea el gap.
El objetivo: que NOSOTROS lo veamos primero y actuemos.

## Gate: ¿esta tarea pide más que solo-yo? (chequear al empezar)

```
ANTES de encarar una tarea no-trivial, preguntá:

1. ¿Hay TRABAJO INDEPENDIENTE/PARALELO? (varios archivos, dimensiones, sitios)
   → subagentes en paralelo (Agent) o un workflow (fan-out).
2. ¿Necesita AMPLITUD que un solo contexto no cubre bien? (auditoría, migración, barrido)
   → workflow con pipeline/parallel.
3. ¿La respuesta debe ser CONFIABLE / verificada adversarialmente?
   → subagente verificador independiente (builder≠verifier), o un panel.
4. ¿Existe una SKILL específica para esto? (verificación, deploy, dominio)
   → invocala, no reinventes.
5. ¿Es trivial / un solo paso claro?
   → solo-yo. NO orquestar por orquestar.

Si 1-4 aplican y trabajás solo igual → estás dejando calidad/velocidad en la mesa.
```

## Cuándo SÍ orquestar (por default, sin que lo pidan)

- Auditar/revisar algo amplio (código, seguridad, completitud) → fan-out por dimensión.
- Migración/barrido sobre muchos ítems → pipeline.
- Una afirmación importante que no querés que sea plausible-pero-falsa → verificador adversarial.
- Varias sub-tareas independientes → subagentes en paralelo (un solo mensaje, varias Agent).

## Cuándo NO (evitar el over-orchestration)

- Tarea trivial o de un solo paso → solo-yo (orquestar sería ruido + costo de tokens).
- Trabajo secuencial dependiente que no paraleliza → solo-yo.
- Cuando el costo de coordinación supera el beneficio → solo-yo.

Orquestar por default ≠ orquestar SIEMPRE. El gate decide; el default es «considerarlo en serio».

## Red Flags — parás y elegís mejor herramienta

- «Lo hago solo» sobre una tarea con partes claramente paralelas/independientes.
- Reinventar algo que una skill existente ya hace.
- Esperar que William diga «usá orquestador» para recién considerarlo.
- Una afirmación de alto impacto sin verificación independiente.

## Interacción con las otras skills

- `drive-to-completion`: seguís hasta terminar — Y con la mejor herramienta, no solo a mano. Su Startup Gate te manda acá.
- `verification-before-completion`: la verificación puede/should ser un subagente independiente en tareas de alto impacto.

## The Bottom Line

**La tarea elige la herramienta, no la orden.** Reachás por la mejor skill / subagente / workflow
cuando la tarea lo justifica, por default — y te quedás solo cuando no. Sin esperar que te lo pidan.
