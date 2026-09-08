import test from 'node:test';
import assert from 'node:assert/strict';
import { canOpenPanelSoul, PANEL_SOUL_PATH } from '../src/lib/panelSoul.ts';
import config from '../next.config.ts';

test('Panel SOUL navigation is limited to existing administrative roles', () => {
  for (const role of ['admin', 'superuser', 'ADMIN']) assert.equal(canOpenPanelSoul(role), true);
  for (const role of ['user', 'viewer', '', 'admin-other']) assert.equal(canOpenPanelSoul(role), false);
  assert.equal(PANEL_SOUL_PATH, '/panel-soul');
});

test('Panel SOUL proxies only to its fixed loopback service; existing routes remain', async () => {
  const routes = await config.rewrites();
  assert.deepEqual(routes, [
    { source: '/bridge/:path*', destination: 'http://127.0.0.1:8765/:path*' },
    { source: '/studio/:path*', destination: 'http://127.0.0.1:8800/:path*' },
    { source: '/panel-soul/:path*', destination: 'http://127.0.0.1:8093/:path*' },
  ]);
});

// El componente, no sólo el helper. Hueco hallado mutando el 8-sep-2026: borrar
// `if (!canOpenPanelSoul(role)) return null;` de PanelSoulLink.tsx dejaba los
// cuatro brazos VERDES. Los tests probaban la función y nadie probaba a quien la
// usa, así que el filtro visual se podía quitar entero sin que nada lo notara.
//
// CÓMO SE RENDERIZA SIN DEPENDENCIA NUEVA (idea de ADA, 8-sep): `node` hace type
// stripping de .ts pero NO transforma JSX y falla con ERR_UNKNOWN_FILE_EXTENSION
// en .tsx. Yo di el brazo por imposible sin sumar vitest; ADA midió que el stack
// YA instalado alcanza — `typescript` transpila el TSX y `react-dom/server` lo
// renderiza con su helper real. Se escribe a un archivo temporal DENTRO de
// frontend/ y no a un `data:` URL, porque desde un data: URL node no resuelve
// especificadores como `react/jsx-runtime`.
//
// ALCANCE: observa la COBERTURA del filtro visual. NO acredita la autorización
// del backend —el servidor en :8093 sigue siendo la autoridad y eso queda fuera
// de lo verificado aquí—. Lo que impide es que la defensa en profundidad se
// erosione en silencio.
test('PanelSoulLink itself withholds the link from non-administrative roles', async () => {
  const ts = (await import('typescript')).default;
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { readFileSync, writeFileSync, rmSync } = await import('node:fs');
  const { fileURLToPath, pathToFileURL } = await import('node:url');
  const path = await import('node:path');

  const aqui = path.dirname(fileURLToPath(import.meta.url));
  const fuente = readFileSync(path.join(aqui, '../src/app/v2/PanelSoulLink.tsx'), 'utf8');
  const js = ts.transpileModule(fuente, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText
    .replace('"@/lib/panelSoul"', '"../src/lib/panelSoul.ts"')
    .replace(/import \{[^}]*\} from "lucide-react";/,
             'const ArrowUpRight = () => null, SlidersHorizontal = () => null;');

  const tmp = path.join(aqui, `.panel-soul-render-${process.pid}.mjs`);
  let PanelSoulLink;
  try {
    writeFileSync(tmp, js);
    PanelSoulLink = (await import(pathToFileURL(tmp).href)).default;
  } finally {
    rmSync(tmp, { force: true });
  }

  for (const role of ['user', 'viewer', '', 'admin-other']) {
    assert.equal(renderToStaticMarkup(PanelSoulLink({ role })), '',
      `el componente rendirizo algo para el rol ${JSON.stringify(role)}`);
    assert.equal(renderToStaticMarkup(PanelSoulLink({ role, compact: true })), '',
      `la variante compacta rendirizo algo para ${JSON.stringify(role)}`);
  }

  // Control positivo: sin esto, un componente que devolviera null SIEMPRE pasaria
  // los brazos de arriba y este test no probaria nada.
  for (const role of ['admin', 'superuser', 'ADMIN']) {
    const html = renderToStaticMarkup(PanelSoulLink({ role }));
    assert.ok(html.includes(PANEL_SOUL_PATH), `no rendirizo el link para ${role}: ${html}`);
  }
});
