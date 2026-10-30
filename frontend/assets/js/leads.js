/**
 * Leads & Pipeline Controller
 * Manages lead listing, filters, stage progressions, and AI qualification details.
 */

const LeadsModule = (() => {
  let leads = [];
  let currentPage = 1;
  let pageSize = 10;
  let totalPages = 1;
  let totalCount = 0;
  let searchTimeout = null;
  let currentLead = null;

  function formatCurrency(val) {
    if (!val && val !== 0) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0
    }).format(val);
  }

  function formatDate(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
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

  function getPriorityBadge(priority) {
    const p = (priority || 'WARM').toUpperCase();
    return `<span class="priority-pill ${p.toLowerCase()}">${p}</span>`;
  }

  function getStageBadge(stage) {
    const s = stage || 'NEW';
    const label = s.replace('_', ' ').toLowerCase();
    return `<span class="stage-pill stage-${s.toLowerCase()}">${label}</span>`;
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

  async function loadLeads() {
    const searchInput = document.getElementById('leadSearchInput');
    const stageFilter = document.getElementById('leadStageFilter');
    const priorityFilter = document.getElementById('leadPriorityFilter');

    const params = {
      page: currentPage,
      page_size: pageSize,
      search: searchInput?.value?.trim() || '',
      stage: stageFilter?.value || '',
      priority: priorityFilter?.value || '',
    };

    const tbody = document.getElementById('leadsTableBody');
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" class="db-empty">Loading leads...</td></tr>`;
    }

    try {
      const res = await Api.getLeads(params);
      leads = res.items || [];
      currentPage = res.page || 1;
      totalPages = res.pages || 1;
      totalCount = res.total || 0;
      renderTable();
      updatePaginationControls();
    } catch (err) {
      console.error('Failed to load leads:', err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="db-empty" style="color:#e11d48;">Error loading leads: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderTable() {
    const tbody = document.getElementById('leadsTableBody');
    if (!tbody) return;

    if (leads.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="db-empty">No leads found matching your criteria.</td></tr>`;
      return;
    }

    tbody.innerHTML = leads.map(lead => {
      const cust = lead.customer || {};
      const name = lead.customer_name || cust.name || lead.name || lead.title || 'Anonymous Prospect';
      const company = cust.company || lead.company || (lead.title && lead.title !== name ? lead.title : '—');
      const phone = lead.customer_phone || cust.phone || lead.phone || '—';
      const email = lead.customer_email || cust.email || lead.email || '';
      const score = lead.score ?? lead.qualification_score ?? 50;
      const stage = lead.status ?? lead.stage ?? 'NEW';
      const val = lead.budget ?? lead.estimated_value;

      return `
        <tr>
          <td>
            <div style="font-weight:600; color:#1e293b;">${escapeHtml(name)}</div>
            <div style="font-size:12px; color:#64748b;">${escapeHtml(company)}</div>
          </td>
          <td>
            <div style="font-size:13px; color:#334155;">${escapeHtml(phone)}</div>
            ${email ? `<div style="font-size:12px; color:#94a3b8;">${escapeHtml(email)}</div>` : ''}
          </td>
          <td>${getScoreBadge(score)}</td>
          <td>${getPriorityBadge(lead.priority)}</td>
          <td>${getStageBadge(stage)}</td>
          <td style="font-weight:600; color:#1e293b;">${formatCurrency(val)}</td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn-secondary" style="padding:4px 8px; font-size:12px;" onclick="LeadsModule.openLeadDetail('${lead.id}')">
                View & Edit
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  function updatePaginationControls() {
    const info = document.getElementById('leadsPageIndicator');
    if (info) info.textContent = `Page ${currentPage} of ${totalPages || 1} (${totalCount} total)`;

    const btnFirst = document.getElementById('btnLeadsFirst');
    const btnPrev = document.getElementById('btnLeadsPrev');
    const btnCurrent = document.getElementById('btnLeadsCurrent');
    const btnNext = document.getElementById('btnLeadsNext');
    const btnLast = document.getElementById('btnLeadsLast');

    if (btnFirst) btnFirst.disabled = currentPage <= 1;
    if (btnPrev) btnPrev.disabled = currentPage <= 1;
    if (btnCurrent) btnCurrent.textContent = currentPage;
    if (btnNext) btnNext.disabled = currentPage >= totalPages;
    if (btnLast) btnLast.disabled = currentPage >= totalPages;
  }

  async function openLeadDetail(leadId) {
    try {
      const lead = await Api.getLead(leadId);
      currentLead = lead;
      populateLeadModal(lead);
      const modal = document.getElementById('leadDetailModal');
      if (modal) modal.classList.add('active');
    } catch (err) {
      if (window.showToast) window.showToast('Could not load lead details: ' + err.message, 'error');
    }
  }

  function closeLeadDetail() {
    const modal = document.getElementById('leadDetailModal');
    if (modal) modal.classList.remove('active');
    currentLead = null;
  }

  function populateLeadModal(lead) {
    const cust = lead.customer || {};
    const name = lead.customer_name || cust.name || lead.name || lead.title || 'Anonymous Prospect';
    const company = cust.company || lead.company || (lead.title && lead.title !== name ? lead.title : 'Individual Client');
    const phone = lead.customer_phone || cust.phone || lead.phone || '—';
    const email = lead.customer_email || cust.email || lead.email || '—';
    const score = lead.score ?? lead.qualification_score ?? 50;
    const stage = lead.status ?? lead.stage ?? 'NEW';
    const val = lead.budget ?? lead.estimated_value;

    document.getElementById('leadModalName').textContent = name;
    document.getElementById('leadModalCompany').textContent = company;
    document.getElementById('leadModalPhone').textContent = phone;
    document.getElementById('leadModalEmail').textContent = email;

    document.getElementById('leadModalScore').innerHTML = getScoreBadge(score);
    document.getElementById('leadModalPriority').innerHTML = getPriorityBadge(lead.priority);

    // Form inputs
    const stageSelect = document.getElementById('leadModalStage');
    if (stageSelect) stageSelect.value = stage;

    const prioritySelect = document.getElementById('leadModalPrioritySelect');
    if (prioritySelect) prioritySelect.value = lead.priority || 'WARM';

    const valueInput = document.getElementById('leadModalValue');
    if (valueInput) valueInput.value = val || '';

    const notesInput = document.getElementById('leadModalNotes');
    if (notesInput) notesInput.value = lead.notes || '';

    // AI Qualification Summary
    const summaryBox = document.getElementById('leadModalAiSummary');
    if (summaryBox) {
      summaryBox.textContent = lead.score_reason || lead.ai_qualification_summary || 'Qualified prospect inquiring about catalog offerings.';
    }

    // Detected Requirements & Pain Points
    const painPointsList = document.getElementById('leadModalPainPoints');
    if (painPointsList) {
      const points = lead.requirements ? [lead.requirements] : (lead.pain_points || []);
      if (points.length === 0) {
        painPointsList.innerHTML = '<span style="color:#94a3b8; font-size:13px;">None identified yet</span>';
      } else {
        painPointsList.innerHTML = points.map(p => `<span class="priority-pill cold" style="margin-right:6px; margin-bottom:6px; font-weight:normal;">${escapeHtml(p)}</span>`).join('');
      }
    }
  }

  async function saveLeadChanges() {
    if (!currentLead) return;
    const stageSelect = document.getElementById('leadModalStage');
    const prioritySelect = document.getElementById('leadModalPrioritySelect');
    const valueInput = document.getElementById('leadModalValue');
    const notesInput = document.getElementById('leadModalNotes');

    const updatePayload = {
      status: stageSelect?.value,
      priority: prioritySelect?.value,
      budget: valueInput?.value ? parseFloat(valueInput.value) : null,
      notes: notesInput?.value || '',
    };

    try {
      await Api.updateLead(currentLead.id, updatePayload);
      if (window.showToast) window.showToast('Lead updated successfully', 'success');
      closeLeadDetail();
      loadLeads();
      if (typeof TenantDashboardModule !== 'undefined') TenantDashboardModule.refresh();
    } catch (err) {
      if (window.showToast) window.showToast('Failed to update lead: ' + err.message, 'error');
    }
  }

  function openLeadConversation() {
    if (!currentLead || !currentLead.customer_id) return;
    closeLeadDetail();
    const convNav = document.querySelector('.nav-item[data-section="section-conversations"]');
    if (convNav) {
      convNav.click();
      if (typeof ConversationsModule !== 'undefined' && ConversationsModule.filterByCustomer) {
        setTimeout(() => ConversationsModule.filterByCustomer(currentLead.customer?.phone || currentLead.customer_id), 150);
      }
    }
  }

  function setupEvents() {
    const searchInput = document.getElementById('leadSearchInput');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
          currentPage = 1;
          loadLeads();
        }, 300);
      });
    }

    const stageFilter = document.getElementById('leadStageFilter');
    if (stageFilter) {
      stageFilter.addEventListener('change', () => {
        currentPage = 1;
        loadLeads();
      });
    }

    const priorityFilter = document.getElementById('leadPriorityFilter');
    if (priorityFilter) {
      priorityFilter.addEventListener('change', () => {
        currentPage = 1;
        loadLeads();
      });
    }

    const pageSizeSelect = document.getElementById('leadsPageSize');
    if (pageSizeSelect) {
      pageSizeSelect.addEventListener('change', (e) => {
        pageSize = parseInt(e.target.value, 10) || 10;
        currentPage = 1;
        loadLeads();
      });
    }

    // Pagination buttons
    document.getElementById('btnLeadsFirst')?.addEventListener('click', () => {
      if (currentPage > 1) { currentPage = 1; loadLeads(); }
    });
    document.getElementById('btnLeadsPrev')?.addEventListener('click', () => {
      if (currentPage > 1) { currentPage--; loadLeads(); }
    });
    document.getElementById('btnLeadsNext')?.addEventListener('click', () => {
      if (currentPage < totalPages) { currentPage++; loadLeads(); }
    });
    document.getElementById('btnLeadsLast')?.addEventListener('click', () => {
      if (currentPage < totalPages) { currentPage = totalPages; loadLeads(); }
    });
  }

  return {
    init() {
      setupEvents();
      loadLeads();
    },
    loadLeads,
    openLeadDetail,
    closeLeadDetail,
    saveLeadChanges,
    openLeadConversation
  };
})();

window.LeadsModule = LeadsModule;
