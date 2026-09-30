/**
 * Subscription Plans Module
 * Handles loading, rendering, searching, creating, editing, and status toggles for Subscription Plans.
 */

const SubscriptionPlansModule = (() => {
  // State
  let currentPage = 1;
  const pageSize = 10;
  let totalPages = 1;
  let currentSearch = '';
  let currentStatus = '';
  let currentCycle = '';
  let allPlans = [];

  // DOM Elements
  const els = {
    // List & Toolbar
    tableBody: document.getElementById('planTableBody'),
    searchInput: document.getElementById('planSearch'),
    statusFilter: document.getElementById('planStatusFilter'),
    billingCycleFilter: document.getElementById('planBillingCycleFilter'),
    btnFirst: document.getElementById('btnPlanFirst'),
    btnPrev: document.getElementById('btnPlanPrev'),
    btnNext: document.getElementById('btnPlanNext'),
    btnLast: document.getElementById('btnPlanLast'),
    pageIndicator: document.getElementById('planPageIndicator'),
    emptyState: document.getElementById('planEmptyState'),
    tableContainer: document.getElementById('planTableContainer'),
    
    // Create/Edit Modal
    modal: document.getElementById('planModal'),
    modalTitle: document.getElementById('planModalTitle'),
    form: document.getElementById('planForm'),
    btnSave: document.getElementById('btnPlanSave'),
    planIdInput: document.getElementById('formPlanId'),

    // Details Modal
    detailsModal: document.getElementById('planDetailsModal'),
    detailsContent: document.getElementById('planDetailsContent'),

    // Toast
    toastContainer: document.getElementById('toastContainer')
  };

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------
  function init() {
    if (!els.tableBody) return;

    // Event Listeners for Toolbar
    let debounceTimer;
    if (els.searchInput) {
      els.searchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          currentSearch = e.target.value.trim();
          currentPage = 1;
          loadPlans();
        }, 300);
      });
    }

    if (els.statusFilter) {
      els.statusFilter.addEventListener('change', (e) => {
        currentStatus = e.target.value;
        currentPage = 1;
        loadPlans();
      });
    }

    if (els.billingCycleFilter) {
      els.billingCycleFilter.addEventListener('change', (e) => {
        currentCycle = e.target.value;
        currentPage = 1;
        loadPlans();
      });
    }

    if (els.btnFirst) {
      els.btnFirst.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage = 1;
          loadPlans();
        }
      });
    }

    if (els.btnPrev) {
      els.btnPrev.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage--;
          loadPlans();
        }
      });
    }

    if (els.btnNext) {
      els.btnNext.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage++;
          loadPlans();
        }
      });
    }

    if (els.btnLast) {
      els.btnLast.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage = totalPages;
          loadPlans();
        }
      });
    }

    // Form Submit
    if (els.form) {
      els.form.addEventListener('submit', handleFormSubmit);
    }

    // Initial Load
    loadBillingCycleOptions();
    loadPlans();
  }

  // -------------------------------------------------------------------------
  // Load Billing Cycle Options dynamically
  // -------------------------------------------------------------------------
  async function loadBillingCycleOptions() {
    const cycleSelect = document.getElementById('planBillingCycle');
    const filterSelect = document.getElementById('planBillingCycleFilter');

    try {
      const response = await Api.getBillingCycles({ active_only: true });
      const cycles = response.items || [];

      if (cycleSelect) {
        cycleSelect.innerHTML = cycles.length > 0
          ? cycles.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} (${c.duration_months} ${c.duration_months === 1 ? 'month' : 'months'})</option>`).join('')
          : `<option value="Monthly">Monthly (1 month)</option><option value="Yearly">Yearly (12 months)</option>`;
      }

      if (filterSelect) {
        filterSelect.innerHTML = `<option value="">All Cycles</option>` +
          cycles.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
      }
    } catch (err) {
      console.error('Failed to load billing cycle options:', err);
    }
  }

  // -------------------------------------------------------------------------
  // Loading & Rendering
  // -------------------------------------------------------------------------
  async function loadPlans() {
    renderLoading();
    
    try {
      const response = await Api.getSubscriptionPlans({
        page: currentPage,
        page_size: pageSize
      });
      
      let items = response.items || [];
      
      // Client-side filtering if needed
      if (currentSearch) {
        const q = currentSearch.toLowerCase();
        items = items.filter(p => p.name.toLowerCase().includes(q) || (p.description && p.description.toLowerCase().includes(q)));
      }
      if (currentStatus) {
        const isActive = currentStatus === 'ACTIVE';
        items = items.filter(p => p.is_active === isActive);
      }
      if (currentCycle) {
        items = items.filter(p => p.billing_cycle === currentCycle);
      }

      allPlans = items;
      totalPages = response.total_pages || 1;
      currentPage = response.page || 1;
      
      renderTable(allPlans);
      updatePagination(currentPage, totalPages);
      
    } catch (err) {
      console.error('Failed to load subscription plans:', err);
      showToast('Unable to load subscription plans. Please try again.', 'error');
      renderEmptyState('Error loading data', true);
    }
  }

  function renderLoading() {
    els.tableContainer.style.display = 'block';
    els.emptyState.style.display = 'none';
    els.tableBody.innerHTML = `
      <tr><td colspan="7" style="text-align: center; padding: 48px;">
        <div style="color:var(--color-gray-500);">Loading subscription plans...</div>
      </td></tr>
    `;
  }

  function renderEmptyState(message = 'No subscription plans found', isError = false) {
    els.tableContainer.style.display = 'block';
    els.emptyState.style.display = 'none';
    
    if (isError) {
      els.tableBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; padding: 48px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="color:var(--color-error); margin-bottom: 16px;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width:48px;height:48px;"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg></div>
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${message}</h3>
            <button class="btn-secondary mt-3" onclick="SubscriptionPlansModule.loadPlans()">Retry</button>
          </div>
        </td></tr>
      `;
    } else {
      els.tableBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; padding: 64px 24px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="background: var(--color-gray-100); border-radius: 50%; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; color: var(--color-gray-400);">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 0 0 2.25-2.25V6.75A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25v10.5A2.25 2.25 0 0 0 4.5 19.5Z" /></svg>
            </div>
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${message}</h3>
            <p style="font-size: 13px; color: var(--color-gray-500); margin-bottom: 16px;">There are no subscription plans matching this criteria.</p>
            <button class="btn-primary" onclick="SubscriptionPlansModule.openCreateModal()">Create Plan</button>
          </div>
        </td></tr>
      `;
    }
  }

  function renderTable(plans) {
    if (plans.length === 0) {
      renderEmptyState(currentSearch ? 'No plans match your search' : 'No subscription plans found');
      return;
    }

    els.emptyState.style.display = 'none';
    els.tableContainer.style.display = 'block';
    
    let html = '';
    for (const p of plans) {
      const priceFormatted = p.price === 0 ? 'Free' : `$${p.price.toFixed(2)} ${p.currency || 'USD'}`;
      const cycleFormatted = escapeHtml(p.billing_cycle || 'Monthly');
      const isActive = p.is_active;

      html += `
        <tr>
          <td>
            <strong>${escapeHtml(p.name)}</strong>
            ${p.description ? `<div style="font-size:12px; color:var(--color-gray-500);">${escapeHtml(p.description)}</div>` : ''}
          </td>
          <td><strong>${priceFormatted}</strong></td>
          <td><span class="badge" style="background:#F5F0E6; color:#6B5535; font-weight:600;">${cycleFormatted}</span></td>
          <td>${p.max_users} user${p.max_users > 1 ? 's' : ''}</td>
          <td>${p.max_branches} branch${p.max_branches > 1 ? 'es' : ''}</td>
          <td>
            <div class="status-toggle-wrapper" onclick="SubscriptionPlansModule.togglePlanStatus('${p.id}', ${isActive}, '${escapeHtml(p.name)}')" title="Click to toggle Active/Inactive status">
              <span class="status-toggle ${isActive ? 'active' : ''}">
                <span class="status-toggle-handle"></span>
              </span>
              <span class="status-toggle-label">${isActive ? 'Active' : 'Inactive'}</span>
            </div>
          </td>
          <td class="cell-actions">
            <button class="action-btn" onclick="SubscriptionPlansModule.toggleActionMenu(event)" aria-label="Actions">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="20" height="20">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3z" />
              </svg>
            </button>
            <div class="action-dropdown">
              <button class="action-dropdown-item" onclick="SubscriptionPlansModule.handleAction(event, 'view', '${p.id}')">View</button>
              <button class="action-dropdown-item" onclick="SubscriptionPlansModule.handleAction(event, 'edit', '${p.id}')">Edit</button>
            </div>
          </td>
        </tr>
      `;
    }
    
    els.tableBody.innerHTML = html;
  }

  function updatePagination(page, total) {
    if (els.pageIndicator) els.pageIndicator.textContent = `Page ${page} of ${total}`;
    if (els.btnFirst) els.btnFirst.disabled = page <= 1;
    if (els.btnPrev) els.btnPrev.disabled = page <= 1;
    if (els.btnNext) els.btnNext.disabled = page >= total;
    if (els.btnLast) els.btnLast.disabled = page >= total;
    
    const currentBtn = document.getElementById('btnPlanCurrent');
    if (currentBtn) currentBtn.textContent = page;
  }

  // -------------------------------------------------------------------------
  // Toggle Status
  // -------------------------------------------------------------------------
  async function togglePlanStatus(id, currentIsActive, planName) {
    const nextIsActive = !currentIsActive;
    try {
      await Api.updateSubscriptionPlanStatus(id, nextIsActive);
      showToast(`Subscription plan "${planName}" is now ${nextIsActive ? 'active' : 'inactive'}.`, 'success');
      loadPlans();
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to update plan status.', 'error');
    }
  }

  // -------------------------------------------------------------------------
  // Modals & Forms
  // -------------------------------------------------------------------------
  function openCreateModal() {
    els.form.reset();
    els.planIdInput.value = '';
    els.modalTitle.textContent = 'Create Subscription Plan';
    document.getElementById('planCurrency').value = 'USD';
    document.getElementById('planBillingCycle').value = 'MONTHLY';
    els.modal.classList.add('active');
  }

  function openEditModal(id) {
    const plan = allPlans.find(p => p.id === id);
    if (!plan) return;

    els.form.reset();
    els.planIdInput.value = plan.id;
    els.modalTitle.textContent = 'Edit Subscription Plan';

    // Populate fields
    document.getElementById('planName').value = plan.name || '';
    document.getElementById('planDescription').value = plan.description || '';
    document.getElementById('planPrice').value = plan.price ?? 0;
    document.getElementById('planCurrency').value = plan.currency || 'USD';
    document.getElementById('planBillingCycle').value = plan.billing_cycle || 'MONTHLY';
    document.getElementById('planMaxUsers').value = plan.max_users ?? 1;
    document.getElementById('planMaxBranches').value = plan.max_branches ?? 1;

    els.modal.classList.add('active');
  }

  function closeFormModal() {
    els.modal.classList.remove('active');
  }

  async function handleFormSubmit(e) {
    e.preventDefault();
    
    const id = els.planIdInput.value;
    const isEdit = !!id;
    
    const data = {
      name: document.getElementById('planName').value.trim(),
      description: document.getElementById('planDescription').value.trim() || null,
      price: parseFloat(document.getElementById('planPrice').value) || 0,
      currency: document.getElementById('planCurrency').value.trim() || 'USD',
      billing_cycle: document.getElementById('planBillingCycle').value,
      max_users: parseInt(document.getElementById('planMaxUsers').value, 10) || 1,
      max_branches: parseInt(document.getElementById('planMaxBranches').value, 10) || 1,
    };

    els.btnSave.disabled = true;
    els.btnSave.textContent = 'Saving...';

    try {
      if (isEdit) {
        await Api.updateSubscriptionPlan(id, data);
        showToast('Subscription plan updated successfully.', 'success');
      } else {
        await Api.createSubscriptionPlan(data);
        showToast('Subscription plan created successfully.', 'success');
      }
      
      closeFormModal();
      loadPlans(); // Refresh
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to save subscription plan.', 'error');
    } finally {
      els.btnSave.disabled = false;
      els.btnSave.textContent = 'Save Plan';
    }
  }

  // -------------------------------------------------------------------------
  // Plan Details
  // -------------------------------------------------------------------------
  async function openDetailsModal(id) {
    try {
      const plan = await Api.getSubscriptionPlan(id);
      const isActive = plan.is_active;
      const priceFormatted = plan.price === 0 ? 'Free' : `$${plan.price.toFixed(2)} ${plan.currency || 'USD'}`;
      
      let html = `
        <div class="detail-section">
          <div class="detail-section-title">PLAN OVERVIEW</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Plan Name</span>
              <span class="detail-value">${escapeHtml(plan.name)}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Description</span>
              <span class="detail-value">${escapeHtml(plan.description || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Price</span>
              <span class="detail-value">${priceFormatted}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Billing Cycle</span>
              <span class="detail-value">${plan.billing_cycle === 'YEARLY' ? 'Yearly' : 'Monthly'}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Status</span>
              <span class="detail-value">
                <span class="status-toggle-wrapper" style="pointer-events:none;">
                  <span class="status-toggle ${isActive ? 'active' : ''}">
                    <span class="status-toggle-handle"></span>
                  </span>
                  <span class="status-toggle-label">${isActive ? 'Active' : 'Inactive'}</span>
                </span>
              </span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <div class="detail-section-title">USAGE & LIMITS</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Max Users</span>
              <span class="detail-value">${plan.max_users}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Max Branches</span>
              <span class="detail-value">${plan.max_branches}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Created At</span>
              <span class="detail-value">${new Date(plan.created_at).toLocaleString()}</span>
            </div>
          </div>
        </div>
      `;

      els.detailsContent.innerHTML = html;
      els.detailsModal.classList.add('active');
    } catch (err) {
      console.error(err);
      showToast('Unable to load plan details.', 'error');
    }
  }

  function closeDetailsModal() {
    els.detailsModal.classList.remove('active');
  }

  // -------------------------------------------------------------------------
  // Toast Notifications
  // -------------------------------------------------------------------------
  function showToast(message, type = 'success') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
    }
  }

  // Utility
  function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe.toString()
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
  }

  function toggleActionMenu(event) {
    if (event) event.stopPropagation();
    const cell = event ? event.currentTarget.closest('.cell-actions') : null;
    if (!cell) return;
    const isCurrentlyActive = cell.classList.contains('active');

    document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));

    if (!isCurrentlyActive) {
      cell.classList.add('active');
    }
  }

  function handleAction(event, action, planId) {
    if (event) event.stopPropagation();
    document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));

    if (action === 'view') {
      openDetailsModal(planId);
    } else if (action === 'edit') {
      openEditModal(planId);
    }
  }

  // Public API
  return {
    init,
    loadPlans,
    loadBillingCycleOptions,
    openCreateModal,
    openEditModal,
    closeFormModal,
    openDetailsModal,
    closeDetailsModal,
    togglePlanStatus,
    toggleActionMenu,
    handleAction
  };
})();

document.addEventListener('DOMContentLoaded', SubscriptionPlansModule.init);
