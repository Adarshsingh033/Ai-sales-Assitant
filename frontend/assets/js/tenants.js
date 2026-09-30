/**
 * Tenant Management Module
 * Handles loading, rendering, searching, creating, editing, toggle status, and deleting Tenants.
 */

const TenantsModule = (() => {
  // State
  let currentPage = 1;
  const pageSize = 10;
  let totalPages = 1;
  let currentSearch = '';
  let currentStatus = '';
  let currentIndustry = '';
  let allTenants = [];
  let subscriptionPlans = [];

  // DOM Elements
  const els = {
    // List & Toolbar
    tableBody: document.getElementById('tenantTableBody'),
    searchInput: document.getElementById('tenantSearch'),
    statusFilter: document.getElementById('tenantStatusFilter'),
    industryFilter: document.getElementById('tenantIndustryFilter'),
    btnFirst: document.getElementById('btnTenantFirst'),
    btnPrev: document.getElementById('btnTenantPrev'),
    btnNext: document.getElementById('btnTenantNext'),
    btnLast: document.getElementById('btnTenantLast'),
    pageIndicator: document.getElementById('tenantPageIndicator'),
    emptyState: document.getElementById('tenantEmptyState'),
    tableContainer: document.getElementById('tenantTableContainer'),
    
    // Create/Edit Modal
    modal: document.getElementById('tenantModal'),
    modalTitle: document.getElementById('tenantModalTitle'),
    form: document.getElementById('tenantForm'),
    btnSave: document.getElementById('btnTenantSave'),
    tenantIdInput: document.getElementById('formTenantId'),

    // Details Modal
    detailsModal: document.getElementById('tenantDetailsModal'),
    detailsContent: document.getElementById('tenantDetailsContent'),

    // Confirmation Modal
    confirmModal: document.getElementById('confirmModal'),
    confirmTitle: document.getElementById('confirmTitle'),
    confirmText: document.getElementById('confirmText'),
    btnConfirm: document.getElementById('btnConfirmAction'),
    
    // Toast
    toastContainer: document.getElementById('toastContainer')
  };

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------
  function init() {
    if (!els.tableBody) return; // Only init if elements exist

    // Event Listeners for Toolbar
    let debounceTimer;
    els.searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        currentSearch = e.target.value.trim();
        currentPage = 1;
        loadTenants();
      }, 300);
    });

    els.statusFilter.addEventListener('change', (e) => {
      currentStatus = e.target.value;
      currentPage = 1;
      loadTenants();
    });

    els.industryFilter.addEventListener('change', (e) => {
      currentIndustry = e.target.value;
      currentPage = 1;
      loadTenants();
    });

    if (els.btnFirst) {
      els.btnFirst.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage = 1;
          loadTenants();
        }
      });
    }

    if (els.btnPrev) {
      els.btnPrev.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage--;
          loadTenants();
        }
      });
    }

    if (els.btnNext) {
      els.btnNext.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage++;
          loadTenants();
        }
      });
    }

    if (els.btnLast) {
      els.btnLast.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage = totalPages;
          loadTenants();
        }
      });
    }

    // Form Submit
    els.form.addEventListener('submit', handleFormSubmit);

    // Load Subscription Plans & Initial Tenants
    loadSubscriptionPlans();
    loadTenants();
  }

  // -------------------------------------------------------------------------
  // Subscription Plans
  // -------------------------------------------------------------------------
  async function loadSubscriptionPlans() {
    try {
      const res = await Api.getSubscriptionPlans({ page_size: 100 });
      subscriptionPlans = res.items || [];
      populateSubscriptionPlanDropdown();
    } catch (err) {
      console.warn('Could not load subscription plans:', err);
    }
  }

  function populateSubscriptionPlanDropdown() {
    const planSelect = document.getElementById('tenantSubscriptionPlan');
    if (!planSelect) return;

    let html = '<option value="">Select Subscription Plan</option>';
    for (const plan of subscriptionPlans) {
      html += `<option value="${plan.id}">${escapeHtml(plan.name)}</option>`;
    }
    planSelect.innerHTML = html;
  }

  // -------------------------------------------------------------------------
  // Loading & Rendering
  // -------------------------------------------------------------------------
  async function loadTenants() {
    renderLoading();
    
    try {
      const params = {
        page: currentPage,
        page_size: pageSize,
        search: currentSearch,
        status: currentStatus,
        industry: currentIndustry
      };
      
      const response = await Api.getTenants(params);
      allTenants = response.items || [];
      totalPages = response.total_pages || 1;
      currentPage = response.page || 1;
      
      renderTable(allTenants);
      updatePagination(currentPage, totalPages);
      
    } catch (err) {
      console.error('Failed to load tenants:', err);
      showToast('Unable to load tenants. Please try again.', 'error');
      renderEmptyState('Error loading data', true);
    }
  }

  function renderLoading() {
    els.tableContainer.style.display = 'block';
    els.emptyState.style.display = 'none';
    els.tableBody.innerHTML = `
      <tr><td colspan="7" style="text-align: center; padding: 48px;">
        <div style="color:var(--color-gray-500);">Loading tenants...</div>
      </td></tr>
    `;
  }

  function renderEmptyState(message = 'No tenants found', isError = false) {
    els.tableContainer.style.display = 'block';
    els.emptyState.style.display = 'none';
    
    if (isError) {
      els.tableBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; padding: 48px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="color:var(--color-error); margin-bottom: 16px;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width:48px;height:48px;"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg></div>
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${message}</h3>
            <button class="btn-secondary mt-3" onclick="TenantsModule.loadTenants()">Retry</button>
          </div>
        </td></tr>
      `;
    } else {
      els.tableBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; padding: 64px 24px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="background: var(--color-gray-100); border-radius: 50%; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; color: var(--color-gray-400);">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008Zm0 3h.008v.008h-.008v-.008Zm0 3h.008v.008h-.008v-.008Z" /></svg>
            </div>
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${message}</h3>
            <p style="font-size: 13px; color: var(--color-gray-500); margin-bottom: 16px;">There are no tenants matching this criteria.</p>
            <button class="btn-primary" onclick="TenantsModule.openCreateModal()">Add Tenant</button>
          </div>
        </td></tr>
      `;
    }
  }

  function renderTable(tenants) {
    if (tenants.length === 0) {
      renderEmptyState(currentSearch ? 'No tenants match your search' : 'No tenants found');
      return;
    }

    els.emptyState.style.display = 'none';
    els.tableContainer.style.display = 'block';
    
    let html = '';
    for (const t of tenants) {
      const createdDate = new Date(t.created_at).toLocaleDateString();
      const planName = getPlanName(t.subscription_plan_id);
      const isTenantActive = t.status === 'ACTIVE';
      
      html += `
        <tr>
          <td><strong>${escapeHtml(t.name)}</strong></td>
          <td>${escapeHtml(t.email || '—')}</td>
          <td>${escapeHtml(t.industry || '—')}</td>
          <td>${planName}</td>
          <td>
            <div class="status-toggle-wrapper" onclick="TenantsModule.toggleTenantStatus('${t.id}', '${t.status}', '${escapeHtml(t.name)}')" title="Click to toggle Active/Inactive status">
              <span class="status-toggle ${isTenantActive ? 'active' : ''}">
                <span class="status-toggle-handle"></span>
              </span>
              <span class="status-toggle-label">${isTenantActive ? 'Active' : 'Inactive'}</span>
            </div>
          </td>
          <td>${createdDate}</td>
          <td class="cell-actions" onmouseleave="this.classList.remove('active')">
            <button class="action-btn" onclick="this.parentElement.classList.toggle('active')" aria-label="Actions">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="20" height="20">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3z" />
              </svg>
            </button>
            <div class="action-dropdown">
              <button class="action-dropdown-item" onclick="TenantsModule.openDetailsModal('${t.id}')">View</button>
              <button class="action-dropdown-item" onclick="TenantsModule.openEditModal('${t.id}')">Edit</button>
              <button class="action-dropdown-item danger" onclick="TenantsModule.confirmDeleteTenant('${t.id}', '${escapeHtml(t.name)}')">Delete</button>
            </div>
          </td>
        </tr>
      `;
    }
    
    els.tableBody.innerHTML = html;
  }

  function updatePagination(page, total) {
    els.pageIndicator.textContent = `Page ${page} of ${total}`;
    if (els.btnFirst) els.btnFirst.disabled = page <= 1;
    if (els.btnPrev) els.btnPrev.disabled = page <= 1;
    if (els.btnNext) els.btnNext.disabled = page >= total;
    if (els.btnLast) els.btnLast.disabled = page >= total;
    
    const currentBtn = document.getElementById('btnTenantCurrent');
    if (currentBtn) currentBtn.textContent = page;
  }

  function getPlanName(planId) {
    if (!planId) return 'Free';
    const plan = subscriptionPlans.find(p => p.id === planId);
    return plan ? escapeHtml(plan.name) : 'Active Plan';
  }

  // -------------------------------------------------------------------------
  // Toggle Status
  // -------------------------------------------------------------------------
  async function toggleTenantStatus(id, currentStatus, tenantName) {
    const nextStatus = currentStatus === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE';
    try {
      await Api.updateTenantStatus(id, nextStatus);
      showToast(`Tenant "${tenantName}" is now ${nextStatus.toLowerCase()}.`, 'success');
      loadTenants();
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to update tenant status.', 'error');
    }
  }

  // -------------------------------------------------------------------------
  // Modals & Forms
  // -------------------------------------------------------------------------
  function resetPasswordInputTypes() {
    ['tenantPassword', 'tenantConfirmPassword'].forEach(id => {
      const input = document.getElementById(id);
      if (input) {
        input.type = 'password';
        const wrapper = input.closest('.password-input-wrapper');
        if (wrapper) {
          const eyeOpen = wrapper.querySelector('.eye-open');
          const eyeClosed = wrapper.querySelector('.eye-closed');
          if (eyeOpen) eyeOpen.style.display = 'block';
          if (eyeClosed) eyeClosed.style.display = 'none';
        }
      }
    });
  }

  function openCreateModal() {
    els.form.reset();
    els.tenantIdInput.value = '';
    els.modalTitle.textContent = 'Add Tenant';
    resetPasswordInputTypes();
    
    // Set field placeholders for create mode
    document.getElementById('tenantPassword').placeholder = 'Enter password';
    document.getElementById('tenantConfirmPassword').placeholder = 'Confirm password';
    
    populateSubscriptionPlanDropdown();
    els.modal.classList.add('active');
  }

  function openEditModal(id) {
    const tenant = allTenants.find(t => t.id === id);
    if (!tenant) return;

    els.form.reset();
    els.tenantIdInput.value = tenant.id;
    els.modalTitle.textContent = 'Edit Tenant';
    resetPasswordInputTypes();
    
    populateSubscriptionPlanDropdown();

    // Populate fields
    document.getElementById('tenantName').value = tenant.name || '';
    document.getElementById('tenantEmail').value = tenant.email || '';
    document.getElementById('tenantPhone').value = tenant.phone || '';
    document.getElementById('tenantIndustry').value = tenant.industry || '';
    document.getElementById('tenantCompanySize').value = tenant.company_size || '';
    document.getElementById('tenantSubscriptionPlan').value = tenant.subscription_plan_id || '';
    document.getElementById('tenantCountry').value = tenant.country || '';
    
    // Clear password inputs and set helper placeholder
    const passInput = document.getElementById('tenantPassword');
    const confirmInput = document.getElementById('tenantConfirmPassword');
    passInput.value = '';
    confirmInput.value = '';
    passInput.placeholder = 'Leave blank to keep unchanged';
    confirmInput.placeholder = 'Leave blank to keep unchanged';

    els.modal.classList.add('active');
  }

  function closeFormModal() {
    els.modal.classList.remove('active');
  }

  async function handleFormSubmit(e) {
    e.preventDefault();
    
    const id = els.tenantIdInput.value;
    const isEdit = !!id;
    
    const name = document.getElementById('tenantName').value.trim();
    const email = document.getElementById('tenantEmail').value.trim();
    const phone = document.getElementById('tenantPhone').value.trim();
    const industry = document.getElementById('tenantIndustry').value;
    const company_size = document.getElementById('tenantCompanySize').value;
    const subscription_plan_id = document.getElementById('tenantSubscriptionPlan').value;
    const country = document.getElementById('tenantCountry').value.trim();
    const password = document.getElementById('tenantPassword').value;
    const confirmPassword = document.getElementById('tenantConfirmPassword').value;

    // Validate password match
    if (password || confirmPassword) {
      if (password !== confirmPassword) {
        showToast('Set password and Confirm password do not match.', 'error');
        return;
      }
    }

    const data = {
      name,
      email: email || null,
      phone: phone || null,
      industry: industry || null,
      company_size: company_size || null,
      country: country || null,
    };

    if (subscription_plan_id) {
      data.subscription_plan_id = subscription_plan_id;
    }
    if (password) {
      data.password = password;
    }

    els.btnSave.disabled = true;
    els.btnSave.textContent = 'Saving...';

    try {
      let savedTenant;
      if (isEdit) {
        savedTenant = await Api.updateTenant(id, data);
        if (subscription_plan_id && subscription_plan_id !== (allTenants.find(t => t.id === id)?.subscription_plan_id)) {
          await Api.assignSubscriptionPlan(id, subscription_plan_id);
        }
        showToast('Tenant updated successfully.', 'success');
      } else {
        savedTenant = await Api.createTenant(data);
        if (subscription_plan_id && savedTenant && savedTenant.id) {
          await Api.assignSubscriptionPlan(savedTenant.id, subscription_plan_id);
        }
        showToast('Tenant created successfully.', 'success');
      }
      
      closeFormModal();
      loadTenants(); // Refresh
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to save tenant.', 'error');
    } finally {
      els.btnSave.disabled = false;
      els.btnSave.textContent = 'Save Tenant';
    }
  }

  // -------------------------------------------------------------------------
  // Tenant Details
  // -------------------------------------------------------------------------
  async function openDetailsModal(id) {
    try {
      const tenant = await Api.getTenant(id);
      const isTenantActive = tenant.status === 'ACTIVE';
      const planName = getPlanName(tenant.subscription_plan_id);
      
      let html = `
        <div class="detail-section">
          <div class="detail-section-title">Organization Information</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Tenant Name</span>
              <span class="detail-value">${escapeHtml(tenant.name)}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Email</span>
              <span class="detail-value">${escapeHtml(tenant.email || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Phone</span>
              <span class="detail-value">${escapeHtml(tenant.phone || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Industry</span>
              <span class="detail-value">${escapeHtml(tenant.industry || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Company Size</span>
              <span class="detail-value">${escapeHtml(tenant.company_size || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Country</span>
              <span class="detail-value">${escapeHtml(tenant.country || '—')}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <div class="detail-section-title">Status & Subscription</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Status</span>
              <span class="detail-value">
                <span class="status-toggle-wrapper" style="pointer-events:none;">
                  <span class="status-toggle ${isTenantActive ? 'active' : ''}">
                    <span class="status-toggle-handle"></span>
                  </span>
                  <span class="status-toggle-label">${isTenantActive ? 'Active' : 'Inactive'}</span>
                </span>
              </span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Subscription Plan</span>
              <span class="detail-value">${planName}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Created At</span>
              <span class="detail-value">${new Date(tenant.created_at).toLocaleString()}</span>
            </div>
          </div>
        </div>
      `;

      els.detailsContent.innerHTML = html;
      els.detailsModal.classList.add('active');
    } catch (err) {
      console.error(err);
      showToast('Unable to load tenant details.', 'error');
    }
  }

  function closeDetailsModal() {
    els.detailsModal.classList.remove('active');
  }

  // -------------------------------------------------------------------------
  // Action Handlers: Delete Tenant
  // -------------------------------------------------------------------------
  let pendingDelete = null;

  function confirmDeleteTenant(id, name) {
    pendingDelete = { id, name };
    
    els.confirmTitle.textContent = 'Delete Tenant';
    els.confirmText.innerHTML = `Are you sure you want to delete <strong>"${escapeHtml(name)}"</strong>? This action cannot be undone.`;
    
    els.btnConfirm.className = 'btn-danger';
    els.btnConfirm.textContent = 'Delete Tenant';
    
    els.confirmModal.classList.add('active');
  }

  function closeConfirmModal() {
    els.confirmModal.classList.remove('active');
    pendingDelete = null;
  }

  async function executeStatusChange() {
    if (!pendingDelete) return;
    
    const { id, name } = pendingDelete;
    els.btnConfirm.disabled = true;
    
    try {
      await Api.deleteTenant(id);
      showToast(`Tenant "${name}" deleted successfully.`, 'success');
      closeConfirmModal();
      loadTenants(); // Refresh table
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to delete tenant.', 'error');
    } finally {
      els.btnConfirm.disabled = false;
    }
  }

  // -------------------------------------------------------------------------
  // Toast Notifications
  // -------------------------------------------------------------------------
  function showToast(message, type = 'success') {
    if (!els.toastContainer) return;
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    const icon = type === 'success' 
      ? '<svg class="toast-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>'
      : '<svg class="toast-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>';
      
    toast.innerHTML = `
      ${icon}
      <span>${escapeHtml(message)}</span>
    `;
    
    els.toastContainer.appendChild(toast);
    
    // Trigger animation
    setTimeout(() => toast.classList.add('show'), 10);
    
    // Remove after 3 seconds
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 300);
    }, 3000);
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

  // Toggle Password Visibility (Eye Icon)
  function togglePasswordVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const eyeOpen = btn.querySelector('.eye-open');
    const eyeClosed = btn.querySelector('.eye-closed');

    if (input.type === 'password') {
      input.type = 'text';
      if (eyeOpen) eyeOpen.style.display = 'none';
      if (eyeClosed) eyeClosed.style.display = 'block';
    } else {
      input.type = 'password';
      if (eyeOpen) eyeOpen.style.display = 'block';
      if (eyeClosed) eyeClosed.style.display = 'none';
    }
  }

  // Public API
  return {
    init,
    loadTenants,
    openCreateModal,
    openEditModal,
    closeFormModal,
    openDetailsModal,
    closeDetailsModal,
    toggleTenantStatus,
    confirmDeleteTenant,
    closeConfirmModal,
    executeStatusChange,
    togglePasswordVisibility
  };
})();

document.addEventListener('DOMContentLoaded', TenantsModule.init);
