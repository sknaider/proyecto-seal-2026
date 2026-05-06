---
auto_invoke: true
name: react_optimization
description: Colección de reglas de oro y mejores prácticas de ingeniería de Vercel para optimizar aplicaciones React y Next.js.
---

# Optimización de React y Next.js (Vercel Engineering)

Esta skill aplica las lecciones aprendidas por el equipo de ingeniería de Vercel para escalar aplicaciones React.

## 1. Eliminar "Waterfalls" de Datos
**Problema:** Componentes anidados que esperan a que el padre termine de cargar datos antes de empezar ellos.
**Solución:**
- Mueve el data-fetching al nivel más alto posible (ej. Page/Layout).
- Usa `Promise.all` para peticiones paralelas.
- En Next.js App Router, los Server Components precargan datos en paralelo por defecto si no se bloquean entre sí.

## 2. Optimización de Bundle (Cliente)
**Regla:** El código más rápido es el que no se envía.
- **Server Components:** Mueve dependencias pesadas (ej. formateadores de fecha, sanitizadores markdown) a Server Components. No se envían al bundle del cliente.
- **Dynamic Imports:** Usa `next/dynamic` o `React.lazy` para componentes pesados que no son visibles inicialmente (ej. Modales, Gráficos complejos).
  ```tsx
  const HeavyChart = dynamic(() => import('./HeavyChart'), { loading: () => <Spinner /> })
  ```

## 3. Renderizado y Re-rrenders
- **Composición:** Evita "Prop Drilling". Usa composición (pasar componentes como `children`) para evitar que componentes padres re-rendericen a todos sus hijos innecesariamente.
- **Estado Local:** Mantén el estado lo más cerca posible de donde se usa. No subas el estado al layout global si solo lo usa un botón.

## 4. Core Web Vitals
- **LCP (Largest Contentful Paint):** El elemento más grande (imagen/texto) debe cargar rápido. Priorízalo con `<Image priority />` en Next.js.
- **CLS (Cumulative Layout Shift):** SIEMPRE define dimensiones (width/height) para imágenes y contenedores. Usa fuentes optimizadas (`next/font`).

## 5. Hooks y Efectos
- **Evita `useEffect` para datos:** En Next.js App Router, PREFIERE fetch en Server Components.
- Si usas `useEffect` solo para sincronizar estado con props, probablemente lo estás haciendo mal. Calcula valores durante el renderizado o usa `useMemo`.
