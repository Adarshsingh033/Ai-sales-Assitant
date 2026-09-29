/**
 * Tenant Management Module
 * Handles loading, rendering, searching, creating, editing and status updates for Tenants.
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

  // DOM Elements
  const els = {
    // List & Toolbar
    tableBody: document.getElementById('tenantTableBody'),
    searchInput: document.getElementById('tenantSearch'),
    statusFilter: document.getElementById('tenantStatusFilter'),
    industryFilter: document.getElementById('tenantIndustryFilter'),
    btnPrev: document.getElementById('btnTenantPrev'),
    btnNext: document.getElementById('btnTenantNext'),
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

    els.btnPrev.addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        loadTenants();
      }
    });

    els.btnNext.addEventListener('click', () => {
      if (currentPage < totalPages) {
        currentPage++;
        loadTenants();
      }
    });

    // Form Submit
    els.form.addEventListener('submit', handleFormSubmit);

    // Initial Load
    loadTenants();
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
    els.tableContainer.style.display = 'none';
    els.emptyState.style.display = 'flex';
    els.emptyState.innerHTML = `
      <div class="empty-state-icon"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" /></svg></div>
      <h3>Loading tenants...</h3>
    `;
  }

  function renderEmptyState(message = 'No tenants found', isError = false) {
    els.tableContainer.style.display = 'none';
    els.emptyState.style.display = 'flex';
    
    if (isError) {
      els.emptyState.innerHTML = `
        <div class="empty-state-icon" style="color:var(--color-error)"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg></div>
        <h3>${message}</h3>
        <button class="btn-secondary mt-3" onclick="TenantsModule.loadTenants()">Retry</button>
      `;
    } else {
      els.emptyState.innerHTML = `
        <div class="empty-state-icon"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008Zm0 3h.008v.008h-.008v-.008Zm0 3h.008v.008h-.008v-.008Z" /></svg></div>
        <h3>${message}</h3>
        <p>There are no tenants matching this criteria.</p>
        <button class="btn-primary mt-3" onclick="TenantsModule.openCreateModal()">Add Tenant</button>
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
      const statusBadge = getStatusBadge(t.status);
      
      html += `
        <tr>
          <td><strong>${escapeHtml(t.name)}</strong></td>
          <td>${escapeHtml(t.email || '—')}</td>
          <td>${escapeHtml(t.industry || '—')}</td>
          <td>${t.subscription_plan_id ? 'Active Plan' : 'Free'}</td>
          <td>${statusBadge}</td>
          <td>${createdDate}</td>
          <td class="cell-actions" onmouseleave="this.classList.remove('active')">
            <button class="action-btn" onclick="this.parentElement.classList.toggle('active')">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.75a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5ZM12 12.75a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5ZM12 18.75a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5Z" />
              </svg>
            </button>
            <div class="action-dropdown">
              <button class="action-dropdown-item" onclick="TenantsModule.openDetailsModal('${t.id}')">View Details</button>
              <button class="action-dropdown-item" onclick="TenantsModule.openEditModal('${t.id}')">Edit</button>
              ${t.status !== 'SUSPENDED' ? `
              <button class="action-dropdown-item danger" onclick="TenantsModule.confirmStatusChange('${t.id}', 'SUSPENDED', '${escapeHtml(t.name)}')">Suspend</button>
              ` : `
              <button class="action-dropdown-item" onclick="TenantsModule.confirmStatusChange('${t.id}', 'ACTIVE', '${escapeHtml(t.name)}')">Activate</button>
              `}
            </div>
          </td>
        </tr>
      `;
    }
    
    els.tableBody.innerHTML = html;
  }

  function updatePagination(page, total) {
    els.pageIndicator.textContent = `${page} of ${total}`;
    els.btnPrev.disabled = page <= 1;
    els.btnNext.disabled = page >= total;
  }

  function getStatusBadge(status) {
    switch (status) {
      case 'ACTIVE': return '<span class="status-badge status-active">Active</span>';
      case 'INACTIVE': return '<span class="status-badge status-inactive">Inactive</span>';
      case 'SUSPENDED': return '<span class="status-badge status-suspended">Suspended</span>';
      default: return `<span class="status-badge">${status}</span>`;
    }
  }

  // -------------------------------------------------------------------------
  // Modals & Forms
  // -------------------------------------------------------------------------
  function openCreateModal() {
    els.form.reset();
    els.tenantIdInput.value = '';
    els.modalTitle.textContent = 'Add Tenant';
    els.modal.classList.add('active');
  }

  function openEditModal(id) {
    const tenant = allTenants.find(t => t.id === id);
    if (!tenant) return;

    els.form.reset();
    els.tenantIdInput.value = tenant.id;
    els.modalTitle.textContent = 'Edit Tenant';
    
    // Populate fields
    document.getElementById('tenantName').value = tenant.name || '';
    document.getElementById('tenantLegalName').value = tenant.legal_name || '';
    document.getElementById('tenantEmail').value = tenant.email || '';
    document.getElementById('tenantPhone').value = tenant.phone || '';
    document.getElementById('tenantWebsite').value = tenant.website || '';
    document.getElementById('tenantIndustry').value = tenant.industry || '';
    document.getElementById('tenantCompanySize').value = tenant.company_size || '';
    document.getElementById('tenantCountry').value = tenant.country || '';
    document.getElementById('tenantTimezone').value = tenant.timezone || '';
    
    els.modal.classList.add('active');
  }

  function closeFormModal() {
    els.modal.classList.remove('active');
  }

  async function handleFormSubmit(e) {
    e.preventDefault();
    
    const id = els.tenantIdInput.value;
    const isEdit = !!id;
    
    const data = {
      name: document.getElementById('tenantName').value.trim(),
      legal_name: document.getElementById('tenantLegalName').value.trim() || null,
      email: document.getElementById('tenantEmail').value.trim() || null,
      phone: document.getElementById('tenantPhone').value.trim() || null,
      website: document.getElementById('tenantWebsite').value.trim() || null,
      industry: document.getElementById('tenantIndustry').value.trim() || null,
      company_size: document.getElementById('tenantCompanySize').value.trim() || null,
      country: document.getElementById('tenantCountry').value.trim() || null,
      timezone: document.getElementById('tenantTimezone').value.trim() || null,
    };

    els.btnSave.disabled = true;
    els.btnSave.textContent = 'Saving...';

    try {
      if (isEdit) {
        await Api.updateTenant(id, data);
        showToast('Tenant updated successfully.', 'success');
      } else {
        await Api.createTenant(data);
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
      
      let html = `
        <div class="detail-section">
          <div class="detail-section-title">Company Information</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Tenant Name</span>
              <span class="detail-value">${escapeHtml(tenant.name)}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Legal Name</span>
              <span class="detail-value">${escapeHtml(tenant.legal_name || '—')}</span>
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
              <span class="detail-label">Website</span>
              <span class="detail-value">${escapeHtml(tenant.website || '—')}</span>
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
          <div class="detail-section-title">Status & System</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Status</span>
              <span class="detail-value">${getStatusBadge(tenant.status)}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Subscription</span>
              <span class="detail-value">${tenant.subscription_plan_id ? 'Active Plan' : 'Free / None'}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Created At</span>
              <span class="detail-value">${new Date(tenant.created_at).toLocaleString()}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Tenant ID</span>
              <span class="detail-value" style="font-size:11px; font-family:monospace; color:var(--color-gray-500)">${tenant.id}</span>
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
  // Status Changes
  // -------------------------------------------------------------------------
  let pendingStatusChange = null;

  function confirmStatusChange(id, newStatus, name) {
    pendingStatusChange = { id, status: newStatus };
    
    let actionText = newStatus === 'SUSPENDED' ? 'suspend' : 'activate';
    els.confirmTitle.textContent = `${actionText.charAt(0).toUpperCase() + actionText.slice(1)} Tenant`;
    els.confirmText.innerHTML = `Are you sure you want to <strong>${actionText}</strong> "${name}"? ${newStatus === 'SUSPENDED' ? 'They will not be able to log in.' : ''}`;
    
    // Adjust button style based on action
    if (newStatus === 'SUSPENDED') {
      els.btnConfirm.className = 'btn-danger';
      els.btnConfirm.textContent = 'Suspend Tenant';
    } else {
      els.btnConfirm.className = 'btn-primary';
      els.btnConfirm.textContent = 'Activate Tenant';
    }
    
    els.confirmModal.classList.add('active');
  }

  function closeConfirmModal() {
    els.confirmModal.classList.remove('active');
    pendingStatusChange = null;
  }

  async function executeStatusChange() {
    if (!pendingStatusChange) return;
    
    const { id, status } = pendingStatusChange;
    els.btnConfirm.disabled = true;
    
    try {
      await Api.updateTenantStatus(id, status);
      showToast(`Tenant status updated to ${status}.`, 'success');
      closeConfirmModal();
      loadTenants(); // Refresh
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to update status.', 'error');
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

  // Public API
  return {
    init,
    loadTenants,
    openCreateModal,
    openEditModal,
    closeFormModal,
    openDetailsModal,
    closeDetailsModal,
    confirmStatusChange,
    closeConfirmModal,
    executeStatusChange
  };
})();

document.addEventListener('DOMContentLoaded', TenantsModule.init);
