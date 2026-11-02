/**
 * Pipeline screen — Kanban board (drag deals between stages) and a list view, plus the "Add deal" form.
 *
 *   PipelineModule.init()                  render once, reload data on every visit
 *   PipelineModule.refresh()               reload whichever view is showing
 *   PipelineModule.openDealForm({ contact?, onSaved? })   "Add deal" dialog (contact is prefilled + locked when given)
 *
 * Deep links: Crm.goto('pipeline', { lead: id }) opens that deal; { unassigned: 1 } shows deals without an owner.
 * Reloads itself on the 'deals-changed' / 'tasks-changed' / 'contacts-changed' events while visible.
 */
const PipelineModule = (() => {
  const esc = (v) => Crm.esc(v);
  const VIEW_KEY = 'pipeline_view';
  const UNASSIGNED = '__none__';

  let rendered = false;
  let view = 'board';
  let search = '';
  let owner = ''; // '' = everyone, '__none__' = unassigned, otherwise a user id
  let board = null; // last board payload
  let unassignedCount = 0;
  let list = null;
  let searchTimer = null;
  let boardSeq = 0;
  let selfEmit = false;
  let dragging = null;

  const $ = (id) => document.getElementById(id);
  const section = () => $('section-leads');
  const visible = () => { const s = section(); return !!s && !s.hidden; };

  function readStoredView() {
    try { return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'board'; } catch (e) { return 'board'; }
  }
  function storeView(v) {
    try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* storage unavailable: just don't remember */ }
  }

  /** The filters shared by both views, in API terms. */
  function query() {
    return {
      search: search.trim(),
      unassigned: owner === UNASSIGNED,
      assigned_user_id: owner && owner !== UNASSIGNED ? owner : undefined,
    };
  }

  // ======================================================================= shell
  function render() {
    const el = section();
    el.innerHTML = `
      <div class="tenant-page-header">
        <div class="tenant-page-title">
          <h2>Pipeline</h2>
          <p>Drag deals from one stage to the next as they progress.</p>
        </div>
        <div class="tenant-toolbar">
          <div class="tenant-search-box">
            <svg class="search-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
            </svg>
            <input type="text" id="pipelineSearch" placeholder="Search deals or contacts…" aria-label="Search deals or contacts" autocomplete="off" />
          </div>
          <select id="pipelineOwner" class="tenant-filter" data-admin-only aria-label="Show deals owned by"></select>
          <div class="crm-segmented" role="group" aria-label="Pipeline view">
            <button type="button" data-view="board">Board</button>
            <button type="button" data-view="list">List</button>
          </div>
          <button type="button" id="pipelineAdd" class="btn-primary">+ Add deal</button>
        </div>
      </div>

      <div id="pipelineNotice" class="pipeline-notice" data-admin-only hidden></div>
      <div id="pipelineBoard" class="pipeline-board" aria-label="Deals by stage"></div>
      <div id="pipelineList" hidden></div>`;

    if (Crm.isAdmin) {
      Crm.fillTeamSelect($('pipelineOwner'), { everyone: 'Everyone', unassigned: 'Unassigned', value: owner });
    }

    $('pipelineSearch').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { search = e.target.value; if (list) list.resetPage(); refresh(); }, 280);
    });
    $('pipelineOwner').addEventListener('change', (e) => setOwner(e.target.value));
    $('pipelineAdd').addEventListener('click', () => openDealForm());
    el.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
    $('pipelineNotice').addEventListener('click', (e) => { if (e.target.closest('[data-show-unassigned]')) setOwner(UNASSIGNED); });

    if (window.LeadsModule && LeadsModule.createList) {
      list = LeadsModule.createList($('pipelineList'), { getQuery: query, onAdd: () => openDealForm() });
    }
    bindBoardEvents();
    rendered = true;
  }

  function setOwner(value) {
    owner = value;
    const sel = $('pipelineOwner');
    if (sel) sel.value = value;
    if (list) list.resetPage();
    refresh();
  }

  function setView(next, { stage } = {}) {
    view = next === 'list' ? 'list' : 'board';
    storeView(view);
    syncView();
    if (view === 'list' && stage !== undefined && list) list.setStage(stage);
    else refresh();
  }

  function syncView() {
    const el = section();
    el.querySelectorAll('[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    $('pipelineBoard').hidden = view !== 'board';
    $('pipelineList').hidden = view !== 'list';
    renderNotice();
  }

  function renderNotice() {
    const box = $('pipelineNotice');
    if (!box) return;
    const n = unassignedCount;
    const show = Crm.isAdmin && n > 0 && owner !== UNASSIGNED;
    box.hidden = !show;
    if (show) {
      box.innerHTML = `<span>${n} deal${n === 1 ? ' has' : 's have'} no owner yet.</span>
        <button type="button" class="crm-link-btn" data-show-unassigned>Show them</button>`;
    }
  }

  // ======================================================================= board
  const stageLabel = (key) => (Crm.STAGES.find((s) => s.key === key) || { label: key }).label;

  function cardHtml(c) {
    const showPriority = c.priority === 'HOT' || c.priority === 'HIGH';
    let tasks = '';
    if (c.overdue_tasks > 0) tasks = `<span class="pipeline-tasks overdue" title="Overdue tasks">⚠ ${c.overdue_tasks} overdue</span>`;
    else if (c.open_tasks > 0) tasks = `<span class="pipeline-tasks">${c.open_tasks} task${c.open_tasks === 1 ? '' : 's'}</span>`;
    const ownerBadge = Crm.isAdmin && c.assigned_user_name
      ? `<span class="pipeline-card-owner" title="Owner: ${esc(c.assigned_user_name)}">${Crm.avatar(c.assigned_user_name, 24)}</span>`
      : (Crm.isAdmin ? '<span class="pipeline-card-owner pipeline-unowned" title="No owner yet">?</span>' : '');
    const moveOptions = Crm.STAGES.filter((s) => s.key !== c.status)
      .map((s) => `<option value="${esc(s.key)}">${esc(s.label)}</option>`).join('');
    return `
      <article class="pipeline-card" draggable="true" data-id="${esc(c.id)}" data-stage="${esc(c.status)}" tabindex="0"
        role="button" aria-label="Open deal ${esc(c.title)}">
        <div class="pipeline-card-title">${esc(c.title)}</div>
        <div class="pipeline-card-contact">${esc(c.customer_name || 'No contact')}</div>
        <div class="pipeline-card-meta">
          <span class="pipeline-card-value">${Crm.money(c.budget)}</span>
          ${Crm.scorePill(c.score)}
          ${showPriority ? Crm.priorityPill(c.priority) : ''}
        </div>
        <div class="pipeline-card-foot">
          ${tasks}
          <span class="pipeline-card-spacer"></span>
          ${ownerBadge}
        </div>
        <select class="pipeline-card-move" aria-label="Move this deal to another stage">
          <option value="">Move to…</option>${moveOptions}
        </select>
      </article>`;
  }

  function columnHtml(col) {
    const calm = col.stage === 'WON' ? ' pipeline-col--won' : col.stage === 'LOST' ? ' pipeline-col--lost' : '';
    const more = col.count - col.cards.length;
    const emptyText = col.stage === 'WON' ? 'Won deals land here' : col.stage === 'LOST' ? 'Lost deals land here' : 'Drop a deal here';
    return `
      <section class="pipeline-col${calm}" data-stage="${esc(col.stage)}" aria-label="${esc(col.label)}">
        <header class="pipeline-col-head">
          <span class="pipeline-col-title">${esc(col.label)}</span>
          <span class="crm-count" data-count>${col.count}</span>
          <span class="pipeline-col-value" data-value>${col.total_value ? Crm.money(col.total_value) : ''}</span>
        </header>
        <div class="pipeline-col-body" data-dropzone>
          ${col.cards.length ? col.cards.map(cardHtml).join('') : `<div class="pipeline-empty">${emptyText}</div>`}
          ${more > 0 ? `<button type="button" class="pipeline-more" data-more="${esc(col.stage)}">+ ${more} more — see the List view</button>` : ''}
        </div>
      </section>`;
  }

  /** Draw the board from `data`, keeping the user's scroll position (horizontal and per column). */
  function renderBoard(data) {
    board = data;
    unassignedCount = data.unassigned || 0;
    const el = $('pipelineBoard');
    const scrolls = {};
    el.querySelectorAll('.pipeline-col').forEach((c) => { scrolls[c.dataset.stage] = c.querySelector('.pipeline-col-body').scrollTop; });
    const left = el.scrollLeft;

    const total = data.columns.reduce((n, c) => n + c.count, 0);
    if (total === 0 && !query().search && !owner) {
      el.innerHTML = `<div class="crm-empty pipeline-board-empty"><div class="crm-empty-icon">📈</div>
        <h4>Your pipeline is empty</h4>
        <p>Deals appear here automatically when the AI assistant captures a lead from a chat. You can also add one yourself.</p>
        <button type="button" class="btn-primary" data-add-first>+ Add deal</button></div>`;
    } else {
      el.innerHTML = data.columns.map(columnHtml).join('');
      el.scrollLeft = left;
      el.querySelectorAll('.pipeline-col').forEach((c) => { c.querySelector('.pipeline-col-body').scrollTop = scrolls[c.dataset.stage] || 0; });
    }
    renderNotice();
  }

  async function loadBoard() {
    const mine = ++boardSeq;
    const el = $('pipelineBoard');
    if (!board) el.innerHTML = '<div class="db-empty pipeline-loading">Loading your pipeline…</div>';
    try {
      const q = query();
      const data = await Api.getLeadBoard({ search: q.search, assigned_user_id: q.assigned_user_id, unassigned: q.unassigned });
      if (mine !== boardSeq) return;
      renderBoard(data);
    } catch (err) {
      if (mine !== boardSeq) return;
      el.innerHTML = `<div class="crm-empty"><div class="crm-empty-icon">⚠️</div><h4>Could not load the pipeline</h4>
        <p>${esc(LeadsModule.errText(err))}</p><button type="button" class="btn-secondary" data-retry>Try again</button></div>`;
    }
  }

  /** Move a deal to another stage: update the screen at once, tell the server, undo on failure. */
  async function moveDeal(id, toStage) {
    if (!board) return;
    let fromCol = null;
    let card = null;
    board.columns.forEach((c) => {
      const found = c.cards.find((x) => x.id === id);
      if (found) { fromCol = c; card = found; }
    });
    const toCol = board.columns.find((c) => c.stage === toStage);
    if (!card || !toCol || fromCol === toCol) return;

    const snapshot = JSON.parse(JSON.stringify(board));
    fromCol.cards = fromCol.cards.filter((x) => x.id !== id);
    fromCol.count -= 1;
    fromCol.total_value = Math.max(0, (fromCol.total_value || 0) - (card.budget || 0));
    card.status = toStage;
    toCol.cards.unshift(card);
    toCol.count += 1;
    toCol.total_value = (toCol.total_value || 0) + (card.budget || 0);
    renderBoard(board);

    try {
      await Api.updateLead(id, { status: toStage });
      Crm.toast(`Moved to ${stageLabel(toStage)}`);
      selfEmit = true;
      try { Crm.emit('deals-changed'); } finally { selfEmit = false; }
    } catch (err) {
      renderBoard(snapshot);
      Crm.toast(`Could not move the deal. ${LeadsModule.errText(err)}`, 'error');
    }
  }

  function endDrag() {
    dragging = null;
    const el = $('pipelineBoard');
    if (!el) return;
    el.classList.remove('is-dragging');
    el.querySelectorAll('.is-dragging').forEach((n) => n.classList.remove('is-dragging'));
    el.querySelectorAll('.drop-target').forEach((n) => n.classList.remove('drop-target'));
  }

  function bindBoardEvents() {
    const el = $('pipelineBoard');

    el.addEventListener('click', (e) => {
      if (e.target.closest('.pipeline-card-move')) return;
      const more = e.target.closest('[data-more]');
      if (more) { setView('list', { stage: more.dataset.more }); return; }
      if (e.target.closest('[data-add-first]')) { openDealForm(); return; }
      if (e.target.closest('[data-retry]')) { loadBoard(); return; }
      const card = e.target.closest('.pipeline-card');
      if (card) LeadsModule.openLeadDetail(card.dataset.id);
    });

    el.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('pipeline-card')) {
        e.preventDefault();
        LeadsModule.openLeadDetail(e.target.dataset.id);
      }
    });

    el.addEventListener('change', (e) => {
      const select = e.target.closest('.pipeline-card-move');
      if (!select || !select.value) return;
      const card = select.closest('.pipeline-card');
      moveDeal(card.dataset.id, select.value);
    });

    el.addEventListener('dragstart', (e) => {
      const card = e.target.closest && e.target.closest('.pipeline-card');
      if (!card) return;
      dragging = { id: card.dataset.id, from: card.dataset.stage };
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', dragging.id); } catch (err) { /* some browsers restrict it */ }
      requestAnimationFrame(() => { card.classList.add('is-dragging'); el.classList.add('is-dragging'); });
    });
    el.addEventListener('dragend', endDrag);

    el.addEventListener('dragover', (e) => {
      if (!dragging) return;
      const col = e.target.closest('.pipeline-col');
      if (!col) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      el.querySelectorAll('.drop-target').forEach((n) => { if (n !== col) n.classList.remove('drop-target'); });
      col.classList.add('drop-target');
    });
    el.addEventListener('dragleave', (e) => {
      const col = e.target.closest && e.target.closest('.pipeline-col');
      if (col && !col.contains(e.relatedTarget)) col.classList.remove('drop-target');
    });
    el.addEventListener('drop', (e) => {
      if (!dragging) return;
      const col = e.target.closest('.pipeline-col');
      if (!col) return;
      e.preventDefault();
      const { id, from } = dragging;
      endDrag();
      if (col.dataset.stage !== from) moveDeal(id, col.dataset.stage);
    });
  }

  // ======================================================================= loading
  function refresh() {
    if (!rendered) return Promise.resolve();
    return view === 'list' && list ? list.load() : loadBoard();
  }

  async function init() {
    if (!rendered) {
      view = readStoredView();
      render();
      syncView();
    } else if (Crm.isAdmin) {
      // pick up newly added team members, keeping the current choice
      Crm.fillTeamSelect($('pipelineOwner'), { everyone: 'Everyone', unassigned: 'Unassigned', value: owner });
    }
    await refresh();
    // In list view nobody has asked for the board, so fetch it quietly to know how many deals have no owner.
    if (view === 'list' && Crm.isAdmin) {
      try { unassignedCount = (await Api.getLeadBoard({})).unassigned || 0; renderNotice(); } catch (e) { /* the notice is optional */ }
    }
  }

  // ======================================================================= Add deal
  function openDealForm({ contact = null, onSaved } = {}) {
    const stageOptions = Crm.STAGES.map((s) => `<option value="${s.key}"${s.key === 'NEW' ? ' selected' : ''}>${esc(s.label)}</option>`).join('');
    const priorityOptions = Crm.PRIORITIES.map((p) => `<option value="${p.key}"${p.key === 'MEDIUM' ? ' selected' : ''}>${esc(p.label)}</option>`).join('');

    const m = Crm.modal({
      title: 'Add deal',
      width: 560,
      body: `
        <form class="crm-form" id="dealForm" novalidate>
          <div class="form-group">
            <label class="form-label">Contact *</label>
            <div id="dealFormContact"></div>
          </div>
          <div class="form-group">
            <label class="form-label" for="dealFormTitle">Deal name *</label>
            <input id="dealFormTitle" class="form-input" type="text" maxlength="255" placeholder="e.g. 20 licences for Acme" autocomplete="off" />
          </div>
          <div class="crm-field-row">
            <div class="form-group">
              <label class="form-label" for="dealFormValue">Estimated value ($)</label>
              <input id="dealFormValue" class="form-input" type="number" min="0" step="any" placeholder="e.g. 5000" />
            </div>
            <div class="form-group">
              <label class="form-label" for="dealFormStage">Stage</label>
              <select id="dealFormStage" class="form-input">${stageOptions}</select>
            </div>
          </div>
          <div class="crm-field-row">
            <div class="form-group">
              <label class="form-label" for="dealFormPriority">Priority</label>
              <select id="dealFormPriority" class="form-input">${priorityOptions}</select>
            </div>
            <div class="form-group" data-admin-only>
              <label class="form-label" for="dealFormOwner">Owner</label>
              <select id="dealFormOwner" class="form-input"></select>
            </div>
          </div>
          <div class="crm-error" id="dealFormError" role="alert" hidden></div>
        </form>`,
      footer: `<button type="button" class="btn-secondary" data-close>Cancel</button>
               <button type="submit" form="dealForm" class="btn-primary" id="dealFormSave">Add deal</button>`,
    });

    const q = (sel) => m.el.querySelector(sel);
    const titleInput = q('#dealFormTitle');
    const errorBox = q('#dealFormError');
    const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };
    let suggestion = '';

    const picker = Crm.contactPicker(q('#dealFormContact'), {
      value: contact,
      locked: !!contact,
      onChange: (c) => {
        suggestion = c && c.name ? `Deal with ${c.name}` : '';
        titleInput.placeholder = suggestion || 'e.g. 20 licences for Acme';
        showError('');
      },
    });
    if (contact && contact.name) {
      suggestion = `Deal with ${contact.name}`;
      titleInput.placeholder = suggestion;
    }

    if (Crm.isAdmin) Crm.fillTeamSelect(q('#dealFormOwner'), { unassigned: 'Assign automatically', value: '' });
    setTimeout(() => (contact ? titleInput : picker).focus(), 80);

    q('#dealForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      showError('');
      const chosen = picker.value;
      if (!chosen) { showError('Choose the contact this deal is for.'); picker.focus(); return; }
      const title = titleInput.value.trim() || suggestion;
      if (!title) { showError('Give the deal a name.'); titleInput.focus(); return; }

      const payload = {
        customer_id: chosen.id,
        title,
        status: q('#dealFormStage').value,
        priority: q('#dealFormPriority').value,
      };
      const rawValue = q('#dealFormValue').value.trim();
      if (rawValue !== '') {
        const v = Number(rawValue);
        if (!Number.isFinite(v) || v < 0) { showError('Estimated value must be a positive number.'); return; }
        payload.budget = v;
      }
      if (Crm.isAdmin) {
        const ownerValue = q('#dealFormOwner').value;
        if (ownerValue) payload.assigned_user_id = ownerValue;
      }

      const save = q('#dealFormSave');
      Crm.busy(save, true, 'Adding…');
      try {
        const created = await Api.createLead(payload);
        Crm.toast('Deal added');
        m.close();
        Crm.emit('deals-changed');
        if (onSaved) onSaved(created);
      } catch (err) {
        showError(LeadsModule.errText(err));
        Crm.busy(save, false);
      }
    });
  }

  // ======================================================================= wiring
  ['deals-changed', 'tasks-changed', 'contacts-changed'].forEach((name) => {
    Crm.on(name, () => {
      if (selfEmit || !rendered || !visible()) return;
      refresh();
    });
  });

  Crm.on('route:pipeline', async (params = {}) => {
    if (!rendered) await init();
    if (params.unassigned) setOwner(UNASSIGNED);
    if (params.lead) LeadsModule.openLeadDetail(params.lead);
  });

  return { init, refresh, openDealForm };
})();

window.PipelineModule = PipelineModule;
