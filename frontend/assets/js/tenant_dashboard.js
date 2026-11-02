/**
 * Home — what needs attention today.
 *
 * Shows a friendly greeting, the first-run setup checklist (admins), the things waiting on you
 * (overdue tasks, chats that need a person, deals nobody owns), a few headline numbers,
 * today's tasks, recent chats and the newest deals. A sales rep's numbers only cover their own work.
 */

const TenantDashboardModule = (() => {
  const HIDE_SETUP_KEY = 'asa_hide_setup_card';
  let loading = false;

  function formatCurrency(val) {
    return Crm.money(val ?? 0);
  }

  function formatDate(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function greetingFor(name) {
    const hour = new Date().getHours();
    const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    return `${part}, ${name || 'there'} 👋`;
  }

  async function load() {
    if (loading) return;
    loading = true;
    setText('homeGreeting', greetingFor(Crm.user.first_name));
    try {
      const [stats, home, aiUsage] = await Promise.all([
        Api.getTenantDashboard(),
        Crm.refreshBadges(),
        Crm.isAdmin ? Api.getAIUsageStats(30).catch(() => null) : Promise.resolve(null),
      ]);
      renderSubtitle(home);
      renderSetup(home);
      renderAttention(home);
      renderStats(stats, home, aiUsage);
      renderTasks(home);
      renderConversations(stats);
      renderDeals(stats);
    } catch (err) {
      console.error('Failed to load home:', err);
      Crm.toast(err.message || 'Could not load your home screen', 'error');
    } finally {
      loading = false;
    }
  }

  // ------------------------------------------------------------ top of the page
  function renderSubtitle(home) {
    if (!home) { setText('homeSubtitle', 'Here is what needs your attention today.'); return; }
    const t = home.tasks || {};
    let text = 'You are all caught up. Nothing is waiting on you right now. 🎉';
    if (t.overdue) text = `You have ${t.overdue} overdue task${t.overdue === 1 ? '' : 's'} to catch up on.`;
    else if (t.today) text = `You have ${t.today} task${t.today === 1 ? '' : 's'} due today.`;
    else if (home?.needs_human) text = `${home.needs_human} chat${home.needs_human === 1 ? ' is' : 's are'} waiting for a person to reply.`;
    setText('homeSubtitle', text);
  }

  function renderSetup(home) {
    const card = document.getElementById('setupCard');
    if (!card) return;
    const steps = home?.setup || [];
    const remaining = steps.filter((s) => !s.done);
    let hidden = false;
    try { hidden = localStorage.getItem(HIDE_SETUP_KEY) === '1'; } catch (e) { /* storage unavailable */ }
    if (!Crm.isAdmin || !steps.length || !remaining.length || hidden) {
      card.hidden = true;
      return;
    }
    const doneCount = steps.length - remaining.length;
    const nextKey = remaining[0].key;
    card.hidden = false;
    card.innerHTML = `
      <div class="home-setup-head">
        <div>
          <h3>Get set up in a few minutes</h3>
          <p>${doneCount} of ${steps.length} done — finish these and your AI assistant is ready to talk to customers.</p>
        </div>
        <button type="button" class="crm-link-btn" id="btnHideSetup">Hide</button>
      </div>
      <div class="home-setup-bar" role="progressbar" aria-valuemin="0" aria-valuemax="${steps.length}" aria-valuenow="${doneCount}"><span style="width:${Math.round((doneCount / steps.length) * 100)}%"></span></div>
      <ul class="home-setup-list">
        ${steps.map((s) => `
          <li class="home-setup-step ${s.done ? 'done' : ''}">
            <span class="home-setup-check" aria-hidden="true">${s.done ? '✓' : ''}</span>
            <div class="home-setup-text"><strong>${Crm.esc(s.title)}</strong><span>${Crm.esc(s.hint)}</span></div>
            ${s.done ? '' : `<button type="button" class="${s.key === nextKey ? 'btn-primary' : 'btn-secondary'}" data-target="${Crm.esc(s.target)}">${s.key === nextKey ? 'Start' : 'Open'}</button>`}
          </li>`).join('')}
      </ul>`;
    card.querySelectorAll('[data-target]').forEach((btn) => btn.addEventListener('click', () => {
      document.querySelector(`.nav-item[data-section="${btn.dataset.target}"]`)?.click();
    }));
    card.querySelector('#btnHideSetup')?.addEventListener('click', () => {
      try { localStorage.setItem(HIDE_SETUP_KEY, '1'); } catch (e) { /* ignore */ }
      card.hidden = true;
    });
  }

  function renderAttention(home) {
    const host = document.getElementById('homeAttention');
    if (!host) return;
    if (!home) {
      host.innerHTML = `<div class="home-load-error">We couldn't load what needs your attention. <button type="button" class="crm-link-btn" id="homeRetry">Try again</button></div>`;
      host.querySelector('#homeRetry')?.addEventListener('click', () => load());
      return;
    }
    const t = home.tasks || {};
    const items = [
      { n: t.overdue || 0, label: 'Overdue tasks', hint: 'People waiting on a follow-up', tone: 'red', go: () => Crm.goto('tasks') },
      { n: t.today || 0, label: 'Due today', hint: 'Tasks planned for today', tone: 'amber', go: () => Crm.goto('tasks') },
      { n: home.needs_human || 0, label: 'Chats need a person', hint: 'Customers waiting for a reply', tone: 'blue', go: () => Crm.goto('inbox') },
    ];
    if (Crm.isAdmin) {
      items.push({ n: home.unassigned_leads || 0, label: 'Deals without an owner', hint: 'Give them to someone on the team', tone: 'purple', go: () => Crm.goto('pipeline', { unassigned: '1' }) });
      items.push({ n: home.unanswered_questions || 0, label: 'Unanswered questions', hint: 'Add a document that covers them', tone: 'blue', go: () => Crm.goto('knowledge', { tab: 'gaps' }) });
    }
    host.innerHTML = items.map((it, i) => `
      <button type="button" class="home-attn-card tone-${it.tone} ${it.n ? '' : 'clear'}" data-i="${i}">
        <span class="home-attn-count">${it.n ? it.n : '✓'}</span>
        <span class="home-attn-text"><strong>${Crm.esc(it.label)}</strong><span>${it.n ? Crm.esc(it.hint) : 'All clear'}</span></span>
      </button>`).join('');
    host.querySelectorAll('[data-i]').forEach((btn) => btn.addEventListener('click', () => items[Number(btn.dataset.i)].go()));
  }

  // ------------------------------------------------------------ numbers
  function renderStats(stats, home, aiUsage) {
    const stages = home?.stage_counts;
    if (stages) {
      const open = ['NEW', 'CONTACTED', 'QUALIFIED', 'PROPOSAL'].reduce((sum, k) => sum + (stages[k] || 0), 0);
      setText('statOpenDeals', open);
      setText('statWonDeals', stages.WON || 0);
    }
    setText('statPipelineValue', formatCurrency(stats.total_pipeline_value));
    setText('statHotLeads', stats.hot_leads ?? 0);
    setText('statAiConversations', stats.active_ai_conversations ?? 0);
    setText('statHumanTakeover', stats.resolved_conversations ?? 0);
    if (aiUsage) {
      const total = aiUsage.total_tokens || 0;
      setText('statTotalTokens', total > 1000000 ? (total / 1000000).toFixed(1) + 'M' : (total > 1000 ? (total / 1000).toFixed(1) + 'k' : total));
    }
  }

  // ------------------------------------------------------------ today's tasks
  function renderTasks(home) {
    const body = document.getElementById('homeTasksBody');
    if (!body) return;
    if (!home) {
      body.innerHTML = `<div class="home-load-error">We couldn't load your tasks. <button type="button" class="crm-link-btn" id="homeRetryTasks">Try again</button></div>`;
      body.querySelector('#homeRetryTasks')?.addEventListener('click', () => load());
      return;
    }
    const tasks = home.focus_tasks || [];
    if (!tasks.length) {
      body.innerHTML = `
        <div class="crm-empty home-empty">
          <div class="crm-empty-icon">🎉</div>
          <h4>Nothing due today</h4>
          <p>Plan your next follow-up so no customer is forgotten.</p>
          <button type="button" class="btn-primary" id="homeAddTask">+ Add a task</button>
        </div>`;
      body.querySelector('#homeAddTask')?.addEventListener('click', () => {
        if (window.TasksModule) TasksModule.openForm({});
      });
      return;
    }
    body.innerHTML = `<ul class="home-task-list">${tasks.map((t) => {
      const due = Crm.dueLabel(t.due_at, false);
      return `
        <li class="home-task-row">
          <button type="button" class="home-task-check" data-id="${Crm.esc(t.id)}" aria-label="Mark “${Crm.esc(t.title)}” as done" title="Mark as done"></button>
          <div class="home-task-main">
            <strong>${Crm.esc(t.title)}</strong>
            ${t.customer_name ? `<span class="crm-muted crm-small">${Crm.esc(t.customer_name)}</span>` : ''}
          </div>
          <span class="crm-due ${due.state}">${Crm.esc(due.text)}</span>
        </li>`;
    }).join('')}</ul>`;
    body.querySelectorAll('.home-task-check').forEach((btn) => btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.classList.add('done');
      try {
        await Api.completeTask(btn.dataset.id);
        Crm.toast('Task done');
        Crm.emit('tasks-changed');
        load();
      } catch (err) {
        btn.disabled = false;
        btn.classList.remove('done');
        Crm.toast(err.message, 'error');
      }
    }));
  }

  // ------------------------------------------------------------ chats and deals
  function renderConversations(stats) {
    const convList = document.getElementById('dashboardRecentConversationsList');
    if (!convList) return;
    if (!stats.recent_conversations || stats.recent_conversations.length === 0) {
      convList.innerHTML = `<li class="db-empty">No chats yet. When a customer messages your assistant, it shows up here.</li>`;
      return;
    }
    convList.innerHTML = stats.recent_conversations.map((c) => `
      <li class="db-activity-item" style="cursor:pointer;" onclick="TenantDashboardModule.openConversation('${Crm.esc(c.id)}')">
        <span class="db-activity-dot ${c.status === 'ESCALATED_HUMAN' || c.status === 'WAITING_SALES' ? 'auth' : 'create'}"></span>
        <div class="db-activity-content">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span class="db-activity-action">${Crm.esc(c.customer_name || 'Customer')}</span>
            <span class="channel-badge ${Crm.esc((c.channel || '').toLowerCase())}">${Crm.esc(c.channel)}</span>
          </div>
          <div class="db-activity-desc">${Crm.esc(chatLine(c))}</div>
        </div>
        <span class="db-activity-time">${formatDate(c.last_activity_at)}</span>
      </li>`).join('');
  }

  /** One honest line per chat, from its real status (and the AI's detected topic when the AI is still on it). */
  function chatLine(c) {
    if (c.status === 'WAITING_SALES') return 'Waiting for a person to reply';
    if (c.status === 'ESCALATED_HUMAN') return 'With a person';
    if (c.status === 'RESOLVED') return 'Resolved';
    const topic = c.detected_intent ? `: ${String(c.detected_intent).replace(/_/g, ' ')}` : '';
    return `With the AI${topic}`;
  }

  function renderDeals(stats) {
    const body = document.getElementById('dashboardRecentLeadsBody');
    if (!body) return;
    if (!stats.recent_leads || stats.recent_leads.length === 0) {
      body.innerHTML = `<tr><td colspan="5" class="db-empty">No deals yet. They appear when customers chat with your assistant, or you add one from a contact.</td></tr>`;
      return;
    }
    body.innerHTML = stats.recent_leads.map((lead) => `
      <tr style="cursor:pointer;" onclick="window.LeadsModule && LeadsModule.openLeadDetail('${Crm.esc(lead.id)}')">
        <td>
          <div style="font-weight:600; color:#1e293b;">${Crm.esc(lead.customer_name || lead.title || 'New contact')}</div>
          <div style="font-size:12px; color:#64748b;">${Crm.esc(lead.title || '')}</div>
        </td>
        <td>${Crm.scorePill(lead.score) || '<span class="crm-muted">—</span>'}</td>
        <td>${Crm.stagePill(lead.status)}</td>
        <td>${lead.budget ? formatCurrency(lead.budget) : '—'}</td>
        <td style="font-size:12px; color:#94a3b8;">${formatDate(lead.created_at)}</td>
      </tr>`).join('');
  }

  function openConversation(convId) {
    Crm.goto('inbox');
    if (window.ConversationsModule && ConversationsModule.selectConversation) {
      setTimeout(() => ConversationsModule.selectConversation(convId), 200);
    }
  }

  Crm.on('tasks-changed', () => {
    const section = document.getElementById('section-dashboard');
    if (section && !section.hidden) load();
  });

  return {
    init() { load(); },
    openConversation,
    refresh() { load(); },
  };
})();

window.TenantDashboardModule = TenantDashboardModule;
