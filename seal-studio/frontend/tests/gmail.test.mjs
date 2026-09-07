import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, stat, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { gmailConfig, GMAIL_SCOPE, seal, unseal, beginConnection, finishConnection, connection, inbox, disconnect, verifyState, withGmailLock, cleanupPending } from '../src/lib/gmail-server.ts';
import { OAuth2Client } from 'google-auth-library';
const env = { GOOGLE_CLIENT_ID:'fixture-client', GOOGLE_CLIENT_SECRET:'fixture-secret', STUDIO_PUBLIC_URL:'https://studio.example.test', STUDIO_INTEGRATION_KEY:'ab'.repeat(32), STUDIO_INTEGRATION_DIR:'/tmp/fixture-gmail' };
const config = gmailConfig(env);
test('configuration fails closed without HTTPS, a 32-byte key, or a fixed origin',()=>{
  assert.equal(gmailConfig({}),null);
  assert.equal(gmailConfig({...env,STUDIO_PUBLIC_URL:'http://100.75.201.110:3001'}),null);
  assert.equal(gmailConfig({...env,STUDIO_PUBLIC_URL:'https://user:password@example.test'}),null);
  assert.equal(gmailConfig({...env,STUDIO_PUBLIC_URL:'https://example.test/path'}),null);
  assert.equal(gmailConfig({...env,STUDIO_INTEGRATION_KEY:'short'}),null);
  assert.ok(config);
});
test('AES-GCM concrete layout: 12-byte IV, 16-byte tag, ciphertext; tampering and cross-owner fail',()=>{
  const data = {uid:99901,credentials:{refresh_token:'SYNTHETIC-NOT-A-REAL-TOKEN'}};
  const encoded = seal(data,config,'gmail-99901'), bytes = Buffer.from(encoded,'base64url');
  assert.equal(bytes.subarray(0,12).length,12); assert.equal(bytes.subarray(12,28).length,16);
  assert.equal(bytes.subarray(28).length,Buffer.byteLength(JSON.stringify(data)));
  assert.deepEqual(unseal(encoded,config,'gmail-99901'),data);
  assert.equal(bytes.includes(Buffer.from('SYNTHETIC-NOT-A-REAL-TOKEN')),false);
  const corrupt = Buffer.from(bytes); corrupt[28] ^= 1;
  assert.throws(()=>unseal(corrupt.toString('base64url'),config,'gmail-99901'));
  assert.throws(()=>unseal(encoded,config,'gmail-99902'));
});
test('OAuth state requires session owner, browser cookie, exact state and unexpired record',()=>{
  const pending = {uid:99901,state:'test-state',verifier:'fixture',expires:Date.now()+10000};
  assert.doesNotThrow(()=>verifyState(pending,99901,'test-state','test-state'));
  assert.throws(()=>verifyState(pending,99902,'test-state','test-state'));
  assert.throws(()=>verifyState(pending,99901,'test-state','other'));
  assert.throws(()=>verifyState({...pending,expires:0},99901,'test-state','test-state'));
  assert.throws(()=>verifyState(null,99901,'test-state','test-state'));
});
test('consent uses Google, PKCE, only metadata scope; pending record encrypted mode600',async()=>{
  const directory = await mkdtemp(path.join(os.tmpdir(),'seal-gmail-test-'));
  const current = {...config,directory};
  const flow = await beginConnection(current,99901), url = new URL(flow.url);
  assert.equal(url.origin,'https://accounts.google.com');
  assert.equal(url.searchParams.get('scope'),GMAIL_SCOPE);
  assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  assert.equal(url.searchParams.get('redirect_uri'),env.STUDIO_PUBLIC_URL+'/api/integrations/gmail/callback');
  assert.equal(url.searchParams.get('state'),flow.state);
  const filename = path.join(directory,'pending-99901.enc'), raw = await readFile(filename,'utf8');
  assert.equal(raw.includes(flow.state),false); assert.equal((await stat(filename)).mode & 0o777,0o600);
  assert.deepEqual(await readdir(directory),['pending-99901.enc']);
  const pending = unseal(raw,current,'pending-99901');
  assert.equal(pending.uid,99901); assert.equal(pending.state,flow.state);
});
test('same-user read-refresh and disconnect are serialized',async()=>{
  const events=[];
  await Promise.all([withGmailLock(99901,async()=>{events.push('read'); await new Promise(r=>setTimeout(r,5));events.push('persist')}),withGmailLock(99901,async()=>{events.push('disconnect')})]);
  assert.deepEqual(events,['read','persist','disconnect']);
});
test('mocked Google lifecycle: consent, ten-header inbox, single-use callback, revocation',async t=>{
  const directory=await mkdtemp(path.join(os.tmpdir(),'seal-gmail-lifecycle-')), current={...config,directory};
  let exchanges=0, revocations=0;
  t.mock.method(OAuth2Client.prototype,'getToken',async()=>{exchanges++;return {tokens:{refresh_token:'SYNTHETIC',access_token:'FAKE',scope:GMAIL_SCOPE}}});
  t.mock.method(OAuth2Client.prototype,'revokeToken',async token=>{assert.equal(token,'SYNTHETIC');revocations++;return {}});
  t.mock.method(OAuth2Client.prototype,'request',async options=>{
    const url=new URL(options.url);
    assert.equal(url.origin,'https://gmail.googleapis.com');
    if(url.pathname.endsWith('/profile'))return {data:{emailAddress:'fixture@example.test'}};
    if(url.pathname.endsWith('/messages')){assert.equal(options.params.maxResults,10);return {data:{messages:[{id:'fixture-message'}]}}}
    assert.equal(options.params.format,'metadata');
    assert.deepEqual(options.params.metadataHeaders,['From','Subject','Date']);
    return {data:{id:'fixture-message',payload:{headers:[{name:'From',value:'sender@example.test'},{name:'Subject',value:'Synthetic test'}]}}};
  });
  const flow=await beginConnection(current,99901);
  await assert.rejects(()=>finishConnection(current,99902,'fake-code',flow.state,flow.state));
  assert.equal(exchanges,0);
  await finishConnection(current,99901,'fake-code',flow.state,flow.state);
  assert.deepEqual(await connection(current,99901),{connected:true,email:'fixture@example.test'});
  assert.equal((await connection(current,99902)).connected,false);
  await assert.rejects(()=>finishConnection(current,99901,'fake-code',flow.state,flow.state));
  assert.equal(exchanges,1);
  const result=await inbox(current,99901); assert.equal(result.messages[0].subject,'Synthetic test');
  assert.equal('body' in result.messages[0],false);
  assert.equal((await readFile(path.join(directory,'gmail-99901.enc'),'utf8')).includes('SYNTHETIC'),false);
  await disconnect(current,99901); assert.equal(revocations,1); assert.equal((await connection(current,99901)).connected,false);
  const next=await beginConnection(current,99901); await finishConnection(current,99901,'fake-code',next.state,next.state);
  t.mock.method(OAuth2Client.prototype,'revokeToken',async()=>{throw new Error('synthetic network outage')});
  assert.deepEqual(await disconnect(current,99901),{localDisconnected:true,remoteRevocationPending:true});
  assert.equal((await connection(current,99901)).connected,false,'Google failure cannot keep local tokens');
});
test('pending cleanup failure never replaces a successful authorization result',async t=>{
  const warnings=[]; t.mock.method(console,'warn',(...args)=>warnings.push(args));
  await cleanupPending('/synthetic-path-not-logged',async()=>{throw Object.assign(new Error('secret-not-logged'),{code:'EACCES'})});
  assert.deepEqual(warnings,[['Gmail pending cleanup failed','EACCES']]);
});
