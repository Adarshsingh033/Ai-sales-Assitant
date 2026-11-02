/**
 * Contacts — one list of everyone the business talks to, and a detail drawer per person
 * (profile, deals, tasks, chats and a timeline of notes and events).
 *
 * Public API: ContactsModule.init() / openContact(id) / openForm({ contact?, onSaved? }) / refresh()
 * Cross-module calls (all guarded): LeadsModule.openLeadDetail(id), PipelineModule.openDealForm({ contact }),
 * TasksModule.openForm({ contact }), ConversationsModule.selectConversation(id).
 */
const ContactsModule = (() => {
  const PAGE_SIZE = 25;
  const esc = Crm.esc;

  // ---- list state
  let rendered = false;
  let page = 1;
  let total = 0;
  let pages = 1;
  let search = '';
  let items = [];
  let listSeq = 0;
  let listLoaded = false;
  let listDirty = false;
  let searchTimer = null;
  let listReloadTimer = null;

  // ---- drawer state
  let drawer = null;
  let drawerId = null;
  let drawerData = null;
  let drawerSeq = 0;
  let drawerRefreshTimer = null;

  // Set while this module emits its own change events, so it does not reload itself twice.
  let quiet = false;

  const CHANNELS = { WHATSAPP: 'WhatsApp', TELEGRAM: 'Telegram', WEB_SIMULATOR: 'Web chat', WEB_CHAT: 'Web chat' };
  const CHAT_STATUS = {
    ACTIVE_AI: 'AI is replying',
    WAITING_SALES: 'Waiting for a person',
    ESCALATED_HUMAN: 'With a person',
    RESOLVED: 'Resolved',
  };
  const KIND_ICON = { NOTE: '📝', STAGE: '➡️', ASSIGNED: '👤', TASK: '✅', DEAL: '💼', CONTACT: '🆕', AI: '🤖', CHAT: '💬' };

  // ------------------------------------------------------------------ helpers
  function errText(err) {
    const detail = err && err.data && err.data.detail;
    if (Array.isArray(detail)) {
      const msgs = detail.map((d) => String((d && d.msg) || d).replace(/^Value error,\s*/i, '')).filter(Boolean);
      if (msgs.length) return msgs.join(' ');
    }
    if (typeof detail === 'string' && detail) return detail;
    if (err && typeof err.message === 'string' && err.message) return err.message;
    return 'Something went wrong. Please try again.';
  }

  function displayName(c) {
    return (c && (c.name || Crm.phoneLabel(c.phone) || c.email)) || 'Unnamed contact';
  }

  function isVisible() {
    const s = document.getElementById('section-contacts');
    return !!s && !s.hidden;
  }

  function emitQuiet(name) {
    quiet = true;
    try { Crm.emit(name); } finally { quiet = false; }
  }

  function telHref(phone) {
    return 'tel:' + String(phone).replace(/[^\d+]/g, '');
  }

  const SEARCH_ICON = '<svg class="search-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" /></svg>';

  // ------------------------------------------------------------------ list
  function renderShell(section) {
    section.innerHTML = `
      <div class="tenant-page-header">
        <div class="tenant-page-title">
          <h2>Contacts</h2>
          <p>Everyone you talk to, in one place.</p>
        </div>
        <div class="tenant-toolbar">
          <div class="tenant-search-box contacts-search">
            ${SEARCH_ICON}
            <input type="text" id="contactsSearch" placeholder="Search by name, phone, email or company…" aria-label="Search contacts" autocomplete="off" />
          </div>
          <button type="button" class="btn-primary" id="contactsAddBtn">+ Add contact</button>
        </div>
      </div>

      <div class="table-container contacts-table-wrap">
        <table class="data-table contacts-table" id="contactsTable">
          <thead>
            <tr>
              <th>Name</th>
              <th>Phone / Email</th>
              <th class="contacts-num">Open deals</th>
              <th data-admin-only>Owner</th>
              <th>Last activity</th>
            </tr>
          </thead>
          <tbody id="contactsBody"></tbody>
        </table>
        <div id="contactsEmpty" hidden></div>
        <div class="pagination contacts-pagination" id="contactsPagination" hidden>
          <span id="contactsPageInfo"></span>
          <div class="contacts-pager">
            <button type="button" class="btn-secondary" id="contactsPrev">Previous</button>
            <button type="button" class="btn-secondary" id="contactsNext">Next</button>
          </div>
        </div>
      </div>`;

    const input = section.querySelector('#contactsSearch');
    input.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        const next = input.value.trim();
        if (next === search) return;
        search = next;
        page = 1;
        loadList();
      }, 300);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && input.value) {
        e.stopPropagation();
        input.value = '';
        clearTimeout(searchTimer);
        search = '';
        page = 1;
        loadList();
      }
    });
    section.querySelector('#contactsAddBtn').addEventListener('click', () => openForm({ onSaved: (c) => c && openContact(c.id) }));
    section.querySelector('#contactsPrev').addEventListener('click', () => { if (page > 1) { page--; loadList(); } });
    section.querySelector('#contactsNext').addEventListener('click', () => { if (page < pages) { page++; loadList(); } });

    const body = section.querySelector('#contactsBody');
    body.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      if (e.target.closest('[data-retry]')) { loadList(); return; }
      const row = e.target.closest('tr[data-id]');
      if (row) openContact(row.dataset.id);
    });
    body.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const row = e.target.closest('tr[data-id]');
      if (row && e.target === row) { e.preventDefault(); openContact(row.dataset.id); }
    });
    section.querySelector('#contactsEmpty').addEventListener('click', (e) => {
      if (e.target.closest('[data-add]')) openForm({ onSaved: (c) => c && openContact(c.id) });
      if (e.target.closest('[data-clear]')) {
        input.value = '';
        search = '';
        page = 1;
        loadList();
      }
    });
  }

  async function loadList() {
    const seq = ++listSeq;
    listDirty = false;
    const body = document.getElementById('contactsBody');
    if (!body) return;
    if (!listLoaded) {
      body.innerHTML = '<tr><td colspan="5" class="db-empty">Loading contacts…</td></tr>';
    }
    let res;
    try {
      res = await Api.getContacts({ page, page_size: PAGE_SIZE, search });
    } catch (err) {
      if (seq !== listSeq) return;
      showListError(err);
      return;
    }
    if (seq !== listSeq) return;
    items = res.items || [];
    total = res.total || 0;
    pages = res.pages || 1;
    if (page > pages && total > 0) {
      page = pages;
      loadList();
      return;
    }
    listLoaded = true;
    renderList();
  }

  function showListError(err) {
    const body = document.getElementById('contactsBody');
    document.getElementById('contactsTable').hidden = false;
    document.getElementById('contactsEmpty').hidden = true;
    document.getElementById('contactsPagination').hidden = true;
    body.innerHTML = `<tr><td colspan="5" class="db-empty contacts-error">Couldn’t load contacts: ${esc(errText(err))}
      <button type="button" class="crm-link-btn" data-retry>Try again</button></td></tr>`;
  }

  function renderList() {
    const table = document.getElementById('contactsTable');
    const body = document.getElementById('contactsBody');
    const empty = document.getElementById('contactsEmpty');
    const pager = document.getElementById('contactsPagination');

    if (!items.length) {
      table.hidden = true;
      pager.hidden = true;
      empty.hidden = false;
      empty.innerHTML = search
        ? `<div class="crm-empty">
             <div class="crm-empty-icon">🔍</div>
             <h4>No contacts match “${esc(search)}”</h4>
             <p>Check the spelling, or try part of a name, phone number or email.</p>
             <button type="button" class="btn-secondary" data-clear>Clear search</button>
           </div>`
        : `<div class="crm-empty">
             <div class="crm-empty-icon">👥</div>
             <h4>No contacts yet</h4>
             <p>Contacts appear automatically when customers chat with your assistant, or add one yourself.</p>
             <button type="button" class="btn-primary" data-add>+ Add contact</button>
           </div>`;
      return;
    }

    table.hidden = false;
    empty.hidden = true;
    body.innerHTML = items.map((c) => {
      const name = displayName(c);
      const deals = c.open_deals > 0 ? `<span class="contacts-deals-pill">${c.open_deals}</span>` : '<span class="crm-muted">—</span>';
      return `
        <tr class="contacts-row" data-id="${esc(c.id)}" tabindex="0" role="button" aria-label="Open ${esc(name)}">
          <td>
            <div class="contacts-cell-name">
              ${Crm.avatar(name, 36)}
              <div class="contacts-name-wrap">
                <div class="contacts-name-text">${esc(name)}${c.do_not_contact ? ' <span class="contacts-dnc" title="This person asked not to be contacted">Do not contact</span>' : ''}</div>
                <div class="contacts-company">${esc(c.company_name || '')}</div>
              </div>
            </div>
          </td>
          <td>
            <div class="contacts-phone">${esc(Crm.phoneLabel(c.phone) || '—')}</div>
            ${c.email ? `<div class="crm-muted crm-small">${esc(c.email)}</div>` : ''}
          </td>
          <td class="contacts-num">${deals}</td>
          <td data-admin-only>${c.owner_name ? esc(c.owner_name) : '<span class="crm-muted">—</span>'}</td>
          <td class="crm-muted" title="${c.last_activity_at ? esc(new Date(c.last_activity_at).toLocaleString()) : ''}">${esc(Crm.relTime(c.last_activity_at))}</td>
        </tr>`;
    }).join('');

    const from = (page - 1) * PAGE_SIZE + 1;
    const to = Math.min(total, from + items.length - 1);
    document.getElementById('contactsPageInfo').textContent = `Showing ${from}–${to} of ${total}`;
    document.getElementById('contactsPrev').disabled = page <= 1;
    document.getElementById('contactsNext').disabled = page >= pages;
    pager.hidden = false;
  }

  // ------------------------------------------------------------------ add / edit form
  let formCounter = 0;

  function openForm({ contact = null, onSaved } = {}) {
    const editing = !!contact;
    const fid = `contactForm${++formCounter}`;
    const m = Crm.modal({
      title: editing ? 'Edit contact' : 'Add contact',
      width: 500,
      body: `
        <form class="crm-form" id="${fid}" novalidate>
          <div class="crm-error" data-error hidden></div>
          <div class="form-group">
            <label class="form-label" for="${fid}Name">Name *</label>
            <input type="text" class="form-input" id="${fid}Name" name="name" maxlength="255" placeholder="e.g. Jordan Smith" autocomplete="off" autofocus />
          </div>
          <div class="crm-field-row">
            <div class="form-group">
              <label class="form-label" for="${fid}Phone">Phone / WhatsApp</label>
              <input type="text" class="form-input" id="${fid}Phone" name="phone" maxlength="50" placeholder="+1 555 123 4567" autocomplete="off" />
            </div>
            <div class="form-group">
              <label class="form-label" for="${fid}Email">Email</label>
              <input type="email" class="form-input" id="${fid}Email" name="email" maxlength="255" placeholder="name@company.com" autocomplete="off" />
            </div>
          </div>
          <div class="form-group">
            <label class="form-label" for="${fid}Company">Company</label>
            <input type="text" class="form-input" id="${fid}Company" name="company_name" maxlength="255" placeholder="Optional" autocomplete="off" />
          </div>
          ${Crm.isAdmin ? `
          <div class="form-group">
            <label class="form-label" for="${fid}Owner">Owner</label>
            <select class="form-input" id="${fid}Owner" name="owner_user_id"></select>
            <span class="crm-hint">The team member responsible for this contact.</span>
          </div>` : ''}
        </form>`,
      footer: `
        <button type="button" class="btn-secondary" data-close>Cancel</button>
        <button type="submit" form="${fid}" class="btn-primary" data-save>${editing ? 'Save changes' : 'Add contact'}</button>`,
    });

    const form = m.body.querySelector('form');
    const errorBox = m.body.querySelector('[data-error]');
    const saveBtn = m.footer.querySelector('[data-save]');
    const f = (n) => form.elements[n];

    if (editing) {
      f('name').value = contact.name || '';
      f('phone').value = contact.phone || '';
      f('email').value = contact.email || '';
      f('company_name').value = contact.company_name || '';
    }
    if (Crm.isAdmin) {
      Crm.fillTeamSelect(f('owner_user_id'), { unassigned: 'No owner', value: editing ? contact.owner_user_id || '' : '' });
    }

    const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };
    form.addEventListener('input', () => showError(''));

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      showError('');
      const name = f('name').value.trim();
      const phone = f('phone').value.trim();
      const email = f('email').value.trim();
      const company = f('company_name').value.trim();
      const owner = Crm.isAdmin ? f('owner_user_id').value : '';

      if (!name) { showError('Please enter a name.'); f('name').focus(); return; }
      if (email && !/^\S+@\S+\.\S+$/.test(email)) { showError('That email address doesn’t look right.'); f('email').focus(); return; }

      let payload;
      if (editing) {
        payload = {};
        if (name !== (contact.name || '')) payload.name = name;
        if (phone !== (contact.phone || '')) payload.phone = phone || null;
        if (email.toLowerCase() !== (contact.email || '').toLowerCase()) payload.email = email || null;
        if (company !== (contact.company_name || '')) payload.company_name = company || null;
        if (Crm.isAdmin && owner !== (contact.owner_user_id || '')) payload.owner_user_id = owner || null;
        if (!Object.keys(payload).length) { m.close(); return; }
      } else {
        payload = { name };
        if (phone) payload.phone = phone;
        if (email) payload.email = email;
        if (company) payload.company_name = company;
        if (owner) payload.owner_user_id = owner;
      }

      Crm.busy(saveBtn, true);
      try {
        const saved = editing ? await Api.updateContact(contact.id, payload) : await Api.createContact(payload);
        Crm.toast(editing ? 'Contact updated' : 'Contact added');
        m.close();
        Crm.emit('contacts-changed');
        if (onSaved) onSaved(saved);
      } catch (err) {
        showError(errText(err));
        Crm.busy(saveBtn, false);
      }
    });
    return m;
  }

  // ------------------------------------------------------------------ detail drawer
  function openContact(id) {
    if (!id) return;
    if (drawer && drawerId === id) {
      loadDrawer();
      return;
    }
    const previous = drawer;
    drawer = null; // so the previous drawer's close handler is ignored
    if (previous) previous.close();

    let handle;
    handle = Crm.drawer({ width: 580, onClose: () => onDrawerClosed(handle) });
    drawer = handle;
    drawerId = id;
    drawerData = null;
    handle.header.innerHTML = '<div class="contacts-loading">Loading…</div>';
    handle.body.innerHTML = '';
    handle.root.addEventListener('click', onDrawerClick);
    handle.root.addEventListener('keydown', onDrawerKeydown);
    loadDrawer();
  }

  function closeDrawer() {
    if (drawer) drawer.close();
  }

  function onDrawerClosed(handle) {
    if (drawer !== handle) return;
    drawer = null;
    drawerId = null;
    drawerData = null;
    clearTimeout(drawerRefreshTimer);
    if (listDirty && isVisible()) loadList();
  }

  async function loadDrawer() {
    const handle = drawer;
    const id = drawerId;
    if (!handle) return;
    const seq = ++drawerSeq;
    let data;
    try {
      data = await Api.getContact(id);
    } catch (err) {
      if (handle !== drawer || seq !== drawerSeq) return;
      handle.header.innerHTML = '<div class="contacts-loading">Contact</div>';
      handle.body.innerHTML = `<div class="crm-error">${esc(err && err.status === 404 ? 'This contact could not be found. It may have been removed, or it isn’t assigned to you.' : errText(err))}</div>
        <button type="button" class="btn-secondary" data-act="retry">Try again</button>`;
      return;
    }
    if (handle !== drawer || seq !== drawerSeq) return;
    drawerData = data;

    // keep what the user was doing across the re-render
    const prevScroll = handle.body.scrollTop;
    const prevInput = handle.body.querySelector('#contactsNoteInput');
    const draft = prevInput ? prevInput.value : '';
    const hadFocus = !!prevInput && document.activeElement === prevInput;

    renderDrawer(handle, data);

    handle.body.scrollTop = prevScroll;
    const input = handle.body.querySelector('#contactsNoteInput');
    if (input && draft) {
      input.value = draft;
      if (hadFocus) { input.focus(); input.setSelectionRange(draft.length, draft.length); }
    }
  }

  function scheduleDrawerRefresh() {
    clearTimeout(drawerRefreshTimer);
    drawerRefreshTimer = setTimeout(() => { if (drawer) loadDrawer(); }, 250);
  }

  function renderDrawer(handle, data) {
    const c = data.contact;
    const name = displayName(c);
    const chats = data.chats || [];
    const ownerLine = c.owner_name
      ? `Owner: <strong>${esc(c.owner_name)}</strong>`
      : (Crm.isAdmin ? '<span class="crm-muted">No owner yet</span>' : '');

    handle.header.innerHTML = `
      <div class="contacts-head">
        ${Crm.avatar(name, 56)}
        <div class="contacts-head-main">
          <h3 class="contacts-name">${esc(name)}</h3>
          ${c.company_name ? `<div class="contacts-sub">${esc(c.company_name)}</div>` : ''}
          <div class="contacts-lines">
            ${c.phone ? (Crm.isInternalPhone(c.phone)
              ? '<span class="contacts-line">💬 Telegram chat</span>'
              : `<a href="${esc(telHref(c.phone))}" class="contacts-line">📞 ${esc(c.phone)}</a>`) : ''}
            ${c.email ? `<a href="mailto:${esc(c.email)}" class="contacts-line">✉️ ${esc(c.email)}</a>` : ''}
            ${!c.phone && !c.email ? '<span class="crm-muted crm-small">No phone or email yet</span>' : ''}
          </div>
          <div class="contacts-owner">${ownerLine}${c.do_not_contact ? ' <span class="contacts-dnc" title="This person asked not to be contacted">Do not contact</span>' : ''}</div>
        </div>
        <button type="button" class="btn-secondary contacts-edit" data-act="edit">Edit</button>
      </div>
      <div class="contacts-actions">
        <button type="button" class="btn-secondary" data-act="note">+ Note</button>
        <button type="button" class="btn-secondary" data-act="task">+ Task</button>
        <button type="button" class="btn-secondary" data-act="deal">+ Deal</button>
        ${chats.length ? '<button type="button" class="btn-secondary" data-act="chat">💬 Open chat</button>' : ''}
      </div>`;

    handle.body.innerHTML = [dealsSection(data.deals || []), tasksSection(data.tasks || []), chatsSection(chats), timelineSection(data.timeline || [])].join('');
  }

  function section(title, count, inner, extra = '') {
    return `
      <section class="contacts-section">
        <h4 class="contacts-section-title">${esc(title)}${count !== null ? ` <span class="crm-count">${count}</span>` : ''}</h4>
        ${extra}${inner}
      </section>`;
  }

  function dealsSection(deals) {
    const inner = deals.length
      ? `<div class="contacts-list">${deals.map((d) => `
          <button type="button" class="contacts-deal" data-deal-id="${esc(d.id)}">
            <div class="contacts-deal-main">
              <div class="contacts-deal-title">${esc(d.title)}</div>
              <div class="contacts-deal-meta">${d.assigned_user_name ? esc(d.assigned_user_name) : 'Unassigned'} · ${Crm.priorityPill(d.priority)}</div>
            </div>
            <div class="contacts-deal-side">${Crm.stagePill(d.status)}<span class="contacts-deal-value">${esc(Crm.money(d.budget))}</span></div>
          </button>`).join('')}</div>`
      : '<p class="contacts-empty-line">No deals yet. Add one when this person is interested in buying.</p>';
    return section('Deals', deals.length, inner);
  }

  function tasksSection(tasks) {
    const open = tasks.filter((t) => t.status !== 'DONE');
    const done = tasks.filter((t) => t.status === 'DONE');
    const row = (t) => {
      const isDone = t.status === 'DONE';
      const due = Crm.dueLabel(t.due_at, isDone);
      return `
        <div class="contacts-task${isDone ? ' is-done' : ''}">
          ${isDone
            ? '<span class="contacts-check done" aria-hidden="true">✓</span>'
            : `<button type="button" class="contacts-check" data-complete="${esc(t.id)}" aria-label="Mark “${esc(t.title)}” as done" title="Mark as done"></button>`}
          <div class="contacts-task-main">
            <div class="contacts-task-title">${esc(t.title)}</div>
            <div class="contacts-task-meta"><span class="crm-due ${esc(due.state)}">${esc(due.text)}</span>${t.lead_title ? ` · <span class="crm-muted">${esc(t.lead_title)}</span>` : ''}</div>
          </div>
          ${isDone ? `<button type="button" class="crm-link-btn" data-reopen="${esc(t.id)}">Reopen</button>` : ''}
        </div>`;
    };
    let inner = '';
    if (!open.length && !done.length) {
      inner = '<p class="contacts-empty-line">No tasks. Add a reminder so you don’t forget to follow up.</p>';
    } else {
      inner = `<div class="contacts-list">${open.length ? open.map(row).join('') : '<p class="contacts-empty-line">Nothing left to do for this contact. 🎉</p>'}${done.map(row).join('')}</div>`;
    }
    return section('Tasks', open.length, inner);
  }

  function chatsSection(chats) {
    const inner = chats.length
      ? `<div class="contacts-list">${chats.map((ch) => `
          <button type="button" class="contacts-chat" data-chat-id="${esc(ch.id)}">
            <div class="contacts-chat-top">
              <span class="channel-badge ${esc(String(ch.channel || '').toLowerCase())}">${esc(CHANNELS[ch.channel] || ch.channel)}</span>
              <span class="contacts-chat-status">${esc(CHAT_STATUS[ch.status] || ch.status)}</span>
              <span class="crm-muted crm-small contacts-chat-time">${esc(Crm.relTime(ch.last_activity_at))}</span>
            </div>
            <div class="contacts-chat-preview">${esc(ch.last_message_preview || 'No messages yet')}</div>
          </button>`).join('')}</div>`
      : '<p class="contacts-empty-line">No chats with this contact yet.</p>';
    return section('Chats', chats.length, inner);
  }

  function timelineSection(timeline) {
    const composer = `
      <div class="contacts-composer">
        <textarea id="contactsNoteInput" rows="3" maxlength="2000" placeholder="Write a note about this contact…" aria-label="Write a note"></textarea>
        <div class="contacts-composer-bar">
          <span class="crm-hint">Ctrl + Enter to save</span>
          <button type="button" class="btn-primary" id="contactsNoteSave" data-act="save-note">Save note</button>
        </div>
      </div>`;
    const list = timeline.length
      ? `<ul class="contacts-timeline">${timeline.map((a) => `
          <li class="contacts-tl-item kind-${esc(String(a.kind).toLowerCase())}">
            <span class="contacts-tl-icon" aria-hidden="true">${KIND_ICON[a.kind] || '•'}</span>
            <div class="contacts-tl-main">
              <div class="contacts-tl-text">${esc(a.text)}</div>
              <div class="contacts-tl-meta">${a.actor_name ? `${esc(a.actor_name)} · ` : ''}<span title="${esc(new Date(a.created_at).toLocaleString())}">${esc(Crm.relTime(a.created_at))}</span></div>
            </div>
            ${a.can_delete ? `<button type="button" class="crm-icon-btn danger" data-delete-note="${esc(a.id)}" aria-label="Delete note" title="Delete note">🗑</button>` : ''}
          </li>`).join('')}</ul>`
      : '<p class="contacts-empty-line">Nothing here yet. Notes and everything that happens with this contact will show up here.</p>';
    return section('Timeline', null, list, composer);
  }

  // ------------------------------------------------------------------ drawer actions
  function contactForPickers() {
    const c = drawerData && drawerData.contact;
    return c ? { id: c.id, name: c.name, phone: c.phone, email: c.email, company_name: c.company_name } : null;
  }

  function onDrawerKeydown(e) {
    if (e.target && e.target.id === 'contactsNoteInput' && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      saveNote();
    }
  }

  function onDrawerClick(e) {
    const t = e.target;
    const act = t.closest('[data-act]');
    if (act) {
      switch (act.dataset.act) {
        case 'edit':
          if (drawerData) openForm({ contact: drawerData.contact });
          break;
        case 'note': {
          const ta = document.getElementById('contactsNoteInput');
          if (ta) { ta.scrollIntoView({ behavior: 'smooth', block: 'center' }); ta.focus(); }
          break;
        }
        case 'task':
          if (typeof TasksModule !== 'undefined' && TasksModule.openForm) TasksModule.openForm({ contact: contactForPickers() });
          else Crm.toast('Tasks are not available right now.', 'error');
          break;
        case 'deal':
          if (typeof PipelineModule !== 'undefined' && PipelineModule.openDealForm) PipelineModule.openDealForm({ contact: contactForPickers() });
          else Crm.toast('Deals are not available right now.', 'error');
          break;
        case 'chat': {
          const chats = (drawerData && drawerData.chats) || [];
          if (chats.length) openChat(chats[0].id);
          break;
        }
        case 'save-note':
          saveNote();
          break;
        case 'retry':
          loadDrawer();
          break;
        default:
      }
      return;
    }

    const deal = t.closest('[data-deal-id]');
    if (deal) {
      if (typeof LeadsModule !== 'undefined' && LeadsModule.openLeadDetail) LeadsModule.openLeadDetail(deal.dataset.dealId);
      return;
    }
    const chat = t.closest('[data-chat-id]');
    if (chat) { openChat(chat.dataset.chatId); return; }

    const complete = t.closest('[data-complete]');
    if (complete) { completeTask(complete); return; }
    const reopen = t.closest('[data-reopen]');
    if (reopen) { reopenTask(reopen); return; }
    const del = t.closest('[data-delete-note]');
    if (del) deleteNote(del.dataset.deleteNote);
  }

  function openChat(chatId) {
    closeDrawer();
    Crm.goto('inbox');
    setTimeout(() => {
      if (typeof ConversationsModule !== 'undefined' && ConversationsModule.selectConversation) ConversationsModule.selectConversation(chatId);
    }, 200);
  }

  async function completeTask(btn) {
    const row = btn.closest('.contacts-task');
    btn.disabled = true;
    if (row) row.classList.add('is-done');
    try {
      await Api.completeTask(btn.dataset.complete);
    } catch (err) {
      if (row) row.classList.remove('is-done');
      btn.disabled = false;
      Crm.toast(errText(err), 'error');
      return;
    }
    Crm.toast('Task done');
    listDirty = true;
    emitQuiet('tasks-changed');
    Crm.refreshBadges();
    loadDrawer();
  }

  async function reopenTask(btn) {
    btn.disabled = true;
    try {
      await Api.reopenTask(btn.dataset.reopen);
    } catch (err) {
      btn.disabled = false;
      Crm.toast(errText(err), 'error');
      return;
    }
    listDirty = true;
    emitQuiet('tasks-changed');
    Crm.refreshBadges();
    loadDrawer();
  }

  async function saveNote() {
    const ta = document.getElementById('contactsNoteInput');
    const btn = document.getElementById('contactsNoteSave');
    if (!ta || !drawerId) return;
    const text = ta.value.trim();
    if (!text) { ta.focus(); return; }
    Crm.busy(btn, true);
    try {
      await Api.addContactNote(drawerId, text);
    } catch (err) {
      Crm.busy(btn, false);
      Crm.toast(errText(err), 'error');
      return;
    }
    ta.value = '';
    Crm.toast('Note saved');
    listDirty = true;
    await loadDrawer();
  }

  async function deleteNote(noteId) {
    const ok = await Crm.confirm('This note will be removed from the timeline. This can’t be undone.', {
      title: 'Delete this note?',
      confirmText: 'Delete note',
      danger: true,
    });
    if (!ok || !drawerId) return;
    try {
      await Api.deleteContactNote(drawerId, noteId);
    } catch (err) {
      Crm.toast(errText(err), 'error');
      return;
    }
    Crm.toast('Note deleted');
    listDirty = true;
    loadDrawer();
  }

  // ------------------------------------------------------------------ wiring
  function scheduleListReload() {
    clearTimeout(listReloadTimer);
    listReloadTimer = setTimeout(loadList, 250);
  }

  function onExternalChange() {
    if (quiet) return;
    if (rendered && isVisible()) scheduleListReload();
    else listDirty = true;
    if (drawer) scheduleDrawerRefresh();
  }

  ['contacts-changed', 'deals-changed', 'tasks-changed'].forEach((name) => Crm.on(name, onExternalChange));
  Crm.on('route:contacts', (p) => { if (p && p.id) openContact(p.id); });

  function init() {
    const section = document.getElementById('section-contacts');
    if (!section) return;
    if (!rendered) {
      renderShell(section);
      rendered = true;
    }
    loadList();
  }

  return {
    init,
    openContact,
    openForm,
    refresh() {
      if (rendered) loadList();
      if (drawer) loadDrawer();
    },
  };
})();

window.ContactsModule = ContactsModule;
