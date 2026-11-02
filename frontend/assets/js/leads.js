/**
 * Deals — the deal dialog and the list view of the Pipeline screen.
 *
 *   LeadsModule.openLeadDetail(id)      open the deal dialog (edit stage, owner, value, notes…)
 *   LeadsModule.createList(host, opts)  mount the table view into `host` (used by PipelineModule)
 *   LeadsModule.loadLeads()             refresh whatever Pipeline view is showing (kept for chat_simulator.js)
 *   LeadsModule.closeLeadDetail()       close the dialog
 *
 * Stages / priorities are the canonical ones from Crm.STAGES / Crm.PRIORITIES.
 */
const LeadsModule = (() => {
  const esc = (v) => Crm.esc(v);

  // Values written by older UI builds are shown as their canonical equivalent.
  const STAGE_ALIAS = { PROPOSAL_SENT: 'PROPOSAL', CLOSED_WON: 'WON', CLOSED_LOST: 'LOST' };
  const PRIORITY_ALIAS = { WARM: 'MEDIUM', COLD: 'LOW' };
  const normStage = (s) => STAGE_ALIAS[String(s || 'NEW').toUpperCase()] || String(s || 'NEW').toUpperCase();
  const normPriority = (p) => PRIORITY_ALIAS[String(p || 'MEDIUM').toUpperCase()] || String(p || 'MEDIUM').toUpperCase();

  /** Human-readable text for an API error, including FastAPI validation lists. */
  function errText(err) {
    const detail = err && err.data && err.data.detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => String(d.msg || d).replace(/^Value error, /, '')).join('. ');
    }
    return (err && err.message) || 'Something went wrong. Please try again.';
  }

  const options = (items, selected) =>
    items.map((i) => `<option value="${esc(i.key)}"${i.key === selected ? ' selected' : ''}>${esc(i.label)}</option>`).join('');

  // ======================================================================= deal dialog
  let dialog = null;

  function closeLeadDetail() {
    if (dialog) dialog.close();
    dialog = null;
  }

  async function openLeadDetail(id) {
    let lead;
    try {
      lead = await Api.getLead(id);
    } catch (err) {
      Crm.toast(`Could not open this deal. ${errText(err)}`, 'error');
      return;
    }
    closeLeadDetail();

    const stage = normStage(lead.status);
    const priority = normPriority(lead.priority);
    const contactName = lead.customer_name || Crm.phoneLabel(lead.customer_phone) || 'Unknown contact';
    const canTask = !!(window.TasksModule && typeof TasksModule.openForm === 'function');
    const hasAiNotes = !!(lead.score_reason || lead.requirements || lead.timeline);

    const body = `
      <div class="deal-sub">
        <span class="deal-contact">${Crm.avatar(contactName, 28)}
          <span><strong>${esc(contactName)}</strong>
          <small>${esc([Crm.phoneLabel(lead.customer_phone), lead.customer_email].filter(Boolean).join(' · '))}</small></span>
        </span>
        ${lead.customer_id ? '<button type="button" class="crm-link-btn" id="dealViewContact">View contact</button>' : ''}
        <span class="deal-pills">${Crm.scorePill(lead.score)} ${Crm.priorityPill(priority)}</span>
      </div>

      ${hasAiNotes ? `
        <div class="deal-ai">
          <div class="deal-ai-title">🤖 What the AI noticed</div>
          ${lead.score_reason ? `<p>${esc(lead.score_reason)}</p>` : ''}
          ${lead.requirements ? `<p><strong>Looking for:</strong> ${esc(lead.requirements)}</p>` : ''}
          ${lead.timeline ? `<p><strong>Timeline:</strong> ${esc(lead.timeline)}</p>` : ''}
        </div>` : ''}

      <form class="crm-form" id="dealEditForm" novalidate>
        <div class="crm-field-row">
          <div class="form-group">
            <label class="form-label" for="dealStage">Stage</label>
            <select id="dealStage" class="form-input">${options(Crm.STAGES, stage)}</select>
          </div>
          <div class="form-group">
            <label class="form-label" for="dealPriority">Priority</label>
            <select id="dealPriority" class="form-input">${options(Crm.PRIORITIES, priority)}</select>
          </div>
        </div>
        <p class="crm-hint deal-close-hint" id="dealCloseHint" hidden>Closing a deal stops the AI’s automatic follow-ups.</p>
        <div class="crm-field-row">
          <div class="form-group">
            <label class="form-label" for="dealValue">Estimated value ($)</label>
            <input id="dealValue" class="form-input" type="number" min="0" step="any" placeholder="e.g. 5000"
              value="${lead.budget !== null && lead.budget !== undefined ? esc(lead.budget) : ''}" />
          </div>
          <div class="form-group" data-admin-only>
            <label class="form-label" for="dealOwner">Owner</label>
            <select id="dealOwner" class="form-input"></select>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label" for="dealNotes">Internal notes</label>
          <textarea id="dealNotes" class="form-input" rows="3" maxlength="2000"
            placeholder="Anything your team should know about this deal…">${esc(lead.notes || '')}</textarea>
        </div>
        <div class="crm-error" id="dealError" role="alert" hidden></div>
      </form>`;

    const footer = `
      <div class="deal-foot-left">
        ${lead.conversation_id ? '<button type="button" class="btn-secondary" id="dealOpenChat">💬 Open chat</button>' : ''}
        ${canTask ? '<button type="button" class="btn-secondary" id="dealAddTask">+ Add task</button>' : ''}
        ${Crm.isAdmin ? '<button type="button" class="crm-link-btn deal-delete" id="dealDelete">Delete deal</button>' : ''}
      </div>
      <div class="deal-foot-right">
        <button type="button" class="btn-secondary" data-close>Cancel</button>
        <button type="submit" form="dealEditForm" class="btn-primary" id="dealSave">Save changes</button>
      </div>`;

    const m = Crm.modal({ title: 'Deal', body, footer, width: 640, onClose: () => { if (dialog === m) dialog = null; } });
    dialog = m;
    m.root.classList.add('deal-dialog');

    // The dialog heading is the deal name itself, editable in place.
    const heading = m.root.querySelector('.modal-header .modal-title');
    heading.innerHTML = `<input type="text" class="deal-title-input" id="dealTitle" maxlength="255"
      aria-label="Deal name" value="${esc(lead.title)}" />`;
    const titleInput = heading.querySelector('input');

    const q = (sel) => m.el.querySelector(sel);
    const errorBox = q('#dealError');
    const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };

    const stageSelect = q('#dealStage');
    const hint = q('#dealCloseHint');
    const syncHint = () => { hint.hidden = !(stageSelect.value === 'WON' || stageSelect.value === 'LOST'); };
    stageSelect.addEventListener('change', syncHint);
    syncHint();

    let ownerInitial = '';
    const ownerSelect = q('#dealOwner');
    if (Crm.isAdmin && ownerSelect) {
      Crm.fillTeamSelect(ownerSelect, { unassigned: 'Unassigned', value: lead.assigned_user_id || '' })
        .then(() => { ownerInitial = ownerSelect.value; });
    }

    q('#dealViewContact')?.addEventListener('click', () => {
      closeLeadDetail(); // the contact panel opens underneath dialogs, so close this one first
      if (window.ContactsModule && ContactsModule.openContact) ContactsModule.openContact(lead.customer_id);
      else Crm.goto('contacts', { id: lead.customer_id });
    });

    q('#dealOpenChat')?.addEventListener('click', () => {
      closeLeadDetail();
      Crm.goto('inbox');
      setTimeout(() => {
        if (window.ConversationsModule && ConversationsModule.selectConversation) ConversationsModule.selectConversation(lead.conversation_id);
      }, 200);
    });

    q('#dealAddTask')?.addEventListener('click', () => {
      TasksModule.openForm({
        contact: lead.customer_id ? { id: lead.customer_id, name: lead.customer_name } : undefined,
        lead: { id: lead.id, title: lead.title },
      });
    });

    q('#dealDelete')?.addEventListener('click', async () => {
      const yes = await Crm.confirm('This deal will be removed for everyone. This cannot be undone.', {
        title: 'Delete this deal?', confirmText: 'Delete deal', danger: true,
      });
      if (!yes) return;
      try {
        await Api.deleteLead(lead.id);
        Crm.toast('Deal deleted');
        closeLeadDetail();
        Crm.emit('deals-changed');
      } catch (err) {
        showError(errText(err));
      }
    });

    q('#dealEditForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      showError('');
      const changes = {};

      const title = titleInput.value.trim();
      if (!title) { showError('Give the deal a name.'); titleInput.focus(); return; }
      if (title !== lead.title) changes.title = title;

      if (stageSelect.value !== stage) changes.status = stageSelect.value;
      const prioritySelect = q('#dealPriority');
      if (prioritySelect.value !== priority) changes.priority = prioritySelect.value;

      const rawValue = q('#dealValue').value.trim();
      let budget = null;
      if (rawValue !== '') {
        budget = Number(rawValue);
        if (!Number.isFinite(budget) || budget < 0) { showError('Estimated value must be a positive number.'); return; }
      }
      const oldBudget = lead.budget === undefined ? null : lead.budget;
      if (budget !== oldBudget) changes.budget = budget;

      const notes = q('#dealNotes').value;
      if (notes.trim() !== (lead.notes || '').trim()) changes.notes = notes.trim() ? notes : null;

      if (Crm.isAdmin && ownerSelect && ownerSelect.value !== ownerInitial) {
        changes.assigned_user_id = ownerSelect.value || null;
      }

      if (!Object.keys(changes).length) { closeLeadDetail(); return; }

      const save = q('#dealSave');
      Crm.busy(save, true);
      try {
        await Api.updateLead(lead.id, changes);
        Crm.toast('Deal updated');
        closeLeadDetail();
        Crm.emit('deals-changed');
      } catch (err) {
        showError(errText(err));
        Crm.busy(save, false);
      }
    });
  }

  // ======================================================================= list view
  /**
   * Mount the table view into `host`.
   *   opts.getQuery()  → { search, assigned_user_id, unassigned } (shared toolbar filters)
   *   opts.onAdd()     → called by the empty-state button
   * Returns { load(), setStage(stage), page(n) }.
   */
  function createList(host, opts = {}) {
    let page = 1;
    const pageSize = 20;
    let stage = '';
    let priority = '';
    let pages = 1;
    let total = 0;
    let seq = 0;

    host.innerHTML = `
      <div class="pipeline-list-filters">
        <select class="tenant-filter" data-f="stage" aria-label="Filter by stage">
          <option value="">All stages</option>${options(Crm.STAGES, '')}
        </select>
        <select class="tenant-filter" data-f="priority" aria-label="Filter by priority">
          <option value="">All priorities</option>${options(Crm.PRIORITIES, '')}
        </select>
        <span class="crm-muted crm-small" data-f="summary"></span>
      </div>
      <div class="table-container">
        <table class="data-table pipeline-table">
          <thead>
            <tr>
              <th>Deal</th><th>Score</th><th>Priority</th><th>Stage</th><th>Value</th>
              <th data-admin-only>Owner</th><th>Updated</th>
            </tr>
          </thead>
          <tbody data-f="body"><tr><td colspan="7" class="db-empty">Loading deals…</td></tr></tbody>
        </table>
        <div class="pagination">
          <div class="pagination-info"><span data-f="indicator">Page 1 of 1</span></div>
          <div class="pagination-controls">
            <button type="button" class="page-icon-btn" data-go="first" aria-label="First page">«</button>
            <button type="button" class="page-icon-btn" data-go="prev" aria-label="Previous page">‹</button>
            <button type="button" class="page-icon-btn active" data-f="current">1</button>
            <button type="button" class="page-icon-btn" data-go="next" aria-label="Next page">›</button>
            <button type="button" class="page-icon-btn" data-go="last" aria-label="Last page">»</button>
          </div>
        </div>
      </div>`;

    const f = (name) => host.querySelector(`[data-f="${name}"]`);
    const go = (name) => host.querySelector(`[data-go="${name}"]`);

    function renderPagination() {
      f('indicator').textContent = `Page ${page} of ${pages}${total ? ` · ${total} deal${total === 1 ? '' : 's'}` : ''}`;
      f('current').textContent = page;
      go('first').disabled = go('prev').disabled = page <= 1;
      go('next').disabled = go('last').disabled = page >= pages;
      host.querySelector('.pagination').hidden = total <= pageSize;
    }

    function row(l) {
      const contact = l.customer_name || Crm.phoneLabel(l.customer_phone) || 'Unknown contact';
      const owner = l.assigned_user_name
        ? `<span class="pipeline-owner">${Crm.avatar(l.assigned_user_name, 24)}<span>${esc(l.assigned_user_name)}</span></span>`
        : '<span class="crm-muted">Unassigned</span>';
      return `
        <tr class="pipeline-row" data-id="${esc(l.id)}" tabindex="0" role="button" aria-label="Open deal ${esc(l.title)}">
          <td><div class="pipeline-row-title">${esc(l.title)}</div><div class="crm-muted crm-small">${esc(contact)}</div></td>
          <td>${Crm.scorePill(l.score) || '<span class="crm-muted">—</span>'}</td>
          <td>${Crm.priorityPill(normPriority(l.priority))}</td>
          <td>${Crm.stagePill(normStage(l.status))}</td>
          <td class="pipeline-row-value">${Crm.money(l.budget)}</td>
          <td data-admin-only>${owner}</td>
          <td class="crm-muted crm-small">${esc(Crm.relTime(l.updated_at))}</td>
        </tr>`;
    }

    function renderEmpty(query) {
      const filtered = !!(stage || priority || query.search || query.assigned_user_id || query.unassigned);
      if (filtered) {
        return `<tr><td colspan="7"><div class="crm-empty"><div class="crm-empty-icon">🔍</div>
          <h4>No deals match</h4><p>Try a different search or clear the filters.</p></div></td></tr>`;
      }
      return `<tr><td colspan="7"><div class="crm-empty"><div class="crm-empty-icon">📈</div>
        <h4>No deals yet</h4>
        <p>Deals appear here when the AI assistant captures a lead from a chat. You can also add one yourself.</p>
        <button type="button" class="btn-primary" data-add>+ Add deal</button></div></td></tr>`;
    }

    async function load() {
      const mine = ++seq;
      const query = (opts.getQuery && opts.getQuery()) || {};
      const body = f('body');
      try {
        const res = await Api.getLeads({
          page, page_size: pageSize, search: query.search || '', status: stage, priority,
          assigned_user_id: query.assigned_user_id, unassigned: query.unassigned,
        });
        if (mine !== seq) return;
        total = res.total || 0;
        pages = Math.max(1, res.pages || 1);
        if (page > pages) { page = pages; return load(); }
        body.innerHTML = res.items && res.items.length ? res.items.map(row).join('') : renderEmpty(query);
        f('summary').textContent = '';
        renderPagination();
        body.querySelector('[data-add]')?.addEventListener('click', () => opts.onAdd && opts.onAdd());
      } catch (err) {
        if (mine !== seq) return;
        body.innerHTML = `<tr><td colspan="7"><div class="crm-empty"><div class="crm-empty-icon">⚠️</div>
          <h4>Could not load deals</h4><p>${esc(errText(err))}</p>
          <button type="button" class="btn-secondary" data-retry>Try again</button></div></td></tr>`;
        body.querySelector('[data-retry]')?.addEventListener('click', load);
      }
    }

    host.addEventListener('change', (e) => {
      const which = e.target.getAttribute && e.target.getAttribute('data-f');
      if (which === 'stage') { stage = e.target.value; page = 1; load(); }
      if (which === 'priority') { priority = e.target.value; page = 1; load(); }
    });
    host.addEventListener('click', (e) => {
      const nav = e.target.closest('[data-go]');
      if (nav && !nav.disabled) {
        const to = nav.getAttribute('data-go');
        if (to === 'first') page = 1;
        else if (to === 'prev') page = Math.max(1, page - 1);
        else if (to === 'next') page = Math.min(pages, page + 1);
        else page = pages;
        load();
        return;
      }
      const tr = e.target.closest('tr.pipeline-row');
      if (tr) openLeadDetail(tr.getAttribute('data-id'));
    });
    host.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('tr.pipeline-row')) {
        e.preventDefault();
        openLeadDetail(e.target.getAttribute('data-id'));
      }
    });

    return {
      load,
      setStage(next) {
        stage = next || '';
        const sel = f('stage');
        if (sel) sel.value = stage;
        page = 1;
        return load();
      },
      resetPage() { page = 1; },
    };
  }

  /** Kept for chat_simulator.js: refresh whichever Pipeline view is showing. */
  function loadLeads() {
    if (window.PipelineModule && typeof PipelineModule.refresh === 'function') PipelineModule.refresh();
  }

  return { openLeadDetail, closeLeadDetail, createList, loadLeads, errText };
})();

window.LeadsModule = LeadsModule;
