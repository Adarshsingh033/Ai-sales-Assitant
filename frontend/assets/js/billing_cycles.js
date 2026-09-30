/**
 * Billing Cycles Management Module
 *
 * Handles CRUD operations, status toggling, filtering, pagination, and modal UI
 * for platform billing cycles in the Super Admin dashboard.
 */

const BillingCyclesModule = (() => {
  let state = {
    cycles: [],
    total: 0,
    page: 1,
    pageSize: 10,
    totalPages: 1,
    search: '',
    statusFilter: '',
    editingCycleId: null,
    formIsActive: true,
    deletingCycleId: null,
    deletingCycleName: null,
  };

  /**
   * Escape HTML utility to prevent XSS.
   */
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /**
   * Load billing cycles from backend API.
   */
  async function loadCycles() {
    const tableContainer = document.getElementById('cycleTableContainer');
    const emptyState = document.getElementById('cycleEmptyState');
    const tbody = document.getElementById('cycleTableBody');

    try {
      const response = await Api.getBillingCycles({
        page: state.page,
        page_size: state.pageSize,
      });

      let items = response.items || [];
      state.total = response.total || 0;
      state.totalPages = response.total_pages || 1;

      // Client-side filtering for search & status filter
      if (state.search) {
        const q = state.search.toLowerCase();
        items = items.filter(
          c =>
            c.name.toLowerCase().includes(q) ||
            c.slug.toLowerCase().includes(q) ||
            (c.description && c.description.toLowerCase().includes(q))
        );
      }

      if (state.statusFilter) {
        const isActive = state.statusFilter === 'ACTIVE';
        items = items.filter(c => c.is_active === isActive);
      }

      state.cycles = items;

      if (state.cycles.length === 0) {
        if (tableContainer) tableContainer.style.display = 'none';
        if (emptyState) {
          emptyState.style.display = 'block';
          emptyState.innerHTML = `
            <div class="empty-state-icon" aria-hidden="true">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.4" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
              </svg>
            </div>
            <h3>No billing cycles found</h3>
            <p>${state.search || state.statusFilter ? 'Try clearing your filters or search terms.' : 'Click "Create Billing Cycle" to get started.'}</p>
          `;
        }
        updatePagination();
        return;
      }

      if (emptyState) emptyState.style.display = 'none';
      if (tableContainer) tableContainer.style.display = 'block';

      if (tbody) {
        tbody.innerHTML = state.cycles
          .map(cycle => {
            const activeClass = cycle.is_active ? 'active' : '';
            const statusLabel = cycle.is_active ? 'Active' : 'Inactive';
            const escapedName = escapeHtml(cycle.name);

            return `
              <tr data-cycle-id="${cycle.id}">
                <td>
                  <strong style="color:var(--color-gray-900);">${escapedName}</strong>
                </td>
                <td>
                  <span class="badge" style="background:#F5F0E6; color:#6B5535; font-weight:600;">
                    ${cycle.duration_months} ${cycle.duration_months === 1 ? 'Month' : 'Months'}
                  </span>
                </td>
                <td>${escapeHtml(cycle.description || '—')}</td>
                <td>
                  <div class="status-toggle-wrapper" onclick="BillingCyclesModule.toggleStatus('${cycle.id}', ${cycle.is_active}, '${escapedName}')" title="Click to toggle Active/Inactive status">
                    <span class="status-toggle ${activeClass}">
                      <span class="status-toggle-handle"></span>
                    </span>
                    <span class="status-toggle-label">${statusLabel}</span>
                  </div>
                </td>
                <td class="cell-actions">
                  <button class="action-btn" onclick="BillingCyclesModule.toggleActionMenu(event)" aria-label="Actions">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="20" height="20">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3z" />
                    </svg>
                  </button>
                  <div class="action-dropdown">
                    <button class="action-dropdown-item" onclick="BillingCyclesModule.handleAction(event, 'view', '${cycle.id}')">View</button>
                    <button class="action-dropdown-item" onclick="BillingCyclesModule.handleAction(event, 'edit', '${cycle.id}')">Edit</button>
                    <button class="action-dropdown-item danger" onclick="BillingCyclesModule.handleAction(event, 'delete', '${cycle.id}', '${escapedName}')">Delete</button>
                  </div>
                </td>
              </tr>
            `;
          })
          .join('');
      }

      updatePagination();
    } catch (err) {
      showToast(err.message || 'Failed to load billing cycles', 'error');
    }
  }

  function updatePagination() {
    const pageIndicator = document.getElementById('cyclePageIndicator');
    const btnFirst = document.getElementById('btnCycleFirst');
    const btnPrev = document.getElementById('btnCyclePrev');
    const btnCurrent = document.getElementById('btnCycleCurrent');
    const btnNext = document.getElementById('btnCycleNext');
    const btnLast = document.getElementById('btnCycleLast');

    if (pageIndicator) pageIndicator.textContent = `Page ${state.page} of ${state.totalPages}`;
    if (btnCurrent) btnCurrent.textContent = state.page;

    if (btnFirst) btnFirst.disabled = state.page <= 1;
    if (btnPrev) btnPrev.disabled = state.page <= 1;
    if (btnNext) btnNext.disabled = state.page >= state.totalPages;
    if (btnLast) btnLast.disabled = state.page >= state.totalPages;
  }

  /**
   * Toggle status of a billing cycle (Active / Inactive).
   */
  async function toggleStatus(cycleId, currentIsActive, cycleName) {
    const nextIsActive = !currentIsActive;
    try {
      await Api.updateBillingCycleStatus(cycleId, nextIsActive);
      showToast(`Billing cycle "${cycleName}" is now ${nextIsActive ? 'active' : 'inactive'}.`, 'success');
      await loadCycles();
      if (typeof SubscriptionPlansModule !== 'undefined' && SubscriptionPlansModule.loadBillingCycleOptions) {
        SubscriptionPlansModule.loadBillingCycleOptions();
      }
    } catch (err) {
      showToast(err.message || 'Failed to update billing cycle status', 'error');
    }
  }

  /**
   * Open Create Billing Cycle Modal.
   */
  function openCreateModal() {
    state.editingCycleId = null;
    state.formIsActive = true;

    const modalTitle = document.getElementById('cycleModalTitle');
    const form = document.getElementById('cycleForm');
    if (modalTitle) modalTitle.textContent = 'Create Billing Cycle';
    if (form) form.reset();

    document.getElementById('formCycleId').value = '';
    updateFormStatusUI();

    const modal = document.getElementById('cycleModal');
    if (modal) modal.classList.add('active');
  }

  /**
   * Open Edit Billing Cycle Modal.
   */
  async function openEditModal(cycleId) {
    try {
      const cycle = await Api.getBillingCycle(cycleId);
      state.editingCycleId = cycleId;
      state.formIsActive = cycle.is_active;

      const modalTitle = document.getElementById('cycleModalTitle');
      if (modalTitle) modalTitle.textContent = 'Edit Billing Cycle';

      document.getElementById('formCycleId').value = cycle.id;
      document.getElementById('cycleName').value = cycle.name || '';
      document.getElementById('cycleDuration').value = cycle.duration_months || 1;
      document.getElementById('cycleDescription').value = cycle.description || '';

      updateFormStatusUI();

      const modal = document.getElementById('cycleModal');
      if (modal) modal.classList.add('active');
    } catch (err) {
      showToast(err.message || 'Failed to fetch billing cycle details', 'error');
    }
  }

  function closeFormModal() {
    const modal = document.getElementById('cycleModal');
    if (modal) modal.classList.remove('active');
    state.editingCycleId = null;
  }

  function toggleFormStatus() {
    state.formIsActive = !state.formIsActive;
    updateFormStatusUI();
  }

  function updateFormStatusUI() {
    const toggle = document.getElementById('cycleFormStatusToggle');
    const label = document.getElementById('cycleFormStatusLabel');
    if (toggle) toggle.className = `status-toggle ${state.formIsActive ? 'active' : ''}`;
    if (label) label.textContent = state.formIsActive ? 'Active' : 'Inactive';
  }

  /**
   * Handle form submit (Save Billing Cycle).
   */
  async function handleFormSubmit(event) {
    event.preventDefault();
    const btnSave = document.getElementById('btnCycleSave');
    if (btnSave) btnSave.disabled = true;

    const name = document.getElementById('cycleName').value.trim();
    const durationMonths = parseInt(document.getElementById('cycleDuration').value, 10);
    const description = document.getElementById('cycleDescription').value.trim();

    const payload = {
      name,
      duration_months: durationMonths,
      description: description || null,
      is_active: state.formIsActive,
    };

    try {
      if (state.editingCycleId) {
        await Api.updateBillingCycle(state.editingCycleId, payload);
        showToast(`Billing cycle "${name}" updated successfully.`, 'success');
      } else {
        await Api.createBillingCycle(payload);
        showToast(`Billing cycle "${name}" created successfully.`, 'success');
      }

      closeFormModal();
      await loadCycles();

      if (typeof SubscriptionPlansModule !== 'undefined' && SubscriptionPlansModule.loadBillingCycleOptions) {
        SubscriptionPlansModule.loadBillingCycleOptions();
      }
    } catch (err) {
      showToast(err.message || 'Failed to save billing cycle', 'error');
    } finally {
      if (btnSave) btnSave.disabled = false;
    }
  }

  /**
   * Open Details Modal.
   */
  async function openDetailsModal(cycleId) {
    try {
      const cycle = await Api.getBillingCycle(cycleId);
      const content = document.getElementById('cycleDetailsContent');
      if (content) {
        content.innerHTML = `
          <div class="detail-section">
            <div class="detail-section-title">BILLING CYCLE INFORMATION</div>
            <div class="detail-grid">
              <div class="detail-row">
                <span class="detail-label">Billing Cycle Name</span>
                <span class="detail-value" style="font-weight:700; color:#8B7355;">${escapeHtml(cycle.name)}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Duration</span>
                <span class="detail-value">${cycle.duration_months} ${cycle.duration_months === 1 ? 'Month' : 'Months'}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Status</span>
                <span class="detail-value">
                  <span class="status-toggle-wrapper" style="pointer-events:none;">
                    <span class="status-toggle ${cycle.is_active ? 'active' : ''}">
                      <span class="status-toggle-handle"></span>
                    </span>
                    <span class="status-toggle-label">${cycle.is_active ? 'Active' : 'Inactive'}</span>
                  </span>
                </span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Created At</span>
                <span class="detail-value">${new Date(cycle.created_at).toLocaleString()}</span>
              </div>
              <div class="detail-row" style="grid-column: span 2;">
                <span class="detail-label">Description</span>
                <span class="detail-value">${escapeHtml(cycle.description || '—')}</span>
              </div>
            </div>
          </div>
        `;
      }

      const modal = document.getElementById('cycleDetailsModal');
      if (modal) modal.classList.add('active');
    } catch (err) {
      showToast(err.message || 'Failed to load details', 'error');
    }
  }

  function closeDetailsModal() {
    const modal = document.getElementById('cycleDetailsModal');
    if (modal) modal.classList.remove('active');
  }

  /**
   * Confirm Delete.
   */
  function confirmDelete(cycleId, name) {
    state.deletingCycleId = cycleId;
    state.deletingCycleName = name;
    const confirmModal = document.getElementById('confirmModal');
    const confirmTitle = document.getElementById('confirmTitle');
    const confirmText = document.getElementById('confirmText');
    const btnConfirm = document.getElementById('btnConfirmAction');

    if (confirmTitle) confirmTitle.textContent = 'Delete Billing Cycle';
    if (confirmText) confirmText.innerHTML = `Are you sure you want to delete <strong>"${escapeHtml(name)}"</strong>? This action cannot be undone.`;

    if (btnConfirm) {
      btnConfirm.className = 'btn-danger';
      btnConfirm.textContent = 'Delete Billing Cycle';
      btnConfirm.onclick = executeDelete;
    }

    if (confirmModal) confirmModal.classList.add('active');
  }

  async function executeDelete() {
    if (!state.deletingCycleId) return;

    const btnConfirm = document.getElementById('btnConfirmAction');
    if (btnConfirm) btnConfirm.disabled = true;

    try {
      await Api.deleteBillingCycle(state.deletingCycleId);
      showToast(`Billing cycle "${state.deletingCycleName || ''}" deleted successfully.`, 'success');
      const confirmModal = document.getElementById('confirmModal');
      if (confirmModal) confirmModal.classList.remove('active');
      state.deletingCycleId = null;
      state.deletingCycleName = null;
      await loadCycles();

      if (typeof SubscriptionPlansModule !== 'undefined' && SubscriptionPlansModule.loadBillingCycleOptions) {
        SubscriptionPlansModule.loadBillingCycleOptions();
      }
    } catch (err) {
      showToast(err.message || 'Failed to delete billing cycle', 'error');
    } finally {
      if (btnConfirm) btnConfirm.disabled = false;
    }
  }

  /**
   * Toast notification system matching Tenant module.
   */
  function showToast(message, type = 'success') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
    }
  }

  /**
   * Initialize event listeners.
   */
  function init() {
    const searchInput = document.getElementById('cycleSearch');
    if (searchInput) {
      let timeout;
      searchInput.addEventListener('input', e => {
        clearTimeout(timeout);
        timeout = setTimeout(() => {
          state.search = e.target.value.trim();
          state.page = 1;
          loadCycles();
        }, 300);
      });
    }

    const statusFilter = document.getElementById('cycleStatusFilter');
    if (statusFilter) {
      statusFilter.addEventListener('change', e => {
        state.statusFilter = e.target.value;
        state.page = 1;
        loadCycles();
      });
    }

    const rowsSelect = document.getElementById('cycleRowsSelect');
    if (rowsSelect) {
      rowsSelect.addEventListener('change', e => {
        state.pageSize = parseInt(e.target.value, 10);
        state.page = 1;
        loadCycles();
      });
    }

    const btnFirst = document.getElementById('btnCycleFirst');
    const btnPrev = document.getElementById('btnCyclePrev');
    const btnNext = document.getElementById('btnCycleNext');
    const btnLast = document.getElementById('btnCycleLast');

    btnFirst?.addEventListener('click', () => {
      if (state.page > 1) {
        state.page = 1;
        loadCycles();
      }
    });
    btnPrev?.addEventListener('click', () => {
      if (state.page > 1) {
        state.page--;
        loadCycles();
      }
    });
    btnNext?.addEventListener('click', () => {
      if (state.page < state.totalPages) {
        state.page++;
        loadCycles();
      }
    });
    btnLast?.addEventListener('click', () => {
      if (state.page < state.totalPages) {
        state.page = state.totalPages;
        loadCycles();
      }
    });

    const form = document.getElementById('cycleForm');
    form?.addEventListener('submit', handleFormSubmit);
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

  function handleAction(event, action, cycleId, extra) {
    if (event) event.stopPropagation();
    document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));

    if (action === 'view') {
      openDetailsModal(cycleId);
    } else if (action === 'edit') {
      openEditModal(cycleId);
    } else if (action === 'delete') {
      confirmDelete(cycleId, extra);
    }
  }

  return {
    loadCycles,
    openCreateModal,
    openEditModal,
    closeFormModal,
    toggleStatus,
    toggleFormStatus,
    openDetailsModal,
    closeDetailsModal,
    confirmDelete,
    toggleActionMenu,
    handleAction,
    showToast,
  };
})();
