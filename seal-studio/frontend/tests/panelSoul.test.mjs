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
