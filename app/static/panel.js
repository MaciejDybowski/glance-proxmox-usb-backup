(() => {
  const $ = id => document.getElementById(id);
  // Defined only by the static demo build; the installed service never serves it.
  const demo = window.HOMELAB_BACKUP_DEMO;
  function savedToken(value) {
    try {
      if (value === undefined) return sessionStorage.getItem('homelab-backup-token') || '';
      if (value) sessionStorage.setItem('homelab-backup-token', value);
      else sessionStorage.removeItem('homelab-backup-token');
    } catch { /* Browsers may disable storage inside cross-site iframes. */ }
    return '';
  }
  let token = demo ? 'demo' : savedToken();
  let state = null;
  let pending = false;
  let polling = false;
  let connectionHealthy = false;
  let machineSignature = '';
  const selected = new Set();
  const notesByGuest = new Map();
  const busyPhases = new Set(['starting', 'running', 'unmounting', 'uncertain']);
  const bytes = n => `${(n / 1024 ** 3).toLocaleString('pl-PL', { maximumFractionDigits: 1 })} GiB`;
  const backupLabels = { pending: 'oczekuje', running: 'w toku', ok: 'gotowe', failed: 'błąd', skipped: 'pominięto', unknown: 'sprawdź log' };

  function message(text = '', error = false) {
    $('message').textContent = text;
    $('message').className = error ? 'error' : 'safe';
  }

  async function api(path, data) {
    if (demo) return demo.request(path, data);
    const headers = { Authorization: `Bearer ${token}` };
    if (data !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { method: data === undefined ? 'GET' : 'POST', headers,
      body: data === undefined ? undefined : JSON.stringify(data), cache: 'no-store', signal: AbortSignal.timeout(150000) });
    const result = await response.json();
    if (response.status === 401) lock();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }

  function lock() {
    if (demo) {
      demo.reset();
      selected.clear();
      notesByGuest.clear();
      machineSignature = '';
      message();
      poll();
      return;
    }
    token = '';
    savedToken('');
    $('login').hidden = false;
    $('dashboard').hidden = true;
  }

  function renderMachines() {
    const signature = JSON.stringify(state.machines);
    if (signature === machineSignature) return;
    machineSignature = signature;
    const valid = new Set(state.machines.filter(vm => !vm.template).map(vm => vm.vmid));
    for (const id of selected) if (!valid.has(id)) selected.delete(id);
    $('machines').replaceChildren(...state.machines.map(vm => {
      const card = document.createElement('div');
      card.className = 'machine';
      const label = document.createElement('label');
      label.className = 'machine-choice';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = String(vm.vmid);
      checkbox.checked = selected.has(vm.vmid);
      checkbox.dataset.template = String(vm.template);
      checkbox.addEventListener('change', () => {
        checkbox.checked ? selected.add(vm.vmid) : selected.delete(vm.vmid);
        controls();
      });
      const content = document.createElement('span');
      content.className = 'machine-content';
      const name = document.createElement('span');
      name.className = 'machine-name';
      name.textContent = vm.name;
      const meta = document.createElement('span');
      meta.className = 'machine-meta';
      const dot = document.createElement('span');
      dot.className = `status-dot ${vm.status === 'running' ? 'running' : ''}`;
      meta.append(dot, `${vm.type === 'lxc' ? 'LXC' : 'VM'} · ${vm.template ? 'szablon' : vm.status === 'running' ? 'uruchomiona' : 'zatrzymana'}`);
      content.append(name, meta);
      const id = document.createElement('span');
      id.className = 'machine-id';
      id.textContent = String(vm.vmid);
      label.append(checkbox, content, id);
      const notesField = document.createElement('div');
      notesField.className = 'machine-notes';
      const notesLabel = document.createElement('label');
      notesLabel.htmlFor = `notes-${vm.vmid}`;
      notesLabel.textContent = 'Notatka do tej kopii · opcjonalnie';
      const notes = document.createElement('textarea');
      notes.id = `notes-${vm.vmid}`;
      notes.rows = 2;
      notes.maxLength = 500;
      notes.dataset.vmid = String(vm.vmid);
      notes.value = notesByGuest.get(vm.vmid) || '';
      notes.placeholder = `Np. przed aktualizacją ${vm.name}`;
      notes.addEventListener('input', () => notesByGuest.set(vm.vmid, notes.value));
      notesField.append(notesLabel, notes);
      card.append(label, notesField);
      return card;
    }));
  }

  function controls() {
    if (!state) return;
    const busy = pending || !connectionHealthy || state.monitor_error || (state.job && busyPhases.has(state.job.phase));
    $('start').disabled = busy || !state.usb.mounted || !selected.size;
    $('eject').disabled = busy || !state.usb.mounted;
    $('eject').hidden = !state.usb.mounted;
    $('mount').hidden = state.usb.mounted;
    $('mount').disabled = busy || state.usb.state !== 'unmounted';
    $('auto-eject').disabled = busy;
    $('toggle-all').disabled = busy;
    document.querySelectorAll('.machine input').forEach(input => {
      input.disabled = busy || input.dataset.template === 'true';
      input.checked = selected.has(Number(input.value));
    });
    document.querySelectorAll('.machine-notes textarea').forEach(input => {
      const checked = selected.has(Number(input.dataset.vmid));
      input.parentElement.hidden = !checked;
      input.disabled = busy || !checked;
    });
    $('selection-count').textContent = selected.size ? `Wybrane maszyny: ${selected.size}` : 'Nie wybrano maszyn';
    const disk = state.machines.filter(vm => selected.has(vm.vmid)).reduce((sum, vm) => sum + vm.disk, 0);
    $('disk-size').textContent = selected.size ? `Dyski główne: ${bytes(disk)}` : '';
    $('toggle-all').textContent = selected.size === state.machines.filter(vm => !vm.template).length ? 'Odznacz wszystkie' : 'Zaznacz wszystkie';
  }

  function render() {
    $('login').hidden = true;
    $('dashboard').hidden = false;
    $('uuid').textContent = state.uuid;
    $('drive-name').textContent = state.drive_name || 'USB backup';
    const usbLabels = { mounted: 'Zamontowany · gotowy do zapisu', unmounted: 'Odmontowany · można wyjąć',
      absent: 'Pendrive odłączony', wrong_device: 'Inny nośnik — backup zablokowany', read_only: 'Tylko do odczytu — backup zablokowany' };
    $('usb-status').textContent = usbLabels[state.usb.state] || 'Nieznany stan USB';
    $('capacity').textContent = state.usb.mounted ? `${bytes(state.usb.free)} wolne / ${bytes(state.usb.total)}` : '—';
    $('space').value = state.usb.mounted && state.usb.total ? 100 * (1 - state.usb.free / state.usb.total) : 0;
    renderMachines();
    renderBackups();
    controls();
    const job = state.job;
    $('job').hidden = !job;
    if (!job) return;
    const titles = { starting: 'Uruchamianie zadania', running: 'Trwa backup', unmounting: 'Odmontowywanie USB',
      succeeded: 'Backup zakończony', failed: 'Backup wymaga uwagi', uncertain: 'Sprawdź stan zadania' };
    $('job-title').textContent = titles[job.phase] || job.phase;
    const complete = job.guests.filter(vm => ['ok', 'failed'].includes(vm.backup)).length;
    $('job-count').textContent = `${complete} / ${job.guests.length}`;
    $('job-progress').max = job.guests.length;
    $('job-progress').value = complete;
    $('job-guests').replaceChildren(...job.guests.map(vm => {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = `${vm.vmid} / ${vm.name}`;
      const description = document.createElement('div');
      description.className = 'job-guest-description';
      description.append(name);
      if (vm.notes) {
        const note = document.createElement('p');
        note.className = 'job-guest-note';
        note.textContent = vm.notes;
        description.append(note);
      }
      const status = document.createElement('span');
      status.className = `backup-state ${vm.backup}`;
      status.textContent = backupLabels[vm.backup] || vm.backup;
      li.append(description, status);
      return li;
    }));
    $('job-result').textContent = state.monitor_error || job.error || (job.eject === 'safe'
      ? state.usb.mounted ? 'Ostatni backup zakończony. Pendrive jest ponownie zamontowany.' : 'USB odmontowane po backupie. Można bezpiecznie wyjąć pendrive.'
      : job.phase === 'succeeded' ? 'Kopie zapisane. USB pozostaje zamontowane.' : '');
    $('job-result').className = job.error || state.monitor_error ? 'error' : 'safe';
    $('acknowledge').hidden = job.phase !== 'uncertain';
    $('acknowledge').disabled = pending;
    const log = $('logs');
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.textContent = job.logs.join('\n');
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function renderBackups() {
    const inventory = state.backups || { items: [], updated: null, cached: true };
    const items = inventory.items;
    const total = items.reduce((sum, item) => sum + (item.size || 0), 0);
    const copyWord = { one: 'kopia', few: 'kopie', many: 'kopii', other: 'kopii' }[new Intl.PluralRules('pl-PL').select(items.length)];
    $('archive-count').textContent = items.length ? `${items.length} ${copyWord} · ${bytes(total)}` : '';
    const updated = inventory.updated ? new Date(inventory.updated * 1000).toLocaleString('pl-PL') : '';
    $('archive-status').textContent = inventory.updated
      ? `${inventory.cached ? `Ostatni odczyt ${state.drive_name || 'USB'} — lista z pamięci` : 'Lista ze zamontowanego USB'} · ${updated}`
      : 'Lista pojawi się po odczycie zamontowanego pendrive’a.';
    $('archive-error').hidden = !inventory.error;
    $('archive-error').textContent = inventory.error || '';
    $('archive-content').hidden = !items.length;
    $('archive-empty').hidden = !!items.length;
    $('archive-empty').textContent = inventory.updated
      ? inventory.cached ? 'Podczas ostatniego odczytu nie znaleziono kopii.' : 'Na pendrivie nie ma jeszcze gotowych backupów.'
      : state.usb.mounted ? 'Oczekiwanie na listę kopii.' : 'Zamontuj pendrive, aby sprawdzić zapisane kopie.';
    const names = new Map(state.machines.map(vm => [vm.vmid, vm.name]));
    $('archive-list').replaceChildren(...items.map(item => {
      const row = document.createElement('li');
      row.className = 'archive-item';
      const guest = document.createElement('div');
      guest.className = 'archive-guest';
      const name = document.createElement('span');
      name.className = 'archive-name';
      name.textContent = `${item.vmid} / ${names.get(item.vmid) || 'maszyna spoza bieżącej listy'}`;
      const file = document.createElement('span');
      file.className = 'archive-file';
      file.textContent = item.filename;
      const meta = document.createElement('div');
      meta.className = 'archive-meta';
      meta.textContent = `${item.type === 'lxc' ? 'LXC' : 'VM'}${item.protected ? ' · chroniona kopia' : ''}`;
      guest.append(name, file, meta);
      const cell = (label, value, className = '') => {
        const div = document.createElement('div');
        div.className = className;
        const caption = document.createElement('span');
        caption.className = 'archive-label';
        caption.textContent = label;
        const text = document.createElement('span');
        text.textContent = value;
        div.append(caption, text);
        return div;
      };
      const date = item.created ? new Date(item.created * 1000).toLocaleString('pl-PL', { dateStyle: 'short', timeStyle: 'short' }) : '—';
      const notes = cell('Notes', item.notes || '—', 'archive-note-cell');
      notes.lastElementChild.className = 'archive-note';
      row.append(guest, cell('Utworzono', date), cell('Rozmiar', item.size == null ? '—' : bytes(item.size), 'archive-size'), notes);
      return row;
    }));
  }

  async function poll() {
    if (!token || polling || pending) return;
    polling = true;
    try {
      state = await api('/api/status');
      if (!token) return;
      connectionHealthy = true;
      render();
    } catch (error) {
      connectionHealthy = false;
      // Never leave actionable controls enabled with stale USB/job information.
      $('start').disabled = true;
      $('eject').disabled = true;
      $('mount').disabled = true;
      message(error.message, true);
    } finally { polling = false; }
  }

  async function action(path, payload, success) {
    if (pending) return;
    pending = true;
    controls();
    message();
    try {
      await api(path, payload);
      message(success);
    } catch (error) { message(error.message, true); }
    finally { pending = false; await poll(); }
  }

  $('login').addEventListener('submit', async event => {
    event.preventDefault();
    const button = $('login').querySelector('button');
    button.disabled = true;
    try {
      const result = await api('/api/login', { password: $('password').value });
      token = result.token;
      savedToken(token);
      $('password').value = '';
      message();
      await poll();
    } catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
  });
  $('backup-form').addEventListener('submit', event => {
    event.preventDefault();
    const notes = Object.fromEntries([...selected].map(id => [String(id), notesByGuest.get(id) || '']));
    action('/api/backup', { vmids: [...selected], auto_eject: $('auto-eject').checked, notes }, 'Zadanie przekazane do Proxmoxa. Możesz zamknąć dashboard.');
  });
  $('toggle-all').addEventListener('click', () => {
    const ids = state.machines.filter(vm => !vm.template).map(vm => vm.vmid);
    if (ids.every(id => selected.has(id))) selected.clear(); else ids.forEach(id => selected.add(id));
    controls();
  });
  $('eject').addEventListener('click', () => action('/api/eject', {}, 'Można bezpiecznie wyjąć pendrive.'));
  $('mount').addEventListener('click', () => action('/api/mount', {}, 'Pendrive zamontowany. Możesz uruchomić backup.'));
  $('acknowledge').addEventListener('click', () => action('/api/acknowledge', {}, 'Stan zadania sprawdzony.'));
  $('logout').addEventListener('click', () => { lock(); message(); });
  if (demo) $('logout').textContent = 'Resetuj demo';
  setInterval(poll, demo ? 500 : 5000);
  poll();

  // Only the public layout height is sent; session/data never leave this frame.
  let previousHeight = 0;
  new ResizeObserver(() => {
    const height = Math.ceil(document.querySelector('main').getBoundingClientRect().height);
    if (height !== previousHeight) {
      previousHeight = height;
      window.parent.postMessage({ type: 'homelab-backup-height', height }, '*');
    }
  }).observe(document.querySelector('main'));
})();
