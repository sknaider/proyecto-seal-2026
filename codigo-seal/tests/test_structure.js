#!/usr/bin/env node
/**
 * Código SEAL IDE — Structure Tests
 * Tests that validate the IDE structure is correct before and after modularization.
 * Run: node tests/test_structure.js
 */

const fs = require('fs');
const path = require('path');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (e) {
    console.log(`  ❌ ${name}: ${e.message}`);
    failed++;
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg || 'Assertion failed');
}

const SRC = path.join(__dirname, '..', 'src');

console.log('\n═══════════════════════════════════════');
console.log('  Código SEAL — Structure Tests');
console.log('═══════════════════════════════════════\n');

// ── File existence ──
console.log('--- Files ---');

test('main.js exists', () => {
  assert(fs.existsSync(path.join(SRC, 'main', 'main.js')));
});

test('preload.js exists', () => {
  assert(fs.existsSync(path.join(SRC, 'preload', 'preload.js')));
});

test('index.html exists', () => {
  assert(fs.existsSync(path.join(SRC, 'renderer', 'index.html')));
});

test('app.js exists', () => {
  assert(fs.existsSync(path.join(SRC, 'renderer', 'app.js')));
});

test('seal-theme.css exists', () => {
  assert(fs.existsSync(path.join(SRC, 'renderer', 'styles', 'seal-theme.css')));
});

test('package.json exists', () => {
  assert(fs.existsSync(path.join(__dirname, '..', 'package.json')));
});

// ── Syntax validation ──
console.log('\n--- Syntax ---');

test('main.js valid syntax', () => {
  const code = fs.readFileSync(path.join(SRC, 'main', 'main.js'), 'utf-8');
  new Function(code); // throws SyntaxError if invalid
});

test('preload.js valid syntax', () => {
  const code = fs.readFileSync(path.join(SRC, 'preload', 'preload.js'), 'utf-8');
  // preload uses require() which isn't available here, just check parse
  require('vm').createScript(code);
});

test('app.js valid syntax', () => {
  const code = fs.readFileSync(path.join(SRC, 'renderer', 'app.js'), 'utf-8');
  // app.js uses browser APIs, just validate parse
  require('vm').createScript(code);
});

// ── IPC channel matching ──
console.log('\n--- IPC Channels ---');

const mainCode = fs.readFileSync(path.join(SRC, 'main', 'main.js'), 'utf-8');
const preloadCode = fs.readFileSync(path.join(SRC, 'preload', 'preload.js'), 'utf-8');

const mainHandlers = [...mainCode.matchAll(/ipcMain\.handle\(['"]([^'"]+)['"]/g)].map(m => m[1]);
const mainOns = [...mainCode.matchAll(/ipcMain\.on\(['"]([^'"]+)['"]/g)].map(m => m[1]);
const preloadInvokes = [...preloadCode.matchAll(/ipcRenderer\.invoke\(['"]([^'"]+)['"]/g)].map(m => m[1]);
const preloadSends = [...preloadCode.matchAll(/ipcRenderer\.send\(['"]([^'"]+)['"]/g)].map(m => m[1]);

test('All preload invokes have main handlers', () => {
  const missing = preloadInvokes.filter(ch => !mainHandlers.includes(ch));
  assert(missing.length === 0, `Missing handlers: ${missing.join(', ')}`);
});

test('All preload sends have main listeners', () => {
  const missing = preloadSends.filter(ch => !mainOns.includes(ch));
  assert(missing.length === 0, `Missing listeners: ${missing.join(', ')}`);
});

// ── HTML element IDs referenced in JS ──
console.log('\n--- HTML/JS ID matching ---');

const appCode = fs.readFileSync(path.join(SRC, 'renderer', 'app.js'), 'utf-8');
const htmlCode = fs.readFileSync(path.join(SRC, 'renderer', 'index.html'), 'utf-8');

const jsIds = [...new Set([...appCode.matchAll(/getElementById\(['"]([^'"]+)['"]\)/g)].map(m => m[1]))];
const htmlIds = [...new Set([...htmlCode.matchAll(/id="([^"]+)"/g)].map(m => m[1]))];

test('All JS getElementById refs exist in HTML', () => {
  const missing = jsIds.filter(id => !htmlIds.includes(id));
  assert(missing.length === 0, `Missing IDs in HTML: ${missing.join(', ')}`);
});

// ── CSS classes used in JS exist in CSS ──
console.log('\n--- CSS classes ---');

const cssCode = fs.readFileSync(path.join(SRC, 'renderer', 'styles', 'seal-theme.css'), 'utf-8');

test('.tab.modified defined in CSS', () => {
  assert(cssCode.includes('.tab.modified'), '.tab.modified missing');
});

test('.chat-msg-main defined in CSS', () => {
  assert(cssCode.includes('.chat-msg-main'), '.chat-msg-main missing');
});

test('.context-menu defined in CSS', () => {
  assert(cssCode.includes('.context-menu'), '.context-menu missing');
});

test('.toast defined in CSS', () => {
  assert(cssCode.includes('.toast'), '.toast missing');
});

test('.palette-box defined in CSS', () => {
  assert(cssCode.includes('.palette-box'), '.palette-box missing');
});

// ── Security checks ──
console.log('\n--- Security ---');

test('No execSync in git handlers (command injection)', () => {
  const gitSection = mainCode.slice(mainCode.indexOf("// Git integration"));
  assert(!gitSection.includes('execSync('), 'execSync found in git handlers — use execFileSync');
});

test('escHtml escapes quotes', () => {
  assert(appCode.includes('&quot;'), 'escHtml must escape double quotes');
  assert(appCode.includes('&#x27;'), 'escHtml must escape single quotes');
});

test('browser:open validates URL scheme', () => {
  assert(mainCode.includes("http://") && mainCode.includes("https://"), 'URL scheme validation missing');
});

// ── Feature checks ──
console.log('\n--- Features ---');

test('Command Palette exists', () => {
  assert(appCode.includes('initCommandPalette'), 'Command palette missing');
});

test('Multi-terminal support', () => {
  assert(appCode.includes('terminals') && appCode.includes('switchTerminal'), 'Multi-terminal missing');
});

test('Git panel exists', () => {
  assert(appCode.includes('initGit') && appCode.includes('fetchGitStatus'), 'Git panel missing');
});

test('Context menus exist', () => {
  assert(appCode.includes('initContextMenus') && appCode.includes('showContextMenu'), 'Context menus missing');
});

test('Toast notifications exist', () => {
  assert(appCode.includes('showToast'), 'Toast notifications missing');
});

test('Resize handles exist', () => {
  assert(appCode.includes('initResizeHandles'), 'Resize handles missing');
});

test('WebSocket reconnect has backoff', () => {
  assert(appCode.includes('chatReconnectDelay') && appCode.includes('Math.min'), 'WebSocket backoff missing');
});

test('findTabByPath used instead of querySelector injection', () => {
  // Should NOT have querySelector with template literal for tab paths
  const dangerousPattern = /querySelector\(`.tab\[data-path="\$\{/;
  assert(!dangerousPattern.test(appCode), 'Dangerous querySelector with path interpolation found');
});

// ── Summary ──
console.log(`\n═══════════════════════════════════════`);
console.log(`  ${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed === 0) {
  console.log(`  ✅ ALL TESTS PASSED`);
} else {
  console.log(`  ❌ ${failed} FAILURES`);
}
console.log(`═══════════════════════════════════════\n`);

process.exit(failed > 0 ? 1 : 0);
