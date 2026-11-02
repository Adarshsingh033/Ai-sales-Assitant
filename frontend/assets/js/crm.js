/**
 * CRM shared helpers — used by Home, Contacts, Pipeline, Tasks and the deal modal.
 *
 * Exposes a single global: `Crm`.
 *   Crm.isAdmin / Crm.isRep          who is logged in
 *   Crm.esc / money / initials / relTime / dueLabel    formatting
 *   Crm.modal / drawer / confirm      dialogs (consistent look, Esc + click-outside to close)
 *   Crm.contactPicker                 "search a contact" field
 *   Crm.team / fillTeamSelect         people a deal, contact or task can be given to
 *   Crm.on / off / emit               tiny event bus (e.g. 'tasks-changed')
 *   Crm.goto('pipeline', { lead: id })  move around the app; modules listen with Crm.on('route:pipeline', fn)
 */
const Crm = (() => {
  const user = (typeof Auth !== 'undefined' && Auth.getCurrentUser()) || {};
  const isAdmin = user.role === 'TENANT_ADMIN';
  const isRep = user.role === 'SALES_USER';

  const STAGES = [
    { key: 'NEW', label: 'New' },
    { key: 'CONTACTED', label: 'Contacted' },
    { key: 'QUALIFIED', label: 'Qualified' },
    { key: 'PROPOSAL', label: 'Proposal' },
    { key: 'WON', label: 'Won' },
    { key: 'LOST', label: 'Lost' },
  ];
  const PRIORITIES = [
    { key: 'HOT', label: 'Hot' },
    { key: 'HIGH', label: 'High' },
    { key: 'MEDIUM', label: 'Medium' },
    { key: 'LOW', label: 'Low' },
  ];
  const SECTIONS = {
    home: 'section-dashboard',
    inbox: 'section-conversations',
    contacts: 'section-contacts',
    pipeline: 'section-leads',
    tasks: 'section-tasks',
    products: 'section-products',
    inventory: 'section-inventory',
    knowledge: 'section-knowledge',
    ai: 'section-ai-config',
    team: 'section-user-management',
  };

  // ---------------------------------------------------------------- formatting
  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function money(value) {
    if (value === null || value === undefined || value === '') return '—';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
  }

  function initials(name) {
    const parts = String(name || '?').trim().split(/\s+/).filter(Boolean);
    return ((parts[0]?.[0] || '?') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
  }

  /** A round initials badge; the colour is derived from the name so the same person always looks the same. */
  function avatar(name, size = 32) {
    let hash = 0;
    for (const ch of String(name || '?')) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
    return `<span class="crm-avatar" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.38)}px;background:hsl(${hash} 38% 90%);color:hsl(${hash} 45% 28%)" aria-hidden="true">${esc(initials(name))}</span>`;
  }

  /** Telegram customers are stored with an internal "+tg_<chat id>" phone; show that as what it is. */
  function isInternalPhone(phone) { return /^\+?tg_/i.test(phone || ''); }
  function phoneLabel(phone) { return isInternalPhone(phone) ? 'Telegram chat' : (phone || ''); }

  function relTime(iso) {
    if (!iso) return '—';
    const then = new Date(iso);
    const secs = Math.floor((Date.now() - then.getTime()) / 1000);
    if (secs < 45) return 'just now';
    if (secs < 3600) return `${Math.floor(secs / 60) || 1} min ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
    if (secs < 172800) return 'yesterday';
    if (secs < 7 * 86400) return `${Math.floor(secs / 86400)} days ago`;
    return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: then.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
  }

  function startOfDay(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
  }

  /** End of the user's local day as ISO — the API needs it to split tasks into Today / Upcoming. */
  function dayEndISO() {
    const end = startOfDay(new Date());
    end.setDate(end.getDate() + 1);
    return end.toISOString();
  }

  /**
   * How to show a task's due time. Returns { text, state } where state is
   * 'overdue' | 'today' | 'soon' | 'none' | 'done'.
   */
  function dueLabel(iso, done = false) {
    if (!iso) return { text: 'No due date', state: done ? 'done' : 'none' };
    const due = new Date(iso);
    const time = due.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const dayDiff = Math.round((startOfDay(due) - startOfDay(new Date())) / 86400000);
    let day;
    if (dayDiff === 0) day = 'Today';
    else if (dayDiff === -1) day = 'Yesterday';
    else if (dayDiff === 1) day = 'Tomorrow';
    else if (dayDiff > 1 && dayDiff < 7) day = due.toLocaleDateString(undefined, { weekday: 'long' });
    else day = due.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: due.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
    let state = 'soon';
    if (done) state = 'done';
    else if (due.getTime() < Date.now()) state = 'overdue';
    else if (dayDiff === 0) state = 'today';
    return { text: `${day}, ${time}`, state };
  }

  const pad = (n) => String(n).padStart(2, '0');

  /** ISO → { date: 'YYYY-MM-DD', time: 'HH:MM' } in the user's local time (for <input type=date|time>). */
  function localParts(iso) {
    if (!iso) return { date: '', time: '' };
    const d = new Date(iso);
    return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}` };
  }

  /** Local date + time strings → ISO for the API. Missing time defaults to 9:00 AM. Returns null without a date. */
  function toISO(date, time) {
    if (!date) return null;
    const d = new Date(`${date}T${time || '09:00'}`);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  }

  /** Shortcuts for the "when?" chips: 'today' (next full hour), 'tomorrow' (9 AM), 'nextweek' (Monday 9 AM). */
  function quickDue(kind) {
    let d = new Date();
    if (kind === 'today') {
      // the next full hour that is at least 30 minutes away; late at night, just before midnight
      d = new Date(Date.now() + 30 * 60000);
      d.setMinutes(0, 0, 0);
      d.setHours(d.getHours() + 1);
      if (d.getDate() !== new Date().getDate()) { d = new Date(); d.setHours(23, 59, 0, 0); }
    } else if (kind === 'tomorrow') {
      d.setDate(d.getDate() + 1);
      d.setHours(9, 0, 0, 0);
    } else if (kind === 'nextweek') {
      d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
      d.setHours(9, 0, 0, 0);
    }
    return localParts(d.toISOString());
  }

  function stagePill(stage) {
    const s = STAGES.find((x) => x.key === stage);
    return `<span class="stage-pill stage-${esc(String(stage || 'NEW').toLowerCase())}">${esc(s ? s.label : stage)}</span>`;
  }

  function priorityPill(priority) {
    const p = PRIORITIES.find((x) => x.key === priority);
    return `<span class="priority-pill ${esc(String(priority || 'MEDIUM').toLowerCase())}">${esc(p ? p.label : priority)}</span>`;
  }

  /** The AI's lead score; empty when the AI never scored the deal (e.g. one added by hand). */
  function scorePill(score) {
    const s = Math.round(score || 0);
    if (!s) return '';
    const cls = s >= 70 ? 'score-hot' : s >= 40 ? 'score-warm' : 'score-cold';
    return `<span class="lead-score-pill ${cls}" title="AI lead score">${s}/100</span>`;
  }

  function toast(message, type = 'success') {
    if (window.showToast) window.showToast(message, type);
  }

  /** Disable a button and show progress while an async action runs. */
  function busy(button, on, label = 'Saving…') {
    if (!button) return;
    if (on) {
      button.dataset.label = button.textContent;
      button.textContent = label;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
    }
  }

  // ---------------------------------------------------------------- events
  const listeners = {};
  function on(name, fn) { (listeners[name] = listeners[name] || []).push(fn); }
  function off(name, fn) { listeners[name] = (listeners[name] || []).filter((f) => f !== fn); }
  function emit(name, payload) { (listeners[name] || []).slice().forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } }); }

  // ---------------------------------------------------------------- navigation
  /** Open a section by key ('home', 'inbox', 'contacts', 'pipeline', 'tasks', ...) with optional deep-link params. */
  function goto(key, params) {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    window.location.hash = `#${key}${qs}`;
  }

  function sectionId(key) { return SECTIONS[key]; }
  function sectionKey(id) { return Object.keys(SECTIONS).find((k) => SECTIONS[k] === id); }

  // ---------------------------------------------------------------- team
  let teamCache = null;
  function team(force = false) {
    if (!teamCache || force) {
      teamCache = Api.getTeam().catch((err) => { teamCache = null; throw err; });
    }
    return teamCache;
  }

  /** Fill a <select> with team members. Options: { value, unassigned: 'Unassigned' | false, everyone: 'Everyone' | false }. */
  async function fillTeamSelect(select, { value = '', unassigned = false, everyone = false } = {}) {
    let members = [];
    try { members = await team(); } catch (e) { /* leave only the fixed options */ }
    const opts = [];
    if (everyone) opts.push(`<option value="">${esc(everyone)}</option>`);
    if (unassigned) opts.push(`<option value="${everyone ? '__none__' : ''}">${esc(unassigned)}</option>`);
    members.forEach((m) => opts.push(`<option value="${esc(m.id)}">${esc(m.name)}${m.is_me ? ' (you)' : ''}</option>`));
    select.innerHTML = opts.join('');
    select.value = value || '';
  }

  // ---------------------------------------------------------------- dialogs
  const dialogStack = [];
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && dialogStack.length) dialogStack[dialogStack.length - 1].close();
  });

  /** Close every open dialog and drawer (used when the user moves to another section). */
  function closeDialogs() {
    dialogStack.slice().reverse().forEach((d) => d.close());
  }

  function mountDialog(overlay, onClose) {
    let closed = false;
    const handle = {
      el: overlay,
      close() {
        if (closed) return;
        closed = true;
        const i = dialogStack.indexOf(handle);
        if (i >= 0) dialogStack.splice(i, 1);
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 220);
        if (onClose) onClose();
      },
    };
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) handle.close(); });
    overlay.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => handle.close()));
    document.body.appendChild(overlay);
    dialogStack.push(handle);
    requestAnimationFrame(() => overlay.classList.add('active'));
    return handle;
  }

  const CLOSE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>';

  function setContent(el, content) {
    if (content === undefined || content === null) return;
    if (typeof content === 'string') el.innerHTML = content;
    else { el.innerHTML = ''; el.appendChild(content); }
  }

  /**
   * Centered dialog. Returns { el, root, body, footer, close, setTitle }.
   * `body` / `footer` may be HTML strings or DOM nodes; elements with [data-close] close the dialog.
   */
  function modal({ title = '', body = '', footer = '', width = 520, onClose } = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay crm-modal-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', title);
    overlay.innerHTML = `
      <div class="modal crm-modal" style="max-width:${width}px">
        <div class="modal-header">
          <h3 class="modal-title">${esc(title)}</h3>
          <button type="button" class="modal-close" data-close aria-label="Close">${CLOSE_ICON}</button>
        </div>
        <div class="modal-body crm-modal-body"></div>
        <div class="modal-footer crm-modal-footer"${footer ? '' : ' hidden'}></div>
      </div>`;
    const bodyEl = overlay.querySelector('.crm-modal-body');
    const footerEl = overlay.querySelector('.crm-modal-footer');
    setContent(bodyEl, body);
    setContent(footerEl, footer);
    const handle = mountDialog(overlay, onClose);
    overlay.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => handle.close()));
    setTimeout(() => (overlay.querySelector('[autofocus]') || overlay.querySelector('input:not([type=hidden]), textarea, select'))?.focus(), 60);
    return Object.assign(handle, {
      root: overlay.querySelector('.modal'),
      body: bodyEl,
      footer: footerEl,
      setTitle(t) { overlay.querySelector('.modal-title').textContent = t; },
    });
  }

  /** Right-hand slide-over panel. Returns { el, root, header, body, close }. */
  function drawer({ width = 560, onClose } = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'crm-drawer-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.innerHTML = `
      <aside class="crm-drawer" style="width:min(${width}px, 100vw)">
        <button type="button" class="crm-drawer-close" data-close aria-label="Close">${CLOSE_ICON}</button>
        <div class="crm-drawer-header"></div>
        <div class="crm-drawer-body"></div>
      </aside>`;
    const handle = mountDialog(overlay, onClose);
    return Object.assign(handle, {
      root: overlay.querySelector('.crm-drawer'),
      header: overlay.querySelector('.crm-drawer-header'),
      body: overlay.querySelector('.crm-drawer-body'),
    });
  }

  /** Yes/no question. Resolves true only when the confirm button is pressed. */
  function confirm(message, { title = 'Are you sure?', confirmText = 'Yes, continue', danger = false } = {}) {
    return new Promise((resolve) => {
      let answered = false;
      const m = modal({
        title,
        width: 420,
        body: `<p class="crm-confirm-text">${esc(message)}</p>`,
        footer: `<button type="button" class="btn-secondary" data-close>Cancel</button>
                 <button type="button" class="${danger ? 'btn-danger' : 'btn-primary'}" id="crmConfirmYes">${esc(confirmText)}</button>`,
        onClose: () => { if (!answered) resolve(false); },
      });
      m.footer.querySelector('#crmConfirmYes').addEventListener('click', () => { answered = true; resolve(true); m.close(); });
    });
  }

  // ---------------------------------------------------------------- contact picker
  /**
   * A search box that lets the user pick (or quickly create) a contact.
   *   const picker = Crm.contactPicker(hostEl, { value: contactOrNull, onChange: (contact|null) => {} });
   *   picker.value  → the chosen contact ({ id, name, ... }) or null;   picker.set(contact);  picker.clear();
   * Pass `locked: true` to show the contact without a "change" button.
   */
  function contactPicker(host, { value = null, onChange, locked = false, placeholder = 'Search by name, phone or email…' } = {}) {
    let selected = value;
    let timer = null;
    host.classList.add('crm-picker');
    host.innerHTML = `
      <div class="crm-picker-chosen" hidden></div>
      <div class="crm-picker-search">
        <input type="text" class="form-input" autocomplete="off" placeholder="${esc(placeholder)}" aria-label="Search contacts" />
        <ul class="crm-picker-list" hidden></ul>
      </div>`;
    const chosenEl = host.querySelector('.crm-picker-chosen');
    const searchEl = host.querySelector('.crm-picker-search');
    const input = host.querySelector('input');
    const list = host.querySelector('.crm-picker-list');

    function render() {
      if (selected) {
        chosenEl.hidden = false;
        searchEl.hidden = true;
        chosenEl.innerHTML = `${avatar(selected.name, 28)}<span class="crm-picker-name">${esc(selected.name || selected.phone || 'Contact')}</span>
          ${locked ? '' : '<button type="button" class="crm-link-btn" data-change>Change</button>'}`;
        chosenEl.querySelector('[data-change]')?.addEventListener('click', () => api.clear());
      } else {
        chosenEl.hidden = true;
        searchEl.hidden = false;
      }
    }

    function choose(contact) {
      selected = contact;
      list.hidden = true;
      input.value = '';
      render();
      if (onChange) onChange(selected);
    }

    async function search(term) {
      try {
        const res = await Api.getContacts({ search: term, page_size: 6 });
        const items = res.items || [];
        list.innerHTML = items.map((c, i) => `
            <li role="option" data-i="${i}" class="crm-picker-item">
              ${avatar(c.name, 28)}
              <span><strong>${esc(c.name || 'Unnamed')}</strong>
              <small>${esc([c.company_name, phoneLabel(c.phone), c.email].filter(Boolean).join(' · ') || 'No details yet')}</small></span>
            </li>`).join('')
          + (term ? `<li class="crm-picker-item crm-picker-new" data-new><span class="crm-picker-plus">+</span><span><strong>Add “${esc(term)}” as a new contact</strong></span></li>` : '');
        list.hidden = !list.innerHTML;
        list.querySelectorAll('[data-i]').forEach((li) => li.addEventListener('mousedown', (e) => { e.preventDefault(); choose(items[Number(li.dataset.i)]); }));
        list.querySelector('[data-new]')?.addEventListener('mousedown', async (e) => {
          e.preventDefault();
          try {
            const created = await Api.createContact({ name: term });
            Crm.emit('contacts-changed');
            choose(created);
          } catch (err) { toast(err.message, 'error'); }
        });
      } catch (err) {
        list.hidden = true;
      }
    }

    input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => search(input.value.trim()), 220); });
    input.addEventListener('focus', () => search(input.value.trim()));
    input.addEventListener('blur', () => setTimeout(() => { list.hidden = true; }, 120));

    const api = {
      get value() { return selected; },
      set(contact) { selected = contact; render(); },
      clear() { selected = null; render(); input.focus(); if (onChange) onChange(null); },
      focus() { input.focus(); },
    };
    render();
    return api;
  }

  // ---------------------------------------------------------------- nav badges (tasks overdue, chats needing a human)
  let lastHome = null;
  async function refreshBadges() {
    try {
      lastHome = await Api.getHomeSummary(dayEndISO());
    } catch (e) {
      return lastHome;
    }
    const tasksBadge = document.getElementById('badgeTasks');
    if (tasksBadge) {
      const t = lastHome.tasks || {};
      const n = t.overdue || 0;
      tasksBadge.textContent = n;
      tasksBadge.hidden = n === 0;
      tasksBadge.title = `${n} overdue task${n === 1 ? '' : 's'}`;
    }
    const inboxBadge = document.getElementById('badgeInbox');
    if (inboxBadge) {
      const n = lastHome.needs_human || 0;
      inboxBadge.textContent = n;
      inboxBadge.hidden = n === 0;
      inboxBadge.title = `${n} chat${n === 1 ? '' : 's'} waiting for a person`;
    }
    emit('home-updated', lastHome);
    return lastHome;
  }

  return {
    user, isAdmin, isRep, STAGES, PRIORITIES,
    esc, money, initials, avatar, isInternalPhone, phoneLabel, relTime, dayEndISO, dueLabel, localParts, toISO, quickDue,
    stagePill, priorityPill, scorePill, toast, busy,
    on, off, emit, goto, sectionId, sectionKey,
    team, fillTeamSelect, modal, drawer, confirm, closeDialogs, contactPicker, refreshBadges,
    get lastHome() { return lastHome; },
  };
})();

window.Crm = Crm;
