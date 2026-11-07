import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';

const root = path.join(process.cwd(), 'app/static');
const screenshotDir = process.env.SCREENSHOT_DIR || path.join(process.cwd(), 'test-results');
await mkdir(screenshotDir, { recursive: true });
const server = createServer(async (req, res) => {
  const names = { '/': ['index.html', 'text/html'], '/panel.js': ['panel.js', 'text/javascript'], '/panel.css': ['panel.css', 'text/css'] };
  const item = names[req.url];
  if (!item) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', item[1]);
  res.end(await readFile(path.join(root, item[0])));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ ...(process.env.BROWSER_EXECUTABLE_PATH ? { executablePath: process.env.BROWSER_EXECUTABLE_PATH } : {}), headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const names = ['home-assistant', 'dns', 'dashboard', 'password-manager', 'media-server', 'desktop', 'finance-demo', 'cloud-files'];
const state = { uuid: 'DEMO-0001', drive_name: 'USB backup', usb: { state: 'mounted', mounted: true, free: 72 * 1024 ** 3, total: 114.6 * 1024 ** 3 },
  machines: names.map((name, i) => ({ vmid: 100 + i, name, status: 'running', type: [2, 3, 6].includes(i) ? 'lxc' : 'qemu', disk: 20 * 1024 ** 3, template: false })), job: null,
  backups: {items:[{vmid:100,type:'qemu',filename:'vzdump-qemu-100-2026_11_07-10_00_00.vma.zst',created:1794042000,size:3*1024**3,notes:'home-assistant - przed aktualizacją'}],updated:1794042100,cached:false,error:null} };
let lastBackup;
let offline = false;
let mountRequests = 0;
await page.route('**/api/**', async route => {
  const endpoint = new URL(route.request().url()).pathname;
  if (endpoint === '/api/login') return route.fulfill({ json: { token: 'test-session' } });
  if (endpoint === '/api/status') return route.fulfill({ status: offline ? 503 : 200, json: offline ? { error: 'Test utraty połączenia' } : state });
  if (endpoint === '/api/backup') {
    lastBackup = route.request().postDataJSON();
    state.job = { phase: 'running', guests: state.machines.filter(vm => lastBackup.vmids.includes(vm.vmid)).map(vm => ({ ...vm, backup: 'running',notes:lastBackup.notes[String(vm.vmid)] })), logs: ['INFO: Starting Backup of VM 100 (qemu)'], error: null };
    return route.fulfill({ status: 202, json: { job: state.job } });
  }
  if (endpoint === '/api/eject') { state.usb = { state: 'unmounted', mounted: false }; return route.fulfill({ json: { message: 'OK' } }); }
  if (endpoint === '/api/mount') {
    mountRequests++;
    state.usb = { state: 'mounted', mounted: true, free: 70 * 1024 ** 3, total: 114.6 * 1024 ** 3 };
    state.backups.cached = false;
    return route.fulfill({ json: { message: 'OK' } });
  }
  return route.fulfill({ status: 404, json: {} });
});
try {
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.locator('#password').fill('test-password-long');
  await page.locator('#login button').click();
  await page.locator('.machine').first().waitFor();
  assert.equal(await page.locator('.machine').count(), 8);
  assert.equal(await page.locator('main').evaluate(el => el.getBoundingClientRect().width), 1600);
  assert.equal(await page.locator('main').evaluate(el => getComputedStyle(el).paddingLeft), '20px');
  assert.equal(await page.locator('h1').evaluate(el => getComputedStyle(el).fontSize), '24px');
  assert.equal(await page.locator('.archive-item').count(),1);
  assert.equal(await page.locator('#archive-list b').count(),0);
  await page.screenshot({ path: path.join(screenshotDir, 'panel-desktop.png'), fullPage: true });
  await page.locator('.machine input[value="100"]').check();
  await page.locator('.machine input[value="106"]').check();
  const notes = 'Przed aktualizacją integracji\nDziałająca konfiguracja';
  const budgetNotes = 'Przed migracją bazy budżetu';
  assert.equal(await page.locator('#notes-101').isVisible(),false);
  await page.locator('#notes-100').fill(notes);
  await page.locator('#notes-106').fill(budgetNotes);
  await page.locator('.machine input[value="100"]').uncheck();
  assert.equal(await page.locator('#notes-100').isVisible(),false);
  await page.locator('.machine input[value="100"]').check();
  assert.equal(await page.locator('#notes-100').inputValue(),notes);
  assert.equal(await page.locator('#start').isEnabled(), true);
  await page.locator('#start').click();
  await page.getByRole('heading', { name: 'Trwa backup', exact: true }).waitFor();
  assert.deepEqual([...lastBackup.vmids].sort(),[100,106]);
  assert.equal(lastBackup.auto_eject,true);
  assert.deepEqual(lastBackup.notes,{'100':notes,'106':budgetNotes});
  assert.equal(await page.locator('#notes-100').isDisabled(), true);
  assert.deepEqual(await page.locator('.job-guest-note').allTextContents(),[notes,budgetNotes]);
  assert.equal(await page.locator('.job-guest-note test').count(), 0);
  assert.equal(await page.locator('#eject').isDisabled(), true);
  assert.equal(await page.locator('.machine input').first().isDisabled(), true);
  state.job.phase = 'succeeded';
  state.job.eject = 'safe';
  state.job.guests.forEach(vm => vm.backup = 'ok');
  state.usb = { state: 'unmounted', mounted: false };
  state.backups.items.unshift(...state.job.guests.map(vm=>({vmid:vm.vmid,type:vm.type,filename:`vzdump-${vm.type}-${vm.vmid}-2026_11_08-10_00_00.${vm.type==='lxc'?'tar':'vma'}.zst`,created:1794128400,size:2*1024**3,notes:`${vm.name} - ${vm.notes.split('\n')[0]}`})));
  state.backups.cached = true;
  await page.getByText('USB odmontowane po backupie. Można bezpiecznie wyjąć pendrive.', { exact: true }).waitFor({ timeout: 8000 });
  assert.equal(await page.locator('#start').isDisabled(), true);
  assert.equal(await page.locator('.archive-item').count(),3);
  assert.match(await page.locator('#archive-status').textContent(),/lista z pamięci/);
  // Remount without removing the USB; stale eject success must disappear.
  assert.equal(await page.locator('#mount').isVisible(), true);
  assert.equal(await page.locator('#mount').isEnabled(), true);
  await page.locator('#mount').click();
  await page.getByText('Ostatni backup zakończony. Pendrive jest ponownie zamontowany.', { exact: true }).waitFor({ timeout: 8000 });
  assert.equal(mountRequests, 1);
  assert.equal(await page.locator('#mount').isVisible(), false);
  assert.equal(await page.locator('#eject').isEnabled(), true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(screenshotDir, 'panel-mobile.png'), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  offline = true;
  await page.getByText('Test utraty połączenia').waitFor({ timeout: 8000 });
  assert.equal(await page.locator('#start').isDisabled(), true);
  assert.equal(await page.locator('#eject').isDisabled(), true);
  await page.locator('.machine input[value="101"]').check();
  assert.equal(await page.locator('#start').isDisabled(), true);
  offline = false;
  state.backups.items[0].notes = '<img src=x onerror="alert(1)">';
  await page.reload();
  await page.locator('.machine').first().waitFor();
  assert.equal(await page.locator('#login').isVisible(), false);
  assert.equal(await page.locator('.archive-note').first().textContent(), '<img src=x onerror="alert(1)">');
  assert.equal(await page.locator('#archive-list img').count(), 0);
  assert.deepEqual(errors, []);
  console.log('Browser OK: per-guest notes/drafts, independent payload, safe text rendering, existing/new backup list, cached list after eject, mount, session, network failure, mobile overflow.');
} finally {
  await browser.close();
  server.close();
}
