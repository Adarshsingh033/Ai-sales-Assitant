/**
 * Tasks screen — follow-ups and reminders.
 *
 * Renders itself into #section-tasks. Public API:
 *   TasksModule.init()            show the screen (renders once, reloads data every call)
 *   TasksModule.openForm(opts)    add / edit dialog: { task?, contact?, lead?, onSaved? }
 *   TasksModule.refresh()         quiet reload of counts + list
 *
 * Relies on: Crm (crm.js), Api (api.js); optionally ContactsModule.openContact and LeadsModule.openLeadDetail.
 * Emits 'tasks-changed' on the Crm bus after every change and refreshes the sidebar badges.
 */
const TasksModule = (() => {
  const TABS = [
    { key: 'overdue', label: 'Overdue', count: 'overdue' },
    { key: 'today', label: 'Today', count: 'today' },
    { key: 'upcoming', label: 'Upcoming', count: 'upcoming' },
    { key: 'no_date', label: 'No date', count: 'no_date' },
    { key: 'done', label: 'Done', count: null },
  ];

  const EMPTY = {
    overdue: { icon: '🎉', title: "You're all caught up", text: 'Nothing is overdue. Nice work!', add: false },
    today: { icon: '☀️', title: 'Nothing due today', text: 'Enjoy the quiet — or add a task to plan your day.', add: true },
    upcoming: { icon: '📅', title: 'No upcoming tasks', text: 'Add one so nothing slips through the cracks.', add: true },
    no_date: { icon: '📝', title: 'No tasks without a date', text: 'Tasks you add without a due date wait here until you schedule them.', add: true },
    done: { icon: '✅', title: 'Completed tasks show up here', text: 'Finish a task and it will appear in this list.', add: false },
  };

  const ICON_EDIT = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.862 4.487 18.55 2.8a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125"/></svg>';
  const ICON_TRASH = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>';
  const ICON_CHECK = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';

  let rendered = false;
  let tab = 'today';
  let tabChosen = false;
  let scopeMine = true; // admins: "My tasks" (true) or "Everyone" (false)
  let summary = null;
  let items = null; // null = nothing loaded for the current tab/scope yet
  let loadError = null;
  let loadToken = 0;
  let refreshTimer = null;
  let pollTimer = null;
  let undoTimer = null;

  const $ = (id) => document.getElementById(id);
  const section = () => $('section-tasks');
  const mineParam = () => Crm.isAdmin && scopeMine;

  // ------------------------------------------------------------------ section markup
  function render(el) {
    el.innerHTML = `
      <div class="tenant-page-header">
        <div class="tenant-page-title">
          <h2>Tasks</h2>
          <p>Your follow-ups and reminders — never forget to call someone back.</p>
        </div>
        <div class="tenant-toolbar tasks-toolbar">
          <div class="crm-segmented" id="tasksScope" data-admin-only role="group" aria-label="Whose tasks">
            <button type="button" data-scope="mine" class="active">My tasks</button>
            <button type="button" data-scope="all">Everyone</button>
          </div>
          <button type="button" class="btn-primary" id="tasksAddBtn">+ Add task</button>
        </div>
      </div>
      <div class="crm-tabs" id="tasksTabs" role="tablist" aria-label="Task groups"></div>
      <div class="tasks-list crm-card" id="tasksList" aria-live="polite"></div>`;

    $('tasksAddBtn').addEventListener('click', () => openForm({}));
    $('tasksScope').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-scope]');
      if (!btn) return;
      const mine = btn.dataset.scope === 'mine';
      if (mine === scopeMine) return;
      scopeMine = mine;
      $('tasksScope').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b === btn));
      items = null;
      renderList();
      load();
    });
    $('tasksTabs').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-tab]');
      if (!btn || btn.dataset.tab === tab) return;
      tab = btn.dataset.tab;
      items = null;
      renderTabs();
      renderList();
      load();
    });
    $('tasksList').addEventListener('click', onListClick);
    renderTabs();
    renderList();
  }

  function renderTabs() {
    const host = $('tasksTabs');
    if (!host) return;
    host.innerHTML = TABS.map((t) => {
      const n = summary && t.count ? summary[t.count] || 0 : null;
      const badge = n === null ? '' : `<span class="crm-count${t.key === 'overdue' && n > 0 ? ' danger' : ''}">${n}</span>`;
      return `<button type="button" role="tab" class="crm-tab${t.key === tab ? ' active' : ''}" data-tab="${t.key}" aria-selected="${t.key === tab}">${Crm.esc(t.label)}${badge}</button>`;
    }).join('');
  }

  // ------------------------------------------------------------------ list
  function renderList() {
    const host = $('tasksList');
    if (!host) return;

    if (loadError) {
      host.innerHTML = `<div class="crm-empty"><div class="crm-empty-icon">⚠️</div><h4>We couldn't load your tasks</h4>
        <p>${Crm.esc(loadError)}</p><button type="button" class="btn-secondary" data-action="retry">Try again</button></div>`;
      return;
    }
    if (items === null) {
      host.innerHTML = '<div class="tasks-loading">Loading tasks…</div>';
      return;
    }
    if (!items.length) {
      const e = EMPTY[tab];
      host.innerHTML = `<div class="crm-empty"><div class="crm-empty-icon">${e.icon}</div><h4>${Crm.esc(e.title)}</h4>
        <p>${Crm.esc(e.text)}</p>${e.add ? '<button type="button" class="btn-primary" data-action="add">+ Add task</button>' : ''}</div>`;
      return;
    }
    host.innerHTML = items.map(rowHtml).join('');
  }

  function rowHtml(t) {
    const done = t.status === 'DONE';
    const due = Crm.dueLabel(t.due_at, done);
    const notes = t.notes ? `<div class="tasks-notes">${Crm.esc(t.notes.length > 140 ? t.notes.slice(0, 140) + '…' : t.notes)}</div>` : '';
    const chips = [];
    if (t.customer_id && t.customer_name) {
      chips.push(`<button type="button" class="tasks-chip" data-action="contact" data-id="${Crm.esc(t.customer_id)}" title="Open contact">${Crm.avatar(t.customer_name, 18)}<span>${Crm.esc(t.customer_name)}</span></button>`);
    }
    if (t.lead_id && t.lead_title) {
      chips.push(`<button type="button" class="tasks-chip tasks-chip-deal" data-action="deal" data-id="${Crm.esc(t.lead_id)}" title="Open deal"><span class="tasks-chip-label">Deal</span><span>${Crm.esc(t.lead_title)}</span></button>`);
    }
    const assignee = Crm.isAdmin && !scopeMine && t.assigned_user_name
      ? `<span class="tasks-assignee" title="Assigned to ${Crm.esc(t.assigned_user_name)}">${Crm.avatar(t.assigned_user_name, 22)}<span>${Crm.esc(t.assigned_user_name.split(' ')[0])}</span></span>`
      : '';
    return `
      <div class="tasks-row${done ? ' is-done' : ''}${due.state === 'overdue' ? ' is-overdue' : ''}" data-id="${Crm.esc(t.id)}">
        <button type="button" class="tasks-check" role="checkbox" aria-checked="${done}" data-action="toggle"
          aria-label="${done ? 'Mark as not done' : 'Mark as done'}: ${Crm.esc(t.title)}" title="${done ? 'Reopen this task' : 'Mark as done'}">${ICON_CHECK}</button>
        <div class="tasks-main">
          <div class="tasks-title">${Crm.esc(t.title)}</div>
          ${notes}
          ${chips.length ? `<div class="tasks-links">${chips.join('')}</div>` : ''}
        </div>
        <div class="tasks-meta">
          <span class="crm-due ${due.state}">${Crm.esc(due.text)}</span>
          ${assignee}
        </div>
        <div class="tasks-actions">
          <button type="button" class="crm-icon-btn" data-action="edit" aria-label="Edit task" title="Edit">${ICON_EDIT}</button>
          <button type="button" class="crm-icon-btn danger" data-action="delete" aria-label="Delete task" title="Delete">${ICON_TRASH}</button>
        </div>
      </div>`;
  }

  function onListClick(e) {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === 'add') return openForm({});
    if (action === 'retry') { loadError = null; items = null; renderList(); return load(); }
    if (action === 'contact') {
      if (window.ContactsModule && ContactsModule.openContact) ContactsModule.openContact(btn.dataset.id);
      return;
    }
    if (action === 'deal') {
      if (window.LeadsModule && LeadsModule.openLeadDetail) LeadsModule.openLeadDetail(btn.dataset.id);
      return;
    }
    const row = btn.closest('.tasks-row');
    const task = row && (items || []).find((t) => t.id === row.dataset.id);
    if (!task) return;
    if (action === 'toggle') toggleDone(task, row);
    else if (action === 'edit') openForm({ task });
    else if (action === 'delete') removeTask(task);
  }

  // ------------------------------------------------------------------ loading
  async function load({ quiet = false } = {}) {
    if (!rendered) return;
    const token = ++loadToken;
    const dayEnd = Crm.dayEndISO();
    const mine = mineParam();
    try {
      let sum;
      let list;
      if (!tabChosen) {
        // first open: land on Overdue if there is something overdue, otherwise Today
        sum = await Api.getTaskSummary({ day_end: dayEnd, mine });
        if (token !== loadToken) return;
        tab = sum.overdue > 0 ? 'overdue' : 'today';
        tabChosen = true;
        summary = sum;
        renderTabs();
        list = await Api.getTasks({ bucket: tab, day_end: dayEnd, mine, page_size: 200 });
      } else {
        [sum, list] = await Promise.all([
          Api.getTaskSummary({ day_end: dayEnd, mine }),
          Api.getTasks({ bucket: tab, day_end: dayEnd, mine, page_size: 200 }),
        ]);
      }
      if (token !== loadToken) return; // a newer request (tab / scope change) took over
      summary = sum;
      items = list.items || [];
      loadError = null;
    } catch (err) {
      if (token !== loadToken) return;
      // a failed background refresh keeps what is on screen; only a first load shows the error state
      if (!quiet || items === null) loadError = err.message || 'Something went wrong.';
    }
    renderTabs();
    renderList();
  }

  /** Reload soon; several triggers in a row (own change + bus event) collapse into one request. */
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => load({ quiet: true }), 150);
  }

  function changed() {
    Crm.emit('tasks-changed');
    Crm.refreshBadges();
    scheduleRefresh();
  }

  // ------------------------------------------------------------------ actions
  async function toggleDone(task, row) {
    if (row.classList.contains('is-busy')) return;
    const wasDone = task.status === 'DONE';
    row.classList.add('is-busy');
    if (!wasDone) row.classList.add('is-completing');
    try {
      if (wasDone) {
        await Api.reopenTask(task.id);
        Crm.toast('Task reopened');
      } else {
        await Api.completeTask(task.id);
        showUndo('Task done', async () => {
          try {
            await Api.reopenTask(task.id);
            changed();
          } catch (err) { Crm.toast(err.message, 'error'); }
        });
      }
      row.classList.add('is-leaving');
      changed();
    } catch (err) {
      row.classList.remove('is-busy', 'is-completing');
      Crm.toast(err.message || 'Could not update the task', 'error');
    }
  }

  async function removeTask(task) {
    const ok = await Crm.confirm(`“${task.title}” will be removed for good.`, {
      title: 'Delete this task?', confirmText: 'Delete task', danger: true,
    });
    if (!ok) return;
    try {
      await Api.deleteTask(task.id);
      Crm.toast('Task deleted');
      changed();
    } catch (err) {
      Crm.toast(err.message || 'Could not delete the task', 'error');
    }
  }

  /** Small bar at the bottom with an Undo link (the shared toast has no actions). */
  function showUndo(message, onUndo) {
    let bar = $('tasksUndo');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'tasksUndo';
      bar.className = 'tasks-undo';
      bar.setAttribute('role', 'status');
      document.body.appendChild(bar);
    }
    bar.innerHTML = `<span>${Crm.esc(message)}</span><button type="button">Undo</button>`;
    const hide = () => bar.classList.remove('show');
    bar.querySelector('button').onclick = () => { clearTimeout(undoTimer); hide(); onUndo(); };
    requestAnimationFrame(() => bar.classList.add('show'));
    clearTimeout(undoTimer);
    undoTimer = setTimeout(hide, 6000);
  }

  // ------------------------------------------------------------------ add / edit form
  function bucketFor(task) {
    if (task.status === 'DONE') return 'done';
    if (!task.due_at) return 'no_date';
    const due = new Date(task.due_at).getTime();
    if (due < Date.now()) return 'overdue';
    return due < new Date(Crm.dayEndISO()).getTime() ? 'today' : 'upcoming';
  }

  function openForm({ task = null, contact = null, lead = null, onSaved = null } = {}) {
    const editing = !!task;
    const formId = `tasksForm${Date.now()}`;
    const start = editing ? Crm.localParts(task.due_at) : Crm.quickDue('tomorrow');
    const leadContact = !contact && lead && lead.customer_id ? { id: lead.customer_id, name: lead.customer_name || 'Contact' } : null;
    const lockedContact = contact || leadContact;

    let relatedHtml = '';
    if (editing) {
      // links are fixed once a task exists; show them so it is clear what the task is about
      const lines = [];
      if (task.customer_name) lines.push(`<div class="tasks-readonly">${Crm.avatar(task.customer_name, 24)}<span>${Crm.esc(task.customer_name)}</span></div>`);
      if (task.lead_title) lines.push(`<div class="tasks-readonly"><span class="tasks-chip-label">Deal</span><span>${Crm.esc(task.lead_title)}</span></div>`);
      if (lines.length) relatedHtml = `<div class="form-group"><span class="form-label">About</span>${lines.join('')}</div>`;
    } else {
      relatedHtml = `<div class="form-group"><span class="form-label">Related contact <span class="crm-muted">(optional)</span></span><div id="${formId}Picker"></div></div>`;
      if (lead) relatedHtml += `<div class="form-group"><span class="form-label">Deal</span><div class="tasks-readonly"><span class="tasks-chip-label">Deal</span><span>${Crm.esc(lead.title || 'Deal')}</span></div></div>`;
    }

    const m = Crm.modal({
      title: editing ? 'Edit task' : 'Add a task',
      width: 520,
      body: `
        <form class="crm-form tasks-form" id="${formId}" novalidate>
          <div class="crm-error" data-error hidden role="alert"></div>
          <div class="form-group">
            <label class="form-label" for="${formId}Title">What do you need to do? *</label>
            <input type="text" id="${formId}Title" class="form-input" maxlength="255" autocomplete="off" autofocus
              placeholder="e.g. Call Priya about the proposal" />
          </div>
          <div class="form-group">
            <span class="form-label">When?</span>
            <div class="crm-chips" data-chips role="group" aria-label="Quick due dates">
              <button type="button" class="crm-chip" data-when="today">Today</button>
              <button type="button" class="crm-chip" data-when="tomorrow">Tomorrow</button>
              <button type="button" class="crm-chip" data-when="nextweek">Next week</button>
              <button type="button" class="crm-chip" data-when="none">No date</button>
            </div>
            <div class="crm-field-row">
              <input type="date" class="form-input" data-date aria-label="Due date" />
              <input type="time" class="form-input" data-time aria-label="Due time" />
            </div>
            <span class="crm-hint">No time? We'll remind you at 9:00 AM that day.</span>
          </div>
          ${relatedHtml}
          <div class="form-group">
            <label class="form-label" for="${formId}Notes">Notes <span class="crm-muted">(optional)</span></label>
            <textarea id="${formId}Notes" class="form-input" rows="3" maxlength="2000" placeholder="Anything you want to remember…"></textarea>
          </div>
          <div class="form-group" data-admin-only>
            <label class="form-label" for="${formId}Assign">Assign to</label>
            <select id="${formId}Assign" class="form-input"></select>
          </div>
        </form>`,
      footer: `<button type="button" class="btn-secondary" data-close>Cancel</button>
               <button type="submit" form="${formId}" class="btn-primary" data-save>${editing ? 'Save changes' : 'Add task'}</button>`,
    });

    const root = m.body;
    const form = root.querySelector('form');
    const errorEl = root.querySelector('[data-error]');
    const titleEl = root.querySelector(`#${formId}Title`);
    const notesEl = root.querySelector(`#${formId}Notes`);
    const dateEl = root.querySelector('[data-date]');
    const timeEl = root.querySelector('[data-time]');
    const assignEl = root.querySelector(`#${formId}Assign`);
    const saveBtn = m.footer.querySelector('[data-save]');
    const chips = root.querySelectorAll('[data-when]');

    titleEl.value = task ? task.title : '';
    notesEl.value = task && task.notes ? task.notes : '';
    dateEl.value = start.date;
    timeEl.value = start.time;

    function setChip(name) { chips.forEach((c) => c.classList.toggle('active', c.dataset.when === name)); }
    setChip(editing ? (task.due_at ? null : 'none') : 'tomorrow');
    chips.forEach((chip) => chip.addEventListener('click', () => {
      const kind = chip.dataset.when;
      const parts = kind === 'none' ? { date: '', time: '' } : Crm.quickDue(kind);
      dateEl.value = parts.date;
      timeEl.value = parts.time;
      setChip(kind);
    }));
    const unchip = () => setChip(null);
    dateEl.addEventListener('input', unchip);
    timeEl.addEventListener('input', unchip);

    let picker = null;
    const pickerHost = root.querySelector(`#${formId}Picker`);
    if (pickerHost) picker = Crm.contactPicker(pickerHost, { value: lockedContact, locked: !!lockedContact });

    if (assignEl) Crm.fillTeamSelect(assignEl, { value: task ? task.assigned_user_id : Crm.user.id });

    function showError(msg) {
      errorEl.textContent = msg;
      errorEl.hidden = !msg;
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      showError('');
      const title = titleEl.value.trim();
      if (!title) { showError('Please say what needs to be done.'); titleEl.focus(); return; }
      const due = Crm.toISO(dateEl.value, timeEl.value);
      const notes = notesEl.value.trim();

      Crm.busy(saveBtn, true, 'Saving…');
      try {
        let saved;
        if (editing) {
          const patch = {};
          if (title !== task.title) patch.title = title;
          if (notes !== (task.notes || '')) patch.notes = notes || null;
          const before = task.due_at ? Math.floor(new Date(task.due_at).getTime() / 60000) : null;
          const after = due ? Math.floor(new Date(due).getTime() / 60000) : null;
          if (before !== after) patch.due_at = due;
          if (assignEl && assignEl.value && assignEl.value !== task.assigned_user_id) patch.assigned_user_id = assignEl.value;
          if (!Object.keys(patch).length) { m.close(); return; }
          saved = await Api.updateTask(task.id, patch);
        } else {
          const payload = { title };
          if (notes) payload.notes = notes;
          if (due) payload.due_at = due;
          const chosen = picker && picker.value;
          if (chosen) payload.customer_id = chosen.id;
          if (lead) payload.lead_id = lead.id;
          if (assignEl && assignEl.value) payload.assigned_user_id = assignEl.value;
          saved = await Api.createTask(payload);
        }
        Crm.toast(editing ? 'Task updated' : 'Task added');
        m.close();
        // show the new task: jump to the tab it lives in (when it is one of my own)
        if (rendered && !editing && (!Crm.isAdmin || !scopeMine || saved.assigned_user_id === Crm.user.id)) {
          const target = bucketFor(saved);
          if (target !== tab) { tab = target; items = null; renderTabs(); renderList(); }
        }
        changed();
        if (onSaved) onSaved(saved);
      } catch (err) {
        Crm.busy(saveBtn, false);
        showError(err.message || 'Could not save the task. Please try again.');
      }
    });
  }

  // ------------------------------------------------------------------ public
  function init() {
    const el = section();
    if (!el) return;
    if (!rendered) {
      rendered = true;
      render(el);
      pollTimer = setInterval(() => {
        const s = section();
        if (s && !s.hidden && !document.hidden) load({ quiet: true });
      }, 60000);
    }
    load({ quiet: items !== null });
  }

  function refresh() { return load({ quiet: true }); }

  Crm.on('tasks-changed', () => { if (rendered) scheduleRefresh(); });
  Crm.on('route:tasks', (p) => { if (p && p.new) openForm({}); });

  return { init, openForm, refresh };
})();

window.TasksModule = TasksModule;
