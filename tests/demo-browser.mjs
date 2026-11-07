import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';

const root = path.join(process.cwd(), 'dist/demo');
const prefix = '/glance-proxmox-usb-backup/';
const files = { 'index.html': 'text/html; charset=utf-8', 'panel.js': 'text/javascript',
  'panel.css': 'text/css', 'demo.js': 'text/javascript', 'demo.css': 'text/css' };
const server = createServer(async (req, res) => {
  if (!req.url.startsWith(prefix)) { res.writeHead(404); res.end(); return; }
  const name = req.url.slice(prefix.length) || 'index.html';
  if (!files[name]) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', files[name]);
  res.end(await readFile(path.join(root, name)));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ ...(process.env.BROWSER_EXECUTABLE_PATH ? { executablePath: process.env.BROWSER_EXECUTABLE_PATH } : {}), headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const requests = [];
const errors = [];
page.on('request', request => requests.push(request.url()));
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(`http://127.0.0.1:${server.address().port}${prefix}`);
  await page.locator('.machine').first().waitFor();
  assert.equal(await page.locator('#login').isVisible(), false);
  assert.equal(await page.locator('.machine').count(), 6);
  assert.equal(await page.locator('.archive-item').count(), 3);
  await page.locator('.machine input[value="200"]').check();
  await page.locator('.machine input[value="230"]').check();
  await page.locator('#notes-200').fill('Demo note for smart home');
  await page.locator('#notes-230').fill('Independent demo note for budget');
  await page.locator('#start').click();
  await page.getByRole('heading', { name: 'Trwa backup', exact: true }).waitFor();
  assert.equal(await page.locator('#eject').isDisabled(), true);
  await page.getByText('USB odmontowane po backupie. Można bezpiecznie wyjąć pendrive.', { exact: true }).waitFor({ timeout: 10000 });
  assert.equal(await page.locator('.archive-item').count(), 5);
  assert.match(await page.locator('#archive-status').textContent(), /lista z pamięci/);
  assert.deepEqual(await page.locator('.job-guest-note').allTextContents(), ['Demo note for smart home', 'Independent demo note for budget']);
  assert.ok((await page.locator('.archive-note').allTextContents()).includes('smart-home - Demo note for smart home'));
  assert.ok((await page.locator('.archive-note').allTextContents()).includes('budget-sandbox - Independent demo note for budget'));
  await page.locator('#mount').click();
  await page.getByText('Ostatni backup zakończony. Pendrive jest ponownie zamontowany.', { exact: true }).waitFor();
  assert.equal(await page.locator('#eject').isEnabled(), true);
  await page.locator('#eject').click();
  await page.getByText('Można bezpiecznie wyjąć pendrive.', { exact: true }).waitFor();
  await page.locator('#logout').click();
  await page.waitForFunction(() => document.querySelectorAll('.archive-item').length === 3 && document.querySelector('#job').hidden);
  assert.equal(await page.locator('.machine input:checked').count(), 0);
  assert.equal(await page.locator('#login').isVisible(), false);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.reload();
  await page.locator('.machine').first().waitFor();
  assert.equal(await page.locator('.archive-item').count(), 3);
  assert.deepEqual(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })), { local: 0, session: 0 });
  assert.ok(requests.every(url => url.startsWith(`http://127.0.0.1:${server.address().port}${prefix}`)), requests.join('\n'));
  assert.ok(!requests.some(url => url.includes('/api/')), requests.join('\n'));
  assert.deepEqual(errors, []);
  await mkdir('test-results', { recursive: true });
  await page.screenshot({ path: 'test-results/demo-mobile.png', fullPage: true });
  console.log('Demo OK: repository subpath, fictional inventory, per-guest notes, simulated queue/eject/mount, reset, mobile, no API calls or persisted credentials.');
} finally {
  await browser.close();
  server.close();
}
