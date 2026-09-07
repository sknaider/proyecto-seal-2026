import assert from 'node:assert/strict';
const origins = process.env.STUDIO_HEALTH_ORIGINS?.split(',') || ['http://127.0.0.1:3001','http://100.75.201.110:3001','http://127.0.0.1:9000'];
for (const origin of origins) {
  const started = performance.now(), response = await fetch(origin + '/v2', { signal: AbortSignal.timeout(10000) });
  assert.equal(response.status,200,origin+'/v2');
  const html = await response.text();
  const assets = [...new Set([...html.matchAll(/(?:src|href)="([^" ]+\.(?:js|css))"/g)].map(m=>m[1]).filter(p=>p.startsWith('/_next/')))];
  assert.ok(assets.length>0,'Next assets present');
  for (const asset of assets) assert.equal((await fetch(origin+asset,{signal:AbortSignal.timeout(10000)})).status,200,asset);
  console.log(JSON.stringify({origin,page:200,assets:assets.length,assetsOK:true,elapsedMs:Math.round(performance.now()-started)}));
}
for (const pathname of ['/bridge/api/auth/me','/studio/api/soul/pulse','/bridge/api/chat/messages?channel=dm%3Aada%3Awilliam','/api/integrations/gmail/status','/api/integrations/gmail/inbox']) {
  const response = await fetch(origins[0]+pathname,{signal:AbortSignal.timeout(10000)});
  assert.equal(response.status,401,pathname+' must reject anonymous');
  console.log(JSON.stringify({pathname,anonymous:401}));
}
console.log('PASS: pages/assets reachable; private endpoints reject anonymous. No authenticated conversation or email was read.');
