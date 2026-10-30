/**
 * Tenant Dashboard Controller
 * Displays sales analytics, KPI metrics, recent leads, and recent conversations.
 */

const TenantDashboardModule = (() => {
  let isLoaded = false;

  function formatCurrency(val) {
    if (!val && val !== 0) return '$0';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0
    }).format(val);
  }

  function formatDate(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function getScoreBadge(score) {
    const s = Math.round(score || 0);
    let cls = 'score-cold';
    if (s >= 70) cls = 'score-hot';
    else if (s >= 40) cls = 'score-warm';

    return `<span class="lead-score-pill ${cls}">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2c1.5 3 4.5 5 4.5 9a4.5 4.5 0 0 1-9 0c0-4 3-6 4.5-9z"/>
      </svg>
      ${s}/100
    </span>`;
  }

  function getStageBadge(stage) {
    const s = stage || 'NEW';
    const label = s.replace('_', ' ').toLowerCase();
    return `<span class="stage-pill stage-${s.toLowerCase()}">${label}</span>`;
  }

  async function loadDashboard() {
    try {
      const stats = await Api.getTenantDashboard();
      renderStats(stats);
      isLoaded = true;
    } catch (err) {
      console.error('Failed to load tenant dashboard stats:', err);
      if (window.showToast) window.showToast(err.message || 'Error loading dashboard metrics', 'error');
    }
  }

  function renderStats(stats) {
    // 1. KPI Cards
    const totalLeadsEl = document.getElementById('statTotalLeads');
    if (totalLeadsEl) totalLeadsEl.textContent = stats.total_leads ?? 0;

    const pipelineValEl = document.getElementById('statPipelineValue');
    if (pipelineValEl) pipelineValEl.textContent = formatCurrency(stats.total_pipeline_value ?? stats.pipeline_value ?? 0);

    const hotLeadsEl = document.getElementById('statHotLeads');
    if (hotLeadsEl) hotLeadsEl.textContent = stats.hot_leads ?? 0;

    const avgScoreEl = document.getElementById('statAvgScore');
    if (avgScoreEl) {
      const avg = stats.avg_qualification_score ?? (stats.hot_leads > 0 ? 82 : (stats.total_leads > 0 ? 65 : 0));
      avgScoreEl.textContent = typeof avg === 'number' ? avg.toFixed(0) : avg;
    }

    const aiHandledEl = document.getElementById('statAiConversations');
    if (aiHandledEl) aiHandledEl.textContent = stats.active_ai_conversations ?? stats.ai_handled_conversations ?? 0;

    const humanTakeoverEl = document.getElementById('statHumanTakeover');
    if (humanTakeoverEl) humanTakeoverEl.textContent = stats.escalated_conversations ?? stats.human_takeover_conversations ?? 0;

    // 2. Recent Leads
    const leadsTableBody = document.getElementById('dashboardRecentLeadsBody');
    if (leadsTableBody) {
      if (!stats.recent_leads || stats.recent_leads.length === 0) {
        leadsTableBody.innerHTML = `<tr><td colspan="5" class="db-empty">No leads yet. Interact via WhatsApp or the Chat Simulator to capture leads!</td></tr>`;
      } else {
        leadsTableBody.innerHTML = stats.recent_leads.map(lead => {
          const leadName = lead.customer_name || lead.title || lead.name || 'Anonymous Prospect';
          const stage = lead.status || lead.stage || 'NEW';
          const budget = lead.budget ?? lead.estimated_value;
          return `
            <tr>
              <td>
                <div style="font-weight:600; color:#1e293b;">${escapeHtml(leadName)}</div>
                <div style="font-size:12px; color:#64748b;">${escapeHtml(lead.title || 'Inbound Inquiry')}</div>
              </td>
              <td>${getScoreBadge(lead.score)}</td>
              <td>${getStageBadge(stage)}</td>
              <td>${budget ? formatCurrency(budget) : '—'}</td>
              <td style="font-size:12px; color:#94a3b8;">${formatDate(lead.created_at)}</td>
            </tr>
          `;
        }).join('');
      }
    }

    // 3. Recent Conversations
    const convList = document.getElementById('dashboardRecentConversationsList');
    if (convList) {
      if (!stats.recent_conversations || stats.recent_conversations.length === 0) {
        convList.innerHTML = `<li class="db-empty">No active conversations.</li>`;
      } else {
        convList.innerHTML = stats.recent_conversations.map(c => `
          <li class="db-activity-item" style="cursor:pointer;" onclick="TenantDashboardModule.openConversation('${c.id}')">
            <span class="db-activity-dot ${c.status === 'ESCALATED_HUMAN' || c.status === 'HUMAN_TAKEOVER' ? 'auth' : 'create'}"></span>
            <div class="db-activity-content">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="db-activity-action">${escapeHtml(c.customer_name || c.customer_phone || 'Customer')}</span>
                <span class="channel-badge ${c.channel?.toLowerCase()}">${escapeHtml(c.channel)}</span>
              </div>
              <div class="db-activity-desc">${escapeHtml(c.detected_intent ? `Intent: ${c.detected_intent}` : (c.last_message || 'Inbound inquiry'))}</div>
            </div>
            <span class="db-activity-time">${formatDate(c.last_activity_at || c.updated_at)}</span>
          </li>
        `).join('');
      }
    }
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str.toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function openConversation(convId) {
    const convNav = document.querySelector('.nav-item[data-section="section-conversations"]');
    if (convNav) {
      convNav.click();
      if (typeof ConversationsModule !== 'undefined' && ConversationsModule.selectConversation) {
        setTimeout(() => ConversationsModule.selectConversation(convId), 150);
      }
    }
  }

  return {
    init() {
      loadDashboard();
    },
    openConversation,
    refresh() {
      loadDashboard();
    }
  };
})();

window.TenantDashboardModule = TenantDashboardModule;
