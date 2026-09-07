// Browser contracts use synthetic accounts/messages; no real DM or email is read.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.STUDIO_TEST_URL || 'http://127.0.0.1:3002';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/snap/chromium/current/usr/lib/chromium-browser/chrome', headless: true, args: ['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
  const page = await context.newPage(); const errors = []; const writes = [];
  page.on('pageerror', e => errors.push(e.message));
  let user = 'fixture', sendFails = false, logoutFails = false;
  const fixture = id => ({ id, sender_name: 'ADA', content: `Mensaje de prueba ${id}`, type: id % 10 === 0 ? 'heartbeat' : 'conversation', created_at: '2026-09-07T12:00:00Z' });
  async function routeAPI(route) {
    const req = route.request(), url = new URL(req.url()); let status = 200, body = { ok: true };
    if (req.method() === 'POST') writes.push({ path: url.pathname, body: req.postData() });
    if (url.pathname.endsWith('/auth/me')) body.user = { id: user === 'fixture' ? 99901 : 99902, username: user, role: 'admin', display_name: 'Cuenta de prueba' };
    else if (url.pathname.endsWith('/auth/logout') && logoutFails) { status = 503; body = { ok: false, error: 'synthetic outage' }; }
    else if (url.pathname.endsWith('/user/agents')) body.agents = ['ADA', 'JARVIS', 'ALICE', 'NEXUS', 'FABLE', 'DUM'];
    else if (url.pathname.endsWith('/team/status')) body.agents = Object.fromEntries(['ADA','JARVIS','ALICE','NEXUS','FABLE','DUM'].map(a => [a,{alive:true}]));
    else if (url.pathname.endsWith('/soul/pulse')) body.pulse = { mem_total: 123456, mem_today: 84, thoughts_1h: 37, nerves_fires_1h: 12 };
    else if (url.pathname.endsWith('/system/health')) body.services = { chat: { status: 'up', port: 8765 } };
    else if (url.pathname.endsWith('/chat/messages')) {
      const channel = url.searchParams.get('channel');
      if (channel === 'web_chat') {
        if (url.searchParams.has('after')) body.messages = [];
        else if (url.searchParams.has('before')) body.messages = Array.from({length:100},(_,i)=>fixture(i+1));
        else body.messages = Array.from({length:100},(_,i)=>fixture(i+101));
      } else body.messages = [{id:500,sender_name:'ADA',content:`Privado sintético ${channel}`,type:'conversation'}];
    } else if (url.pathname.endsWith('/chat/send') && sendFails) { status = 503; body = { ok:false, error:'synthetic send failure' }; }
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  }
  await context.route('**/bridge/**', routeAPI); await context.route('**/studio/**', routeAPI);
  await context.route('**/api/integrations/gmail/status', r => r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:true,configured:false,connected:false})}));
  await page.goto(base + '/v2');
  await page.getByText('Bienvenido a casa, Cuenta de prueba.').waitFor();
  await page.screenshot({path:'/home/dadito/IA/proyecto-seal/var/rescate/seal-casa-desktop.png'});
  for (const width of [1440, 1024, 768, 390, 320]) {
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), true, `no horizontal overflow at ${width}`);
  }
  await page.screenshot({path:'/home/dadito/IA/proyecto-seal/var/rescate/seal-casa-mobile.png'});
  await page.getByRole('button',{name:'Abrir canales y mensajes directos'}).click();
  await page.locator('.sidebar').getByRole('button',{name:'General',exact:true}).click();
  await page.getByText('90 mensajes cargados').waitFor({state:'attached'});
  assert.ok(await page.locator('.message').count() < 90,'virtualized list limits rendered nodes');
  await page.getByRole('button',{name:'Cargar mensajes anteriores'}).click();
  await page.getByText('180 mensajes cargados').waitFor({state:'attached'});
  const search = page.getByRole('textbox',{name:'Buscar en los mensajes cargados'});
  await search.fill('prueba 199'); await page.getByText('Mensaje de prueba 199',{exact:true}).waitFor();
  await search.fill(''); await page.getByText('180 mensajes cargados').waitFor({state:'attached'});
  await page.getByRole('textbox',{name:'Mensaje',exact:true}).fill('Borrador que debe conservarse'); sendFails = true;
  await page.getByRole('button',{name:'Enviar mensaje'}).click(); await page.getByRole('alert').waitFor();
  assert.equal(await page.getByRole('textbox',{name:'Mensaje',exact:true}).inputValue(),'Borrador que debe conservarse');
  sendFails = false;
  await page.locator('.composer input[type=file]').setInputFiles({name:'prueba.txt',mimeType:'text/plain',buffer:Buffer.from('fixture only')});
  await page.getByRole('button',{name:'Enviar mensaje'}).click();
  await page.waitForFunction(()=>document.querySelector('textarea[aria-label=Mensaje]').value==='');
  assert.equal(writes.filter(w=>w.path.endsWith('/api/upload')).length,1,'exactly one upload POST');
  // Real browser MediaRecorder with synthetic device, never a human microphone.
  const postsBeforeCapture = writes.length;
  await page.getByRole('button',{name:'Grabar audio',exact:true}).click();
  await page.getByRole('button',{name:'Terminar grabación'}).waitFor();
  await page.getByRole('textbox',{name:'Mensaje',exact:true}).fill('Texto durante grabación');
  assert.equal(await page.getByRole('button',{name:'Enviar mensaje'}).isDisabled(),true,'cannot send while recording');
  await page.waitForTimeout(1200);
  await page.getByRole('button',{name:'Terminar grabación'}).click();
  await page.locator('.selected-file').waitFor();
  assert.equal(writes.length,postsBeforeCapture,'capture never auto-sends');
  await page.getByRole('button',{name:'Quitar adjunto'}).click();
  await page.getByRole('button',{name:'Grabar video',exact:true}).click();
  await page.locator('.capture-preview').waitFor();
  await page.evaluate(()=>{window.__testTracks=document.querySelector('.capture-preview').srcObject.getTracks()});
  await page.getByRole('button',{name:'Abrir canales y mensajes directos'}).click();
  await page.locator('.sidebar').getByRole('button',{name:/ADA/}).click();
  await page.getByText('Privado sintético dm:ada:fixture',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.__testTracks.every(t=>t.readyState==='ended')),true,'switching channels releases mic/camera');
  assert.equal(await page.getByText('Mensaje de prueba 199',{exact:true}).count(),0,'public rows never survive DM switch');
  await page.getByRole('button',{name:'Abrir canales y mensajes directos'}).click();
  await page.locator('.sidebar').getByRole('button',{name:'Latidos',exact:true}).click();
  await page.getByText('10 mensajes cargados').waitFor({state:'attached'});
  assert.equal(await page.getByRole('textbox',{name:'Mensaje',exact:true}).count(),0,'heartbeat view is read-only');
  await page.getByRole('button',{name:'Productividad',exact:true}).first().click();
  await page.getByRole('textbox',{name:'Nueva tarea'}).fill('Recuperar con respaldo');
  await page.getByRole('button',{name:'Agregar tarea'}).click();
  await page.getByRole('checkbox',{name:'Completar Recuperar con respaldo'}).check();
  await page.getByRole('button',{name:'Eliminar Recuperar con respaldo'}).click();
  await page.getByRole('button',{name:'Deshacer última eliminación'}).click();
  assert.equal(await page.getByRole('checkbox',{name:'Completar Recuperar con respaldo'}).isChecked(),true,'undo restores task and completed state');
  await page.getByRole('textbox',{name:'Notas personales'}).fill('Datos sintéticos, ninguna memoria real');
  await page.getByRole('button',{name:'Comenzar',exact:true}).click(); await page.getByRole('button',{name:'Pausar',exact:true}).waitFor();
  await page.reload(); await page.getByRole('button',{name:'Productividad',exact:true}).first().click();
  assert.equal(await page.getByRole('checkbox',{name:'Completar Recuperar con respaldo'}).isChecked(),true);
  assert.equal(await page.getByRole('textbox',{name:'Notas personales'}).inputValue(),'Datos sintéticos, ninguna memoria real');
  await page.getByRole('button',{name:'Cambiar tema'}).click(); assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  await page.keyboard.press('Control+k'); await page.getByRole('dialog',{name:'Navegación rápida'}).waitFor(); await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(),0);
  user = 'second-fixture'; await page.reload(); await page.getByRole('button',{name:'Productividad',exact:true}).first().click();
  assert.equal(await page.getByRole('textbox',{name:'Notas personales'}).inputValue(),'','local notes are account-scoped');
  // Corrupt the fixture only after leaving the mounted writer. Injecting during
  // its initial save effect races the test harness against valid app state.
  await page.getByRole('button',{name:'Inicio',exact:true}).first().click();
  await page.getByText('Bienvenido a casa, Cuenta de prueba.').waitFor();
  const damaged = '{damaged-but-preserved';
  await page.evaluate(raw=>localStorage.setItem('seal-workspace-v1:99902',raw),damaged);
  await page.reload(); await page.getByRole('button',{name:'Productividad',exact:true}).first().click();
  await page.getByText('No se pudo leer el espacio local.',{exact:false}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Exportar respaldo'}).isEnabled(),true,'corrupt data remains exportable');
  assert.equal(await page.evaluate(()=>localStorage.getItem('seal-workspace-v1:99902')),damaged,'corrupt data never overwritten');
  logoutFails = true; await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click();
  await page.getByRole('button',{name:'Entrar a mi espacio'}).waitFor();
  await page.reload(); await page.getByRole('button',{name:'Entrar a mi espacio'}).waitFor();
  assert.deepEqual(errors,[],'no uncaught browser errors');
  console.log('PASS: 5 viewport widths; virtualized history/search; DM isolation; send failure; single upload; audio/video capture without auto-send; camera/mic cleanup; read-only heartbeats; account-scoped tasks/notes; timer; themes; palette; failed-logout local lock; zero browser errors.');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
