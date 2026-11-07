/* Fictional, in-memory simulator. No network requests or persistent storage. */
(() => {
  const GiB = 1024 ** 3;
  const t = (key, values) => window.HOMELAB_I18N.t(key, values);
  const baseTime = Date.UTC(2026, 10, 7, 12, 0, 0) / 1000;
  const machineData = [
    [200, 'smart-home', 'qemu', 24, 'running'],
    [201, 'dns-filter', 'lxc', 4, 'running'],
    [210, 'media-library', 'qemu', 48, 'running'],
    [220, 'cloud-files', 'qemu', 32, 'running'],
    [230, 'budget-sandbox', 'lxc', 12, 'running'],
    [240, 'test-workstation', 'qemu', 40, 'stopped']
  ];
  let state;
  let generation = 0;
  let run = 0;
  let timers = [];

  function archive(vm, created, size, notes) {
    const stamp = new Date(created * 1000).toISOString().slice(0, 19).replaceAll('-', '_').replace('T', '-').replaceAll(':', '_');
    const filename = `vzdump-${vm.type}-${vm.vmid}-${stamp}.${vm.type === 'lxc' ? 'tar' : 'vma'}.zst`;
    return { vmid: vm.vmid, type: vm.type, filename, volid: `demo-usb:backup/${filename}`,
      created, size, notes: notes.split('\n')[0], protected: vm.vmid === 200 && created < baseTime };
  }

  function reset() {
    timers.forEach(clearTimeout);
    timers = [];
    generation++;
    run = 0;
    const machines = machineData.map(([vmid, name, type, disk, status]) => ({ vmid, name, type,
      disk: disk * GiB, status, template: false }));
    state = {
      node: 'demo-node', storage: 'demo-usb', uuid: 'DEMO-USB-001', drive_name: 'Demo Backup Drive',
      usb: { state: 'mounted', mounted: true, free: 81.4 * GiB, total: 128 * GiB },
      machines, job: null, monitor_error: null,
      backups: { cached: false, error: null, updated: baseTime,
        items: [archive(machines[0], baseTime - 3600, 3.2 * GiB, `smart-home - ${t('demo_note_home')}`),
          archive(machines[3], baseTime - 7200, 8.7 * GiB, `cloud-files - ${t('demo_note_cloud')}`),
          archive(machines[4], baseTime - 86400, 1.4 * GiB, `budget-sandbox - ${t('demo_note_budget')}`)] }
    };
  }

  function later(callback, delay) {
    const current = generation;
    timers.push(setTimeout(() => { if (generation === current) callback(); }, delay));
  }

  function busy() {
    return state.job && ['starting', 'running', 'unmounting'].includes(state.job.phase);
  }

  function backup(data) {
    if (busy()) throw new Error('demo_busy');
    if (!state.usb.mounted) throw new Error('demo_mount_first');
    const ids = data?.vmids;
    if (!Array.isArray(ids) || !ids.length || new Set(ids).size !== ids.length
      || ids.some(id => !state.machines.some(vm => vm.vmid === id))) throw new Error('demo_select');
    const notes = data.notes || {};
    if (ids.some(id => typeof (notes[id] || '') !== 'string' || (notes[id] || '').length > 500)) {
      throw new Error('demo_notes_limit');
    }
    run++;
    const started = baseTime + run * 600;
    const guests = state.machines.filter(vm => ids.includes(vm.vmid)).map(vm => ({ ...vm,
      backup: 'pending', notes: (notes[vm.vmid] || '').trim() }));
    state.job = { id: `demo-${run}`, phase: 'running', started, auto_eject: !!data.auto_eject,
      guests, logs: ['[DEMO] Simulated backup queue. No Proxmox connection.'], error: null, eject: null };
    guests.forEach((vm, index) => {
      later(() => {
        vm.backup = 'running';
        state.job.logs.push(`[DEMO] Starting Backup of VM ${vm.vmid} (${vm.type})`);
      }, index * 1400 + 100);
      later(() => {
        vm.backup = 'ok';
        const size = (vm.type === 'lxc' ? 0.7 : 2.4) * GiB;
        const note = vm.notes ? `${vm.name} - ${vm.notes}`.replaceAll('{{guestname}}', vm.name)
          .replaceAll('{{vmid}}', String(vm.vmid)).replaceAll('{{node}}', 'demo-node').replaceAll('{{cluster}}', 'demo-cluster') : vm.name;
        state.backups.items.unshift(archive(vm, started + index, size, note));
        state.backups.items.sort((a, b) => b.created - a.created);
        state.backups.updated = started + index;
        state.usb.free = Math.max(0, state.usb.free - size);
        state.job.logs.push(`[DEMO] Finished Backup of VM ${vm.vmid}. Fictional archive added.`);
      }, (index + 1) * 1400);
    });
    later(() => {
      state.job.finished = started + guests.length;
      if (state.job.auto_eject) {
        state.job.phase = 'unmounting';
        later(() => {
          state.usb.state = 'unmounted';
          state.usb.mounted = false;
          state.backups.cached = true;
          state.job.phase = 'succeeded';
          state.job.eject = 'safe';
          state.job.logs.push('[DEMO] Simulated unmount completed. No real device was accessed.');
        }, 650);
      } else state.job.phase = 'succeeded';
    }, guests.length * 1400 + 200);
    return { job: structuredClone(state.job) };
  }

  async function request(path, data) {
    switch (path) {
      case '/api/status': return structuredClone(state);
      case '/api/backup': return backup(data);
      case '/api/mount':
      case '/api/eject': {
        if (busy()) throw new Error('demo_wait');
        const mounted = path === '/api/mount';
        state.usb.mounted = mounted;
        state.usb.state = mounted ? 'mounted' : 'unmounted';
        state.backups.cached = !mounted;
        if (!mounted && state.job?.phase === 'succeeded') state.job.eject = 'safe';
        return { message: 'Simulated USB state updated.' };
      }
      default: throw new Error('demo_unavailable');
    }
  }

  reset();
  window.HOMELAB_BACKUP_DEMO = Object.freeze({ request, reset });
})();
