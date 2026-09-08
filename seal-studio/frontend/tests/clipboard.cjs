// Isolated synthetic users/channels: never reads or writes a real DM.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.STUDIO_TEST_URL || 'http://127.0.0.1:3002';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=', 'base64');
const agents = ['ADA', 'JARVIS', 'ALICE', 'NEXUS', 'FABLE', 'DUM'];
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/snap/chromium/current/usr/lib/chromium-browser/chrome', headless:true, args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}, permissions:['clipboard-read','clipboard-write']});
    const page = await context.newPage(), errors = [], uploads = [], messages = new Map();
    let failUpload = false, textPosts = 0;
    page.on('pageerror', e => errors.push(e.message));
    await context.addInitScript(() => {
      window.previewURLs = new Set();
      const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
      URL.createObjectURL = blob => { const url = create(blob); window.previewURLs.add(url); return url; };
      URL.revokeObjectURL = url => { window.previewURLs.delete(url); revoke(url); };
    });
    async function fixture(route) {
      const req = route.request(), url = new URL(req.url());
      let status = 200, body = {ok:true};
      if (url.pathname.startsWith('/bridge/uploads/')) return route.fulfill({status:200,contentType:'image/png',body:png});
      if (url.pathname.endsWith('/auth/me')) body.user = {id:99901,username:'fixture',display_name:'Cuenta de prueba',role:'admin'};
      else if (url.pathname.endsWith('/user/agents')) body.agents = agents;
      else if (url.pathname.endsWith('/team/status')) body.agents = Object.fromEntries(agents.map(a => [a,{alive:true}]));
      else if (url.pathname.endsWith('/chat/messages')) body.messages = (messages.get(url.searchParams.get('channel')) || []).filter(m => m.id > Number(url.searchParams.get('after') || 0));
      else if (url.pathname.endsWith('/api/upload')) {
        const form = await new Request('http://fixture/upload',{method:'POST',headers:{'content-type':req.headers()['content-type']},body:req.postDataBuffer()}).formData();
        const file = form.get('file');
        const upload = {channel:form.get('channel'),caption:form.get('caption'),bytes:Buffer.from(await file.arrayBuffer()),name:file.name};
        uploads.push(upload);
        if (failUpload) { status = 503; body = {ok:false,error:'synthetic upload failure'}; }
        else {
          const id = uploads.length, rows = messages.get(upload.channel) || [];
          rows.push({id,sender_name:'fixture',content:upload.caption,type:'conversation',file_url:`/uploads/fixture-${id}.png`,filename:upload.name});
          messages.set(upload.channel,rows); body.id = id; body.file_url = `/uploads/fixture-${id}.png`;
        }
      } else if (url.pathname.endsWith('/chat/send')) textPosts++;
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    }
    await context.route('**/bridge/**',fixture);
    await context.route('**/studio/**',fixture);
    await context.route('**/api/integrations/**',fixture);
    await page.goto(base+'/v2');
    await page.getByText('Bienvenido a casa, Cuenta de prueba.').waitFor();
    const editor = page.getByRole('textbox',{name:'Mensaje',exact:true});
    const remove = page.getByRole('button',{name:'Quitar adjunto'});
    async function channel(name) {
      await page.locator('.sidebar').getByRole('button',{name: name === 'General' ? name : new RegExp(name+'$'),exact:true}).click();
      await editor.waitFor();
    }
    async function paste({count=1,size=0,text=''}={}) {
      return editor.evaluate((el,{bytes,count,size,text}) => {
        const data = new DataTransfer();
        if (text) data.setData('text/plain',text);
        for (let i=0;i<count;i++) data.items.add(new File([size ? new Uint8Array(size) : new Uint8Array(bytes)],`paste-${i}.png`,{type:'image/png'}));
        const event = new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});
        el.dispatchEvent(event); return event.defaultPrevented;
      },{bytes:[...png],count,size,text});
    }
    async function preview() {
      await page.waitForFunction(() => document.querySelector('.selected-image-preview')?.naturalWidth > 0);
    }
    for (const name of ['General',...agents]) {
      await channel(name);
      const expected = name === 'General' ? 'web_chat' : 'dm:'+['fixture',name.toLowerCase()].sort().join(':');
      const before = uploads.length;
      await editor.fill(`Foto ${name}`);
      assert.equal(await paste({text:'clipboard alternate text'}),true);
      await preview();
      assert.equal(await editor.inputValue(),`Foto ${name}`,'image paste preserves caption');
      assert.equal(uploads.length,before,'paste never auto-sends');
      await page.getByRole('button',{name:'Enviar mensaje'}).click();
      await page.waitForFunction(() => document.querySelector('textarea[aria-label=Mensaje]').value === '');
      assert.equal(uploads.length,before+1,'exactly one upload');
      assert.equal(uploads.at(-1).channel,expected);
      assert.equal(uploads.at(-1).caption,`Foto ${name}`);
      assert.deepEqual(uploads.at(-1).bytes,png,'original image bytes survive multipart');
      await page.locator('.attachment-preview').last().waitFor();
      await page.waitForFunction(() => window.previewURLs.size===0);
      console.log(`PASS paste → preview → upload → image: ${expected}`);
    }
    assert.equal(textPosts,0,'uploads do not duplicate a text message');
    await channel('General');
    // Seed the OS clipboard on loopback when the target is HTTP/Tailscale.
    // The target still receives a real Ctrl+V without Clipboard API permissions.
    let clipboardPage = page;
    if (!await page.evaluate(() => isSecureContext)) {
      clipboardPage = await context.newPage();
      const loopback = new URL(base); loopback.hostname = '127.0.0.1';
      await clipboardPage.goto(loopback.origin+'/v2');
    }
    await clipboardPage.bringToFront();
    await clipboardPage.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
      canvas.getContext('2d').fillRect(0,0,1,1);
      const blob = await new Promise(resolve => canvas.toBlob(resolve,'image/png'));
      await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
    });
    await page.bringToFront(); await editor.focus(); await page.keyboard.press('Control+v'); await preview();
    await remove.click();
    await clipboardPage.bringToFront();
    await clipboardPage.evaluate(() => navigator.clipboard.writeText('Texto pegado intacto'));
    await page.bringToFront(); await editor.focus(); await page.keyboard.press('Control+v');
    assert.equal(await editor.inputValue(),'Texto pegado intacto');
    if (clipboardPage !== page) await clipboardPage.close();
    console.log('PASS native Chromium Ctrl+V: image and ordinary text');
    await paste(); await preview();
    await paste(); await page.getByRole('alert').filter({hasText:'Quitá el adjunto actual'}).waitFor();
    await page.waitForTimeout(3200);
    assert.match(await page.locator('.inline-error').innerText(),/Quitá el adjunto actual/,'polling cannot erase validation error');
    await remove.click();
    await paste({count:2}); await page.getByRole('alert').filter({hasText:'una imagen por mensaje'}).waitFor();
    assert.equal(await page.locator('.selected-file').count(),0);
    await paste({size:25*1024*1024+1}); await page.getByRole('alert').filter({hasText:'hasta 25 MB'}).waitFor();
    assert.equal(await page.locator('.selected-file').count(),0);
    assert.equal(await paste({count:0,text:'normal'}),false,'text paste default not prevented');
    await paste(); await preview(); failUpload = true;
    await page.getByRole('button',{name:'Enviar mensaje'}).click();
    await page.getByRole('alert').filter({hasText:'synthetic upload failure'}).waitFor();
    assert.equal(await editor.inputValue(),'Texto pegado intacto'); await preview();
    failUpload = false;
    await page.getByRole('button',{name:'Enviar mensaje'}).click();
    await page.waitForFunction(() => !document.querySelector('.selected-file'));
    console.log('PASS existing attachment, multi-image, size validation, polling, failed upload and retry');
    await paste(); await preview(); await editor.fill('No debe cruzar al DM');
    await channel('ADA');
    assert.equal(await editor.inputValue(),'');
    assert.equal(await page.locator('.selected-file').count(),0);
    await page.waitForFunction(() => window.previewURLs.size===0);
    await paste(); await preview();
    for (const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true,`preview fits ${width}px`);
    }
    await remove.click(); await page.waitForFunction(() => window.previewURLs.size===0);
    assert.deepEqual(errors,[]);
    console.log('PASS channel isolation, preview URL cleanup, responsive layout; no browser errors');
    console.log(`PASS clipboard suite: 7 channels, ${uploads.length} upload attempts, ${textPosts} duplicate text posts`);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
