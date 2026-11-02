/**
 * Sales User Management Module (Tenant Admin)
 * Handles loading, rendering, searching, filtering, sorting, creating, editing,
 * activating/deactivating, resetting passwords, and deleting Sales Users.
 */

const SalesUsersModule = (() => {
  // State
  let currentPage = 1;
  let pageSize = 10;
  let totalPages = 1;
  let currentSearch = '';
  let currentStatus = '';
  let currentAvailability = '';
  let currentSortBy = 'created_at';
  let currentSortOrder = 'desc';
  let allUsers = [];
  let pendingAction = null;
  let roles = []; // roles an admin has created: the Role dropdown lists exactly these

  // DOM Elements
  const els = {
    // List & Toolbar
    tableBody: document.getElementById('salesUserTableBody'),
    searchInput: document.getElementById('salesUserSearch'),
    statusFilter: document.getElementById('salesUserStatusFilter'),
    availabilityFilter: document.getElementById('salesUserAvailabilityFilter'),
    sortSelect: document.getElementById('salesUserSort'),
    pageSizeSelect: document.getElementById('salesUserPageSize'),
    btnFirst: document.getElementById('btnSalesUserFirst'),
    btnPrev: document.getElementById('btnSalesUserPrev'),
    btnNext: document.getElementById('btnSalesUserNext'),
    btnLast: document.getElementById('btnSalesUserLast'),
    btnCurrent: document.getElementById('btnSalesUserCurrent'),
    pageIndicator: document.getElementById('salesUserPageIndicator'),
    tableContainer: document.getElementById('salesUserTableContainer'),

    // Create/Edit Modal
    modal: document.getElementById('salesUserModal'),
    modalTitle: document.getElementById('salesUserModalTitle'),
    form: document.getElementById('salesUserForm'),
    btnSave: document.getElementById('btnSalesUserSave'),
    userIdInput: document.getElementById('formSalesUserId'),
    passwordRow: document.getElementById('salesUserPasswordRow'),
    roleSelect: document.getElementById('salesUserRole'),
    roleHint: document.getElementById('salesUserRoleHint'),

    // Details Modal
    detailsModal: document.getElementById('salesUserDetailsModal'),
    detailsContent: document.getElementById('salesUserDetailsContent'),

    // Reset Password Modal
    resetModal: document.getElementById('salesUserResetPasswordModal'),
    resetForm: document.getElementById('salesUserResetPasswordForm'),
    resetIdInput: document.getElementById('resetSalesUserId'),
    resetText: document.getElementById('salesUserResetText'),
    btnResetSave: document.getElementById('btnSalesUserResetSave'),

    // Confirmation Modal
    confirmModal: document.getElementById('confirmModal'),
    confirmTitle: document.getElementById('confirmTitle'),
    confirmText: document.getElementById('confirmText'),
    btnConfirm: document.getElementById('btnConfirmAction'),
  };

  // -------------------------------------------------------------------------
  // Initialization (data is loaded when the section is opened)
  // -------------------------------------------------------------------------
  function init() {
    if (!els.tableBody) return;

    let debounceTimer;
    els.searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        currentSearch = e.target.value.trim();
        currentPage = 1;
        loadUsers();
      }, 300);
    });

    els.statusFilter.addEventListener('change', (e) => {
      currentStatus = e.target.value;
      currentPage = 1;
      loadUsers();
    });

    els.availabilityFilter.addEventListener('change', (e) => {
      currentAvailability = e.target.value;
      currentPage = 1;
      loadUsers();
    });

    els.sortSelect.addEventListener('change', (e) => {
      const [sortBy, sortOrder] = e.target.value.split(':');
      currentSortBy = sortBy;
      currentSortOrder = sortOrder;
      currentPage = 1;
      loadUsers();
    });

    els.pageSizeSelect.addEventListener('change', (e) => {
      pageSize = parseInt(e.target.value, 10) || 10;
      currentPage = 1;
      loadUsers();
    });

    els.btnFirst.addEventListener('click', () => goToPage(1));
    els.btnPrev.addEventListener('click', () => goToPage(currentPage - 1));
    els.btnNext.addEventListener('click', () => goToPage(currentPage + 1));
    els.btnLast.addEventListener('click', () => goToPage(totalPages));

    els.form.addEventListener('submit', handleFormSubmit);
    els.resetForm.addEventListener('submit', handleResetPasswordSubmit);
    els.roleSelect.addEventListener('change', showRoleHint);

    // roles were added, renamed or removed in the "Manage roles" dialog
    Crm.on('roles-changed', async () => {
      await loadRoles();
      if (els.modal.classList.contains('active')) fillRoleSelect(els.roleSelect.value);
      if (els.tableContainer.offsetParent) loadUsers();
    });

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.cell-actions')) {
        document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));
      }
    });
  }

  function goToPage(page) {
    if (page < 1 || page > totalPages || page === currentPage) return;
    currentPage = page;
    loadUsers();
  }

  // -------------------------------------------------------------------------
  // Loading & Rendering
  // -------------------------------------------------------------------------
  async function loadUsers() {
    renderLoading();

    try {
      const response = await Api.getSalesUsers({
        page: currentPage,
        page_size: pageSize,
        search: currentSearch,
        is_active: currentStatus,
        is_available: currentAvailability,
        sort_by: currentSortBy,
        sort_order: currentSortOrder,
      });
      allUsers = response.items || [];
      totalPages = response.total_pages || 1;
      currentPage = response.page || 1;

      renderTable(allUsers);
      updatePagination(currentPage, totalPages);
    } catch (err) {
      console.error('Failed to load sales users:', err);
      showToast(err.message || 'Unable to load users. Please try again.', 'error');
      renderEmptyState('Error loading data', true);
    }
  }

  function renderLoading() {
    els.tableContainer.style.display = 'block';
    els.tableBody.innerHTML = `
      <tr><td colspan="8" style="text-align: center; padding: 48px;">
        <div style="color:var(--color-gray-500);">Loading users...</div>
      </td></tr>
    `;
  }

  function renderEmptyState(message = 'No team members found', isError = false) {
    els.tableContainer.style.display = 'block';

    if (isError) {
      els.tableBody.innerHTML = `
        <tr><td colspan="8" style="text-align: center; padding: 48px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${escapeHtml(message)}</h3>
            <button class="btn-secondary mt-3" onclick="SalesUsersModule.loadUsers()">Retry</button>
          </div>
        </td></tr>
      `;
      return;
    }

    els.tableBody.innerHTML = `
      <tr><td colspan="8" style="text-align: center; padding: 64px 24px;">
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
          <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${escapeHtml(message)}</h3>
          <p style="font-size: 13px; color: var(--color-gray-500); margin-bottom: 16px;">There are no team members matching this criteria.</p>
          <button class="btn-primary" onclick="SalesUsersModule.openCreateModal()">Add team member</button>
        </div>
      </td></tr>
    `;
  }

  function renderTable(users) {
    if (users.length === 0) {
      const filtered = currentSearch || currentStatus || currentAvailability;
      renderEmptyState(filtered ? 'No team members match your filters' : 'No team members yet');
      return;
    }

    els.tableContainer.style.display = 'block';

    let html = '';
    for (const u of users) {
      const fullName = `${u.first_name} ${u.last_name}`;
      const lastLogin = u.last_login_at ? new Date(u.last_login_at).toLocaleString() : 'Never';
      const isAdmin = u.role === 'TENANT_ADMIN';
      // Admins and roles without automatic deals are not part of the lead rotation, so availability does not apply
      const availability = (isAdmin || !u.receives_deals)
        ? `<span class="team-muted" title="Not given deals automatically">—</span>`
        : (u.is_available ? 'Available' : 'Unavailable');
      const toggleAttrs = u.is_me
        ? 'class="status-toggle-wrapper locked" title="You can’t deactivate your own account"'
        : `class="status-toggle-wrapper" onclick="SalesUsersModule.toggleUserStatus('${u.id}')" title="Click to toggle Active/Inactive status"`;
      const ownAccountMenu = u.is_me ? '' : `
              <button class="action-dropdown-item" onclick="SalesUsersModule.handleAction(event, 'reset-password', '${u.id}')">Reset Password</button>
              <button class="action-dropdown-item danger" onclick="SalesUsersModule.handleAction(event, 'delete', '${u.id}')">Delete</button>`;

      html += `
        <tr>
          <td><strong>${escapeHtml(fullName)}</strong>${u.is_me ? '<span class="team-you">(you)</span>' : ''}</td>
          <td>${escapeHtml(u.email)}</td>
          <td>${escapeHtml(u.role_name || '—')}${isAdmin ? '<span class="team-role-pill" title="Full access to the workspace">Admin access</span>' : ''}</td>
          <td>${escapeHtml(u.phone_number || '—')}</td>
          <td>${availability}</td>
          <td>
            <div ${toggleAttrs}>
              <span class="status-toggle ${u.is_active ? 'active' : ''}">
                <span class="status-toggle-handle"></span>
              </span>
              <span class="status-toggle-label">${u.is_active ? 'Active' : 'Inactive'}</span>
            </div>
          </td>
          <td>${escapeHtml(lastLogin)}</td>
          <td class="cell-actions">
            <button class="action-btn" onclick="SalesUsersModule.toggleActionMenu(event)" aria-label="Actions">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="20" height="20">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm0 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3z" />
              </svg>
            </button>
            <div class="action-dropdown">
              <button class="action-dropdown-item" onclick="SalesUsersModule.handleAction(event, 'view', '${u.id}')">View</button>
              <button class="action-dropdown-item" onclick="SalesUsersModule.handleAction(event, 'edit', '${u.id}')">Edit</button>${ownAccountMenu}
            </div>
          </td>
        </tr>
      `;
    }

    els.tableBody.innerHTML = html;
  }

  function updatePagination(page, total) {
    els.pageIndicator.textContent = `Page ${page} of ${total}`;
    els.btnFirst.disabled = page <= 1;
    els.btnPrev.disabled = page <= 1;
    els.btnNext.disabled = page >= total;
    els.btnLast.disabled = page >= total;
    if (els.btnCurrent) els.btnCurrent.textContent = page;
  }

  function findUser(id) {
    return allUsers.find(u => u.id === id);
  }

  function displayName(user) {
    return user ? `${user.first_name} ${user.last_name}` : 'this user';
  }

  // -------------------------------------------------------------------------
  // Activate / Deactivate
  // -------------------------------------------------------------------------
  function toggleUserStatus(id) {
    const user = findUser(id);
    if (!user || user.is_me) return;

    if (user.is_active) {
      openConfirm({
        type: 'deactivate',
        id,
        title: 'Deactivate User',
        html: `Deactivate <strong>"${escapeHtml(displayName(user))}"</strong>? They will be signed out immediately, will no longer be able to log in, and will be marked unavailable.`,
        buttonText: 'Deactivate',
        danger: true,
      });
    } else {
      changeStatus(id, true);
    }
  }

  async function changeStatus(id, isActive) {
    const user = findUser(id);
    try {
      const res = await Api.updateSalesUserStatus(id, isActive);
      showToast(`User "${displayName(user)}" is now ${isActive ? 'active' : 'inactive'}.`, 'success');
      if (!isActive && res && res.has_open_leads) {
        showToast(`This user has ${res.open_leads_count} open lead(s) that need reassignment.`, 'error');
      }
      loadUsers();
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Failed to update user status.', 'error');
    }
  }

  // -------------------------------------------------------------------------
  // Create / Edit
  // -------------------------------------------------------------------------
  function resetPasswordInputTypes() {
    ['salesUserPassword', 'salesUserConfirmPassword', 'salesUserNewPassword', 'salesUserNewPasswordConfirm'].forEach(id => {
      const input = document.getElementById(id);
      if (!input) return;
      input.type = 'password';
      const wrapper = input.closest('.password-input-wrapper');
      if (wrapper) {
        const eyeOpen = wrapper.querySelector('.eye-open');
        const eyeClosed = wrapper.querySelector('.eye-closed');
        if (eyeOpen) eyeOpen.style.display = 'block';
        if (eyeClosed) eyeClosed.style.display = 'none';
      }
    });
  }

  function setPasswordFieldsEnabled(enabled) {
    els.passwordRow.style.display = enabled ? '' : 'none';
    ['salesUserPassword', 'salesUserConfirmPassword'].forEach(id => {
      const input = document.getElementById(id);
      input.required = enabled;
      input.disabled = !enabled;
    });
  }

  // ---- roles: the dropdown lists exactly the roles the admin created (plus the two built-in ones) ----
  async function loadRoles() {
    try {
      roles = await Api.getRoles();
    } catch (err) {
      console.error(err);
      roles = [];
      showToast('Could not load the roles. Please try again.', 'error');
    }
    return roles;
  }

  function builtInRoleId(access) {
    const role = roles.find((r) => r.is_system && r.access === access);
    return role ? role.id : '';
  }

  function fillRoleSelect(selectedId) {
    els.roleSelect.innerHTML = roles
      .map((r) => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.name)}${r.access === 'ADMIN' ? ' (admin access)' : ''}</option>`)
      .join('');
    if (selectedId && roles.some((r) => r.id === selectedId)) els.roleSelect.value = selectedId;
    showRoleHint();
  }

  function showRoleHint() {
    const role = roles.find((r) => r.id === els.roleSelect.value);
    if (!role) { els.roleHint.textContent = ''; return; }
    els.roleHint.textContent = role.access === 'ADMIN'
      ? 'Full access to everything in the workspace. Never gets automatic deals.'
      : `Sees only their own deals, chats and tasks. ${role.receives_deals ? 'Gets new deals automatically.' : 'Does not get automatic deals.'}`;
  }

  async function openCreateModal() {
    await loadRoles();
    els.form.reset();
    els.userIdInput.value = '';
    els.modalTitle.textContent = 'Add team member';
    els.btnSave.textContent = 'Save User';
    document.getElementById('salesUserEmail').disabled = false;
    fillRoleSelect(builtInRoleId('SALES'));
    els.roleSelect.disabled = false;
    els.roleSelect.title = '';
    els.form.dataset.originalRole = '';
    setPasswordFieldsEnabled(true);
    resetPasswordInputTypes();
    els.modal.classList.add('active');
  }

  async function openEditModal(id) {
    const user = findUser(id);
    if (!user) return;

    await loadRoles();
    els.form.reset();
    els.userIdInput.value = user.id;
    els.modalTitle.textContent = 'Edit team member';
    els.btnSave.textContent = 'Save User';
    setPasswordFieldsEnabled(false);

    document.getElementById('salesUserFirstName').value = user.first_name || '';
    document.getElementById('salesUserLastName').value = user.last_name || '';
    const emailInput = document.getElementById('salesUserEmail');
    emailInput.value = user.email || '';
    emailInput.disabled = true;
    document.getElementById('salesUserPhone').value = user.phone_number || '';
    document.getElementById('salesUserAvailable').value = String(user.is_available);

    const current = user.role_id || builtInRoleId(user.role === 'TENANT_ADMIN' ? 'ADMIN' : 'SALES');
    fillRoleSelect(current);
    els.form.dataset.originalRole = current;
    els.form.dataset.originalLevel = user.role;
    // nobody can change their own access (an admin could lock themselves out)
    els.roleSelect.disabled = !!user.is_me;
    els.roleSelect.title = user.is_me ? 'You can’t change your own role' : '';

    els.modal.classList.add('active');
  }

  function closeFormModal() {
    els.modal.classList.remove('active');
  }

  async function handleFormSubmit(e) {
    e.preventDefault();

    const id = els.userIdInput.value;
    const isEdit = !!id;

    const first_name = document.getElementById('salesUserFirstName').value.trim();
    const last_name = document.getElementById('salesUserLastName').value.trim();
    const email = document.getElementById('salesUserEmail').value.trim();
    const phone_number = document.getElementById('salesUserPhone').value.trim();
    const is_available = document.getElementById('salesUserAvailable').value === 'true';

    if (!first_name || !last_name) {
      showToast('First name and last name are required.', 'error');
      return;
    }

    const role_id = els.roleSelect.value;
    if (!els.roleSelect.disabled && !role_id) {
      showToast('Choose a role. You can create roles with "Manage roles".', 'error');
      return;
    }

    let data;
    if (isEdit) {
      data = { first_name, last_name, phone_number: phone_number || null, is_available };
      if (!els.roleSelect.disabled && role_id !== els.form.dataset.originalRole) {
        data.role_id = role_id;
        const picked = roles.find((r) => r.id === role_id);
        const level = picked && picked.access === 'ADMIN' ? 'TENANT_ADMIN' : 'SALES_USER';
        if (level !== els.form.dataset.originalLevel) {
          const who = `${first_name} ${last_name}`.trim();
          const sure = await Crm.confirm(
            level === 'TENANT_ADMIN'
              ? `${who} will get full access to your workspace: all deals, the team and settings. They will be signed out once and need to log in again.`
              : `${who} will only see their own deals, chats and tasks, and lose admin access. They will be signed out once and need to log in again.`,
            { title: 'Change access?', confirmText: 'Yes, change access' },
          );
          if (!sure) return;
        }
      }
    } else {
      const password = document.getElementById('salesUserPassword').value;
      const confirmPassword = document.getElementById('salesUserConfirmPassword').value;
      if (password.length < 8) {
        showToast('Password must be at least 8 characters long.', 'error');
        return;
      }
      if (password !== confirmPassword) {
        showToast('Set password and Confirm password do not match.', 'error');
        return;
      }
      data = { first_name, last_name, email, phone_number: phone_number || null, is_available, password, role_id };
    }

    els.btnSave.disabled = true;
    els.btnSave.textContent = 'Saving...';

    try {
      if (isEdit) {
        await Api.updateSalesUser(id, data);
        showToast('Team member updated.', 'success');
      } else {
        await Api.createSalesUser(data);
        showToast('Team member added.', 'success');
      }
      closeFormModal();
      loadUsers();
    } catch (err) {
      console.error(err);
      showToast(formatError(err, 'Failed to save team member.'), 'error');
    } finally {
      els.btnSave.disabled = false;
      els.btnSave.textContent = 'Save User';
    }
  }

  // -------------------------------------------------------------------------
  // Details
  // -------------------------------------------------------------------------
  async function openDetailsModal(id) {
    try {
      const user = await Api.getSalesUser(id);
      const detailRow = (label, value) => `
        <div class="detail-row">
          <span class="detail-label">${label}</span>
          <span class="detail-value">${escapeHtml(value)}</span>
        </div>`;

      els.detailsContent.innerHTML = `
        <div class="detail-section">
          <div class="detail-section-title">Profile</div>
          <div class="detail-grid">
            ${detailRow('Name', `${user.first_name} ${user.last_name}`)}
            ${detailRow('Email', user.email)}
            ${detailRow('Phone', user.phone_number || '—')}
            ${detailRow('Role', user.role_name || '—')}
          </div>
        </div>
        <div class="detail-section">
          <div class="detail-section-title">Status</div>
          <div class="detail-grid">
            ${detailRow('Status', user.is_active ? 'Active' : 'Inactive')}
            ${detailRow('Availability', user.is_available ? 'Available' : 'Unavailable')}
            ${detailRow('Last Login', user.last_login_at ? new Date(user.last_login_at).toLocaleString() : 'Never')}
            ${detailRow('Created At', new Date(user.created_at).toLocaleString())}
          </div>
        </div>
      `;
      els.detailsModal.classList.add('active');
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Unable to load user details.', 'error');
    }
  }

  function closeDetailsModal() {
    els.detailsModal.classList.remove('active');
  }

  // -------------------------------------------------------------------------
  // Reset Password
  // -------------------------------------------------------------------------
  function openResetPasswordModal(id) {
    const user = findUser(id);
    if (!user) return;
    els.resetForm.reset();
    resetPasswordInputTypes();
    els.resetIdInput.value = id;
    els.resetText.textContent = `Set a new password for ${displayName(user)}. All of their active sessions will be signed out.`;
    els.resetModal.classList.add('active');
  }

  function closeResetPasswordModal() {
    els.resetModal.classList.remove('active');
  }

  async function handleResetPasswordSubmit(e) {
    e.preventDefault();
    const id = els.resetIdInput.value;
    const newPassword = document.getElementById('salesUserNewPassword').value;
    const confirmPassword = document.getElementById('salesUserNewPasswordConfirm').value;

    if (newPassword.length < 8) {
      showToast('Password must be at least 8 characters long.', 'error');
      return;
    }
    if (newPassword !== confirmPassword) {
      showToast('New password and Confirm password do not match.', 'error');
      return;
    }

    els.btnResetSave.disabled = true;
    els.btnResetSave.textContent = 'Resetting...';
    try {
      await Api.resetSalesUserPassword(id, newPassword);
      showToast('Password reset successfully.', 'success');
      closeResetPasswordModal();
    } catch (err) {
      console.error(err);
      showToast(formatError(err, 'Failed to reset password.'), 'error');
    } finally {
      els.btnResetSave.disabled = false;
      els.btnResetSave.textContent = 'Reset Password';
    }
  }

  // -------------------------------------------------------------------------
  // Confirmation (Deactivate / Delete)
  // -------------------------------------------------------------------------
  function openConfirm({ type, id, title, html, buttonText, danger }) {
    pendingAction = { type, id };
    els.confirmTitle.textContent = title;
    els.confirmText.innerHTML = html;
    els.btnConfirm.className = danger ? 'btn-danger' : 'btn-primary';
    els.btnConfirm.textContent = buttonText;
    els.confirmModal.classList.add('active');
  }

  function confirmDeleteUser(id) {
    const user = findUser(id);
    openConfirm({
      type: 'delete',
      id,
      title: 'Delete User',
      html: `Are you sure you want to delete <strong>"${escapeHtml(displayName(user))}"</strong>? This action cannot be undone.`,
      buttonText: 'Delete User',
      danger: true,
    });
  }

  function closeConfirmModal() {
    els.confirmModal.classList.remove('active');
    pendingAction = null;
  }

  async function executeConfirmedAction() {
    if (!pendingAction) return;
    const { type, id } = pendingAction;
    const user = findUser(id);
    els.btnConfirm.disabled = true;

    try {
      if (type === 'deactivate') {
        closeConfirmModal();
        await changeStatus(id, false);
      } else if (type === 'delete') {
        await Api.deleteSalesUser(id);
        showToast(`User "${displayName(user)}" deleted successfully.`, 'success');
        closeConfirmModal();
        if (allUsers.length === 1 && currentPage > 1) currentPage--;
        loadUsers();
      }
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Action failed.', 'error');
    } finally {
      els.btnConfirm.disabled = false;
    }
  }

  // -------------------------------------------------------------------------
  // Utilities
  // -------------------------------------------------------------------------
  function formatError(err, fallback) {
    const detail = err && err.data && err.data.detail;
    if (Array.isArray(detail)) {
      return detail.map(d => {
        const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : '';
        return field ? `${field}: ${d.msg}` : d.msg;
      }).join('; ');
    }
    return (err && typeof err.message === 'string' && err.message !== '[object Object]') ? err.message : fallback;
  }

  function showToast(message, type = 'success') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
    }
  }

  function escapeHtml(unsafe) {
    if (unsafe === null || unsafe === undefined) return '';
    return unsafe.toString()
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function togglePasswordVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const eyeOpen = btn.querySelector('.eye-open');
    const eyeClosed = btn.querySelector('.eye-closed');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    if (eyeOpen) eyeOpen.style.display = show ? 'none' : 'block';
    if (eyeClosed) eyeClosed.style.display = show ? 'block' : 'none';
  }

  function toggleActionMenu(event) {
    if (event) event.stopPropagation();
    const cell = event ? event.currentTarget.closest('.cell-actions') : null;
    if (!cell) return;
    const isCurrentlyActive = cell.classList.contains('active');
    document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));
    if (!isCurrentlyActive) cell.classList.add('active');
  }

  function handleAction(event, action, userId) {
    if (event) event.stopPropagation();
    document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));

    if (action === 'view') {
      openDetailsModal(userId);
    } else if (action === 'edit') {
      openEditModal(userId);
    } else if (action === 'reset-password') {
      openResetPasswordModal(userId);
    } else if (action === 'delete') {
      confirmDeleteUser(userId);
    }
  }

  // Public API
  return {
    init,
    loadUsers,
    openCreateModal,
    openEditModal,
    closeFormModal,
    openDetailsModal,
    closeDetailsModal,
    closeResetPasswordModal,
    toggleUserStatus,
    closeConfirmModal,
    executeConfirmedAction,
    togglePasswordVisibility,
    toggleActionMenu,
    handleAction,
  };
})();

document.addEventListener('DOMContentLoaded', SalesUsersModule.init);
