/**
 * Knowledge — ask questions and get short, source-backed answers; manage the documents behind them.
 *
 * Tabs
 *   Ask         everyone: a chat that answers ONLY from the company's documents and product catalog, with sources
 *   Documents   admins: upload / retry / delete files.  Sales reps: a read-only list of what the assistant knows
 *   Unanswered  admins: questions the team asked that nothing could answer (so they know what to add)
 *
 * The section is rendered here once (init() is safe to call on every visit), so event handlers never pile up.
 * Public: KnowledgeModule.init(), .refresh(); deep link: Crm.goto('knowledge', { tab: 'ask' | 'docs' | 'gaps' }).
 */
const KnowledgeModule = (() => {
  const MAX_BYTES = 15 * 1024 * 1024;
  const ALLOWED = ['pdf', 'docx', 'txt', 'md', 'csv'];
  const TYPE_LABEL = { pdf: 'PDF', docx: 'Word', txt: 'Text', md: 'Markdown', csv: 'CSV' };
  const MAX_HISTORY = 8;

  const isAdmin = Crm.isAdmin;
  let rendered = false;
  let tab = 'ask';
  let history = []; // the chat so far, [{ role: 'user' | 'assistant', text }]; lives only in this page
  let busy = false;
  let lastQuestion = '';
  let library = null; // { documents, product_count, product_names }
  let docs = []; // normalised: { id, name, type, status, size, created, version, error }
  let gaps = { items: [], open_count: 0 };
  let pollTimer = null;

  const $ = (id) => document.getElementById(id);
  const esc = Crm.esc;

  // ------------------------------------------------------------------ small helpers
  function formatBytes(bytes) {
    if (!bytes) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return `${parseFloat((bytes / Math.pow(1024, i)).toFixed(i ? 1 : 0))} ${units[i]}`;
  }

  function formatDate(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  /** FastAPI validation errors arrive as a list; make any error readable. */
  function errText(err) {
    const detail = err && err.data && err.data.detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg || d).join('; ');
    return (err && err.message) || 'Something went wrong. Please try again.';
  }

  function stripCitations(text) {
    return String(text || '').replace(/\s*\[\d{1,2}\]/g, '').trim();
  }

  // ------------------------------------------------------------------ page skeleton
  function render(el) {
    el.innerHTML = `
      <div class="tenant-page-header">
        <div class="tenant-page-title">
          <h2>Knowledge Base</h2>
          <p>Ask a question and get a short answer backed by your own documents, never a guess.</p>
        </div>
      </div>
      <div class="crm-tabs" id="kbTabs" role="tablist" aria-label="Knowledge sections">
        <button type="button" role="tab" class="crm-tab active" data-tab="ask">Ask</button>
        <button type="button" role="tab" class="crm-tab" data-tab="docs">${isAdmin ? 'Documents' : 'What it knows'} <span class="crm-count" id="kbDocCount" hidden></span></button>
        ${isAdmin ? '<button type="button" role="tab" class="crm-tab" data-tab="gaps">Unanswered <span class="crm-count danger" id="kbGapCount" hidden></span></button>' : ''}
      </div>

      <div id="kbPaneAsk" class="kb-pane">
        <div class="kb-chat crm-card">
          <div class="kb-chat-head">
            <div>
              <strong>Ask your knowledge base</strong>
              <span>Answers come only from your documents and product catalog.</span>
            </div>
            <button type="button" class="btn-secondary" id="kbNewChat" hidden>New chat</button>
          </div>
          <div class="kb-messages" id="kbMessages" aria-live="polite"></div>
          <form class="kb-composer" id="kbForm">
            <textarea id="kbInput" rows="1" maxlength="500" placeholder="Ask a question, e.g. what is our refund policy?" aria-label="Your question"></textarea>
            <button type="submit" class="btn-primary" id="kbSend">Ask</button>
          </form>
          <div class="kb-hint">Enter to send · Shift+Enter for a new line · Questions it can't answer are shared with your admins so they can add the missing information.</div>
        </div>
      </div>

      <div id="kbPaneDocs" class="kb-pane" hidden></div>
      ${isAdmin ? '<div id="kbPaneGaps" class="kb-pane" hidden></div>' : ''}`;

    $('kbTabs').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-tab]');
      if (btn) setTab(btn.dataset.tab);
    });
    $('kbForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = $('kbInput');
      const text = input.value;
      input.value = '';
      autosize();
      ask(text);
    });
    const input = $('kbInput');
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        $('kbForm').requestSubmit();
      }
    });
    input.addEventListener('input', autosize);
    $('kbNewChat').addEventListener('click', newChat);
    $('kbMessages').addEventListener('click', onMessageClick);
    showWelcome();
  }

  function autosize() {
    const input = $('kbInput');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 130) + 'px';
    input.style.overflowY = input.scrollHeight > 130 ? 'auto' : 'hidden';
  }

  function setTab(next) {
    if (next === 'gaps' && !isAdmin) next = 'ask';
    if (!['ask', 'docs', 'gaps'].includes(next)) next = 'ask';
    tab = next;
    document.querySelectorAll('#kbTabs .crm-tab').forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', String(on));
    });
    $('kbPaneAsk').hidden = tab !== 'ask';
    $('kbPaneDocs').hidden = tab !== 'docs';
    if ($('kbPaneGaps')) $('kbPaneGaps').hidden = tab !== 'gaps';
    if (tab === 'docs') loadDocs();
    if (tab === 'gaps') loadGaps();
    if (tab === 'ask') setTimeout(() => $('kbInput')?.focus(), 50);
  }

  // ------------------------------------------------------------------ Ask: welcome / empty states
  async function loadLibrary() {
    try {
      library = await Api.getKnowledgeLibrary();
      if (!isAdmin) setCount('kbDocCount', library.documents.length);
      if (!history.length && !$('kbMessages').querySelector('.kb-msg')) showWelcome();
    } catch (err) {
      library = null;
    }
  }

  function setCount(id, n) {
    const el = $(id);
    if (!el) return;
    el.textContent = n;
    el.hidden = !n;
  }

  function showWelcome() {
    const host = $('kbMessages');
    if (!host) return;
    const empty = library && library.documents.length === 0 && library.product_count === 0;
    if (empty) {
      host.innerHTML = `
        <div class="kb-welcome">
          <div class="kb-welcome-icon">📚</div>
          <h3>Nothing to search yet</h3>
          <p>${isAdmin
            ? 'Upload your company documents (policies, FAQs, price lists, playbooks) and your team can start asking questions.'
            : 'No documents have been added yet. Ask your admin to upload your company documents.'}</p>
          ${isAdmin ? '<button type="button" class="btn-primary" data-go-docs>Upload a document</button>' : ''}
        </div>`;
      return;
    }
    const examples = [];
    if (library && library.product_names.length) {
      examples.push(`What is the price of ${library.product_names[0]} and is it in stock?`);
    }
    host.innerHTML = `
      <div class="kb-welcome">
        <div class="kb-welcome-icon">💡</div>
        <h3>Ask anything about your company</h3>
        <p>I answer only from your uploaded documents and product catalog, and I show where every answer came from. If it isn't written down, I'll say so instead of guessing.</p>
        ${examples.length ? `<div class="crm-chips kb-examples">${examples.map((q) => `<button type="button" class="crm-chip" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>` : ''}
      </div>`;
  }

  function newChat() {
    history = [];
    lastQuestion = '';
    $('kbNewChat').hidden = true;
    showWelcome();
    $('kbInput').focus();
  }

  // ------------------------------------------------------------------ Ask: conversation
  function scrollToEnd() {
    const host = $('kbMessages');
    host.scrollTop = host.scrollHeight;
  }

  function startThread() {
    const host = $('kbMessages');
    if (host.querySelector('.kb-welcome')) host.innerHTML = '';
    $('kbNewChat').hidden = false;
    return host;
  }

  function addUser(text) {
    const host = startThread();
    host.insertAdjacentHTML('beforeend', `<div class="kb-msg user"><div class="kb-bubble">${esc(text)}</div></div>`);
    scrollToEnd();
  }

  function addThinking() {
    const host = $('kbMessages');
    host.insertAdjacentHTML('beforeend', `
      <div class="kb-msg bot kb-thinking" role="status">
        <div class="kb-bot-body"><span class="kb-dots" aria-hidden="true"><i></i><i></i><i></i></span> Searching your documents…</div>
      </div>`);
    scrollToEnd();
    return host.lastElementChild;
  }

  function citeHtml(text) {
    return esc(text).replace(/\[(\d{1,2})\]/g, (_, n) => `<button type="button" class="kb-cite" data-n="${n}" aria-label="Show source ${n}">${n}</button>`);
  }

  /** Plain text with "- " lines and [n] markers becomes short paragraphs / bullets with clickable source numbers. */
  function answerHtml(text) {
    let html = '';
    let list = [];
    const flush = () => {
      if (list.length) html += `<ul>${list.map((li) => `<li>${citeHtml(li)}</li>`).join('')}</ul>`;
      list = [];
    };
    String(text).split(/\n+/).map((l) => l.trim()).filter(Boolean).forEach((line) => {
      const bullet = line.match(/^[-•*]\s+(.*)$/);
      if (bullet) list.push(bullet[1]);
      else { flush(); html += `<p>${citeHtml(line)}</p>`; }
    });
    flush();
    return html;
  }

  const STATUS_LOOK = {
    answered: { cls: 'ok', icon: '' },
    off_topic: { cls: 'muted', icon: '💬' },
    not_found: { cls: 'muted', icon: '🔍' },
    empty: { cls: 'muted', icon: '📚' },
    not_stated: { cls: 'warn', icon: '⚠️' },
    unverified: { cls: 'warn', icon: '⚠️' },
  };

  function sourcesHtml(res) {
    if (!res.sources || !res.sources.length) return '';
    const heading = res.status === 'answered' ? 'Sources'
      : res.status === 'unverified' ? 'Closest passages — please read them yourself' : 'Related passages';
    return `
      <div class="kb-sources">
        <div class="kb-sources-title">${heading}</div>
        ${res.sources.map((s) => `
          <div class="kb-source" data-n="${s.n}">
            <button type="button" class="kb-source-head" aria-expanded="false">
              <span class="kb-source-n">${s.n}</span>
              <span class="kb-source-icon" aria-hidden="true">${s.kind === 'PRODUCT' ? '🛍️' : '📄'}</span>
              <span class="kb-source-title">${esc(s.title)}</span>
              ${s.location ? `<span class="kb-source-loc">${esc(s.location)}</span>` : ''}
              <span class="kb-source-chev" aria-hidden="true">▾</span>
            </button>
            <div class="kb-source-text" hidden>${esc(s.text)}</div>
          </div>`).join('')}
      </div>`;
  }

  function addAnswer(res) {
    const look = STATUS_LOOK[res.status] || STATUS_LOOK.not_found;
    const body = res.status === 'answered' ? answerHtml(res.answer) : `<p>${esc(res.answer)}</p>`;
    const searched = res.status === 'answered' && res.searched_for ? `<div class="kb-searched">Searched for: ${esc(res.searched_for)}</div>` : '';
    const shared = res.status === 'not_found' || res.status === 'not_stated' || res.status === 'unverified'
      ? '<div class="kb-shared">Shared with your admins so they can add the missing information.</div>' : '';
    const actions = res.status === 'answered'
      ? '<div class="kb-actions"><button type="button" class="crm-link-btn" data-copy>Copy answer</button></div>' : '';
    $('kbMessages').insertAdjacentHTML('beforeend', `
      <div class="kb-msg bot ${look.cls}" data-answer="${esc(stripCitations(res.answer))}">
        <div class="kb-bot-body">
          ${look.icon ? `<span class="kb-status-icon" aria-hidden="true">${look.icon}</span>` : ''}
          <div class="kb-answer">${body}</div>
          ${searched}
          ${sourcesHtml(res)}
          ${shared}
          ${actions}
        </div>
      </div>`);
    scrollToEnd();
  }

  function addError(message) {
    $('kbMessages').insertAdjacentHTML('beforeend', `
      <div class="kb-msg bot error">
        <div class="kb-bot-body">
          <div class="kb-answer"><p>${esc(message)}</p></div>
          <div class="kb-actions"><button type="button" class="crm-link-btn" data-retry>Try again</button></div>
        </div>
      </div>`);
    scrollToEnd();
  }

  async function ask(raw) {
    const question = String(raw || '').trim();
    if (question.length < 2 || busy) return;
    busy = true;
    lastQuestion = question;
    $('kbSend').disabled = true;
    addUser(question);
    const thinking = addThinking();
    try {
      const res = await Api.askKnowledge(question, history.slice(-MAX_HISTORY));
      thinking.remove();
      addAnswer(res);
      if (res.status !== 'off_topic') { // a "not my topic" note must not become context for the next question
        history.push({ role: 'user', text: question }, { role: 'assistant', text: stripCitations(res.answer).slice(0, 1400) });
        history = history.slice(-MAX_HISTORY);
      }
      if (res.status !== 'answered' && res.status !== 'empty' && res.status !== 'off_topic' && isAdmin) loadGaps(true);
    } catch (err) {
      thinking.remove();
      addError(errText(err));
    } finally {
      busy = false;
      $('kbSend').disabled = false;
      $('kbInput').focus();
    }
  }

  function onMessageClick(e) {
    const chip = e.target.closest('[data-q]');
    if (chip) return ask(chip.dataset.q);
    if (e.target.closest('[data-go-docs]')) return setTab('docs');
    if (e.target.closest('[data-retry]')) {
      e.target.closest('.kb-msg')?.remove();
      return ask(lastQuestion);
    }
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const text = copy.closest('.kb-msg').dataset.answer || '';
      const done = () => { copy.textContent = 'Copied ✓'; setTimeout(() => { copy.textContent = 'Copy answer'; }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => Crm.toast('Could not copy', 'error'));
      else Crm.toast('Copy is not available in this browser', 'error');
      return;
    }
    const cite = e.target.closest('.kb-cite');
    if (cite) {
      const source = cite.closest('.kb-msg').querySelector(`.kb-source[data-n="${cite.dataset.n}"]`);
      if (source) {
        toggleSource(source, true);
        source.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        source.classList.add('flash');
        setTimeout(() => source.classList.remove('flash'), 1200);
      }
      return;
    }
    const head = e.target.closest('.kb-source-head');
    if (head) toggleSource(head.closest('.kb-source'));
  }

  function toggleSource(source, forceOpen) {
    const text = source.querySelector('.kb-source-text');
    const head = source.querySelector('.kb-source-head');
    const open = forceOpen === undefined ? text.hidden : forceOpen;
    text.hidden = !open;
    head.setAttribute('aria-expanded', String(open));
    source.classList.toggle('open', open);
  }

  // ------------------------------------------------------------------ Documents
  function normalizeDocs(items) {
    return items.map((d) => ({
      id: d.id,
      name: d.filename || d.title || 'Document',
      type: (d.file_type || '').toLowerCase(),
      status: d.status || 'INDEXED',
      size: d.file_size || 0,
      created: d.created_at || d.updated_at,
      version: d.version || 1,
      error: d.error_message || '',
    }));
  }

  async function loadDocs(quiet) {
    const pane = $('kbPaneDocs');
    if (!quiet && !pane.querySelector('.kb-doc-list')) pane.innerHTML = '<div class="kb-loading">Loading documents…</div>';
    try {
      if (isAdmin) {
        const res = await Api.getKnowledgeDocuments();
        docs = normalizeDocs(Array.isArray(res) ? res : (res.items || []));
      } else {
        library = await Api.getKnowledgeLibrary();
        docs = normalizeDocs(library.documents);
      }
      setCount('kbDocCount', docs.length);
      renderDocs();
      schedulePoll();
    } catch (err) {
      pane.innerHTML = `<div class="crm-empty"><div class="crm-empty-icon">⚠️</div><h4>We couldn't load your documents</h4><p>${esc(errText(err))}</p>
        <button type="button" class="btn-secondary" data-docs-retry>Try again</button></div>`;
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (tab === 'docs' && docs.some((d) => d.status === 'PENDING' || d.status === 'PROCESSING')) {
      pollTimer = setTimeout(() => loadDocs(true), 3000);
    }
  }

  function statusChip(d) {
    if (d.status === 'PENDING' || d.status === 'PROCESSING') return '<span class="kb-status reading"><i class="kb-spin"></i> Reading…</span>';
    if (d.status === 'FAILED') return '<span class="kb-status failed">Couldn’t read it</span>';
    return '<span class="kb-status ready">✓ Ready</span>';
  }

  function docRow(d) {
    const label = TYPE_LABEL[d.type] || d.type.toUpperCase() || 'File';
    const meta = [d.created ? `Added ${formatDate(d.created)}` : '', formatBytes(d.size), d.version > 1 ? `version ${d.version}` : ''].filter(Boolean).join(' · ');
    const failed = d.status === 'FAILED';
    return `
      <div class="kb-doc" data-id="${esc(d.id)}">
        <span class="kb-doc-type t-${esc(d.type)}">${esc(label)}</span>
        <div class="kb-doc-main">
          <div class="kb-doc-name" title="${esc(d.name)}">${esc(d.name)}</div>
          <div class="kb-doc-meta">${esc(meta)}${failed && d.error ? ` · <span class="kb-doc-error">${esc(d.error.slice(0, 100))}</span>` : ''}</div>
        </div>
        ${isAdmin ? statusChip(d) : ''}
        ${isAdmin ? `<div class="kb-doc-actions">
          ${failed ? '<button type="button" class="btn-secondary" data-retry-doc>Retry</button>' : ''}
          <button type="button" class="crm-icon-btn danger" data-delete-doc aria-label="Delete ${esc(d.name)}" title="Delete">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>
          </button>
        </div>` : ''}
      </div>`;
  }

  function renderDocs() {
    const pane = $('kbPaneDocs');
    const productCount = library ? library.product_count : null;
    const catalogNote = productCount
      ? `<p class="kb-note">🛍️ Your ${productCount} product${productCount === 1 ? '' : 's'} are searched too, and prices and stock are always read live${isAdmin ? ' from Products and Inventory, so you never need to upload them' : ''}.</p>`
      : '';
    const upload = isAdmin ? `
      <div class="kb-upload crm-card" id="kbDrop" tabindex="0" role="button" aria-label="Upload documents">
        <div class="kb-upload-icon" aria-hidden="true">⬆️</div>
        <div><strong>Drop files here, or click to choose</strong>
        <span>PDF, Word, text, Markdown or CSV · up to 15 MB each. They're ready to answer questions within moments.</span></div>
        <input type="file" id="kbFile" multiple accept=".pdf,.docx,.txt,.md,.csv" hidden />
      </div>
      <div id="kbUploads"></div>` : '<p class="kb-note">These are the documents your assistant answers from. Ask an admin if something is missing or out of date.</p>';

    let list;
    if (!docs.length) {
      list = `<div class="crm-empty"><div class="crm-empty-icon">📄</div><h4>No documents yet</h4>
        <p>${isAdmin ? 'Add your policies, FAQs, price lists or sales playbooks. Your team can then ask questions and get answers straight from them.' : 'Your admin hasn\'t added any documents yet.'}</p></div>`;
    } else {
      list = docs.map(docRow).join('');
    }
    pane.innerHTML = `${upload}<div class="kb-doc-list crm-card">${list}</div>${catalogNote}`;
    if (isAdmin) bindUpload();
  }

  function bindUpload() {
    const drop = $('kbDrop');
    const input = $('kbFile');
    drop.addEventListener('click', () => input.click());
    drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    input.addEventListener('change', () => { if (input.files.length) uploadFiles(input.files); input.value = ''; });
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('dragover'); }));
    drop.addEventListener('drop', (e) => { if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });
  }

  async function uploadFiles(files) {
    for (const file of Array.from(files)) await uploadOne(file);
  }

  function uploadRow(name) {
    const host = $('kbUploads');
    host.insertAdjacentHTML('beforeend', `<div class="kb-upload-row"><span class="kb-spin"></span><span class="kb-upload-name">${esc(name)}</span><span class="kb-upload-msg">Uploading…</span></div>`);
    return host.lastElementChild;
  }

  function failRow(row, message) {
    row.classList.add('failed');
    row.innerHTML = `<span>⚠️</span><span class="kb-upload-name">${esc(row.dataset.name || '')}</span><span class="kb-upload-msg">${esc(message)}</span>
      <button type="button" class="crm-link-btn" data-dismiss-upload>Dismiss</button>`;
    row.querySelector('[data-dismiss-upload]').addEventListener('click', () => row.remove());
  }

  async function uploadOne(file) {
    const row = uploadRow(file.name);
    row.dataset.name = file.name;
    const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
    if (!ALLOWED.includes(ext)) return failRow(row, 'This type of file isn’t supported. Use PDF, Word, text, Markdown or CSV.');
    if (file.size > MAX_BYTES) return failRow(row, 'This file is larger than 15 MB.');
    const form = new FormData();
    form.append('file', file);
    try {
      const doc = await Api.uploadKnowledgeDocument(form);
      row.remove();
      Crm.toast(doc.status === 'INDEXED' ? `“${file.name}” is ready` : `“${file.name}” uploaded. It's being read in the background.`);
      await loadDocs(true);
      loadLibrary();
    } catch (err) {
      failRow(row, errText(err));
    }
  }

  async function onDocsClick(e) {
    if (e.target.closest('[data-docs-retry]')) return loadDocs();
    const row = e.target.closest('.kb-doc');
    if (!row) return;
    const doc = docs.find((d) => d.id === row.dataset.id);
    if (!doc) return;
    if (e.target.closest('[data-retry-doc]')) {
      try {
        await Api.reindexKnowledgeDocument(doc.id);
        Crm.toast('Reading it again…');
      } catch (err) {
        Crm.toast(errText(err), 'error');
      }
      return loadDocs(true);
    }
    if (e.target.closest('[data-delete-doc]')) {
      const ok = await Crm.confirm(`The assistant will stop using “${doc.name}” in its answers.`, {
        title: 'Delete this document?', confirmText: 'Delete document', danger: true,
      });
      if (!ok) return;
      try {
        await Api.deleteKnowledgeDocument(doc.id);
        Crm.toast('Document deleted');
        await loadDocs(true);
        loadLibrary();
      } catch (err) {
        Crm.toast(errText(err), 'error');
      }
    }
  }

  // ------------------------------------------------------------------ Unanswered questions (admins)
  async function loadGaps(quiet) {
    const pane = $('kbPaneGaps');
    if (!pane) return;
    if (!quiet && tab === 'gaps' && !pane.querySelector('.kb-gap-list')) pane.innerHTML = '<div class="kb-loading">Loading…</div>';
    try {
      gaps = await Api.getKnowledgeGaps();
      setCount('kbGapCount', gaps.open_count);
      if (tab === 'gaps' || !quiet) renderGaps();
    } catch (err) {
      if (tab === 'gaps') pane.innerHTML = `<div class="crm-empty"><div class="crm-empty-icon">⚠️</div><h4>We couldn't load this list</h4><p>${esc(errText(err))}</p>
        <button type="button" class="btn-secondary" data-gaps-retry>Try again</button></div>`;
    }
  }

  function renderGaps() {
    const pane = $('kbPaneGaps');
    if (!pane) return;
    const intro = `
      <div class="kb-gap-intro">
        <p>Your team asked these and the documents couldn't answer them. Add a document that covers them and they'll be answered next time.</p>
        <button type="button" class="btn-primary" data-go-docs>+ Add a document</button>
      </div>`;
    if (!gaps.items.length) {
      pane.innerHTML = `<div class="crm-card"><div class="crm-empty"><div class="crm-empty-icon">🎉</div><h4>Nothing unanswered</h4>
        <p>Every question your team has asked so far was answered from your documents.</p></div></div>`;
      return;
    }
    pane.innerHTML = `${intro}<div class="kb-gap-list crm-card">${gaps.items.map((g) => `
      <div class="kb-gap" data-id="${esc(g.id)}">
        <div class="kb-gap-main">
          <div class="kb-gap-q">${esc(g.question)}</div>
          <div class="kb-gap-meta">Asked ${g.times_asked} time${g.times_asked === 1 ? '' : 's'}${g.last_asked_by ? ` · last by ${esc(g.last_asked_by)}` : ''} · ${esc(Crm.relTime(g.last_asked_at))}</div>
        </div>
        <button type="button" class="btn-secondary" data-dismiss-gap title="Hide this question">Dismiss</button>
      </div>`).join('')}</div>`;
  }

  async function onGapsClick(e) {
    if (e.target.closest('[data-go-docs]')) return setTab('docs');
    if (e.target.closest('[data-gaps-retry]')) return loadGaps();
    const row = e.target.closest('.kb-gap');
    if (!row || !e.target.closest('[data-dismiss-gap]')) return;
    try {
      await Api.dismissKnowledgeGap(row.dataset.id);
      gaps.items = gaps.items.filter((g) => g.id !== row.dataset.id);
      gaps.open_count = Math.max(0, gaps.open_count - 1);
      setCount('kbGapCount', gaps.open_count);
      renderGaps();
      Crm.emit('knowledge-gaps-changed');
    } catch (err) {
      Crm.toast(errText(err), 'error');
    }
  }

  // ------------------------------------------------------------------ public
  function init() {
    const el = $('section-knowledge');
    if (!el) return;
    if (!rendered) {
      rendered = true;
      render(el);
      $('kbPaneDocs').addEventListener('click', onDocsClick);
      $('kbPaneGaps')?.addEventListener('click', onGapsClick);
    }
    loadLibrary();
    if (isAdmin) loadGaps(true);
    if (tab === 'docs') loadDocs(true);
    if (tab === 'ask') setTimeout(() => $('kbInput')?.focus(), 80);
  }

  function refresh() {
    if (!rendered) return;
    loadLibrary();
    if (tab === 'docs') loadDocs(true);
    if (tab === 'gaps') loadGaps(true);
  }

  Crm.on('route:knowledge', (params) => {
    if (!rendered) init();
    if (params && params.tab) setTab(params.tab);
  });

  return { init, refresh };
})();

window.KnowledgeModule = KnowledgeModule;
