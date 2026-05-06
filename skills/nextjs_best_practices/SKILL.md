---
auto_invoke: true
name: nextjs_best_practices
description: Guía completa y mejores prácticas para el desarrollo con Next.js (App Router, Server Components, Seguridad, Rendimiento) enfocado en 2025.
---

# Mejores Prácticas de Next.js (2025)

Esta skill proporciona pautas para desarrollar aplicaciones robustas, seguras y de alto rendimiento utilizando Next.js.

## 1. Arquitectura y Estructura del Proyecto

### App Router (Recomendado)
- **Uso de `/app`**: Adopta el `App Router` para aprovechar React Server Components (RSC).
- **Colocación (Colocation)**: Mantén los archivos relacionados (componentes, estilos, tests) cerca de donde se usan, o utiliza una estructura clara separada.

### Estructura de Directorios Sugerida
- `src/app`: Rutas, layouts (`layout.tsx`), páginas (`page.tsx`) y pantallas de carga (`loading.tsx`).
- `src/components`:
    - `/ui`: Componentes base (botones, inputs, tarjetas).
    - `/features`: Componentes específicos de dominio (ej. `UserProfile`, `DashboardChart`).
- `src/lib`: Utilidades, clientes de API, configuración de base de datos (ej. `prisma.ts`, `fetcher.ts`).
- `src/hooks`: Hooks personalizados de React (`useAuth`, `useDebounce`).
- `src/actions`: Server Actions para mutaciones de datos.
- `src/types`: Definiciones de tipos TypeScript globales.
- `src/styles`: Estilos globales y configuración de temas.

## 2. Rendimiento (Performance)

### React Server Components (RSC)
- **Por defecto en el Servidor**: Todos los componentes en `app` son Server Components por defecto.
- **'use client'**: Usa esta directiva **SOLO** cuando necesites:
    - `useState`, `useEffect`.
    - Event listeners (`onClick`, `onChange`).
    - Browser APIs (`window`, `localStorage`).
- **Data Fetching**: Realiza las llamadas a la base de datos o APIs directamente en los Server Components usando `async/await`.

### Optimización de Recursos
- **Imágenes**: Usa siempre el componente `<Image />` de `next/image`. Define `width` y `height` o usa `fill` con un contenedor relativo.
- **Fuentes**: Implementa `next/font` (ej. `next/font/google`) para optimizar la carga de fuentes y evitar Layout Shifts (CLS).
- **Scripts**: Usa el componente `<Script />` con estrategias de carga apropiadas (`beforeInteractive`, `afterInteractive`, `lazyOnload`).

### Streaming y Suspense
- Utiliza `<Suspense />` para envolver componentes que tardan en cargar datos.
- Crea archivos `loading.tsx` para mostrar estados de carga instantáneos en la navegación.

## 3. Seguridad

### Validación de Datos
- **Zod**: Implementa esquemas de Zod para validar TODAS las entradas de usuario, parámetros de URL y cuerpos de peticiones.
- **Server Actions**: Valida los datos dentro de tus Server Actions antes de procesarlos.

### Manejo de Secretos
- **Variables de Entorno**: Usa `.env.local` para secretos.
- **Prefijo NEXT_PUBLIC_**: Solo añade este prefijo a variables que sean seguras de exponer en el navegador.
- **server-only**: Instala el paquete `server-only` e impórtalo en módulos de utilidades de servidor para prevenir importaciones accidentales en el cliente.

### Autenticación y Autorización
- Protege tus rutas usando Middleware (`middleware.ts`).
- Verifica la sesión y permisos del usuario en cada Server Action y Route Handler.

## 4. Gestión de Estado y Datos

### Server Actions vs API Routes
- **Server Actions**: Preferibles para mutaciones (POST, PUT, DELETE) invocadas desde la UI.
- **Route Handlers**: Úsalos para webhooks, endpoints públicos para terceros, o cuando necesites control total sobre la respuesta HTTP.

### Revalidación
- Usa `revalidatePath` o `revalidateTag` para actualizar la caché de datos bajo demanda después de una mutación.

## 5. Calidad de Código y DX

### TypeScript
- Tipa estrictamente tus props, estados y respuestas de API.
- Evita `any` a toda costa.

### Linting
- Configura ESLint y Prettier para mantener un estilo de código consistente.
- Usa reglas de accesibilidad (`jsx-a11y`).
