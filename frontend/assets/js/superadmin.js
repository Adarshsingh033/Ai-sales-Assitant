/**
 * Super Admin Dashboard controller.
 *
 * - Enforces authentication and SUPER_ADMIN role on load.
 * - Populates UI with real user data from the stored session.
 * - Handles sidebar navigation, profile dropdown, and logout.
 * - Shows honest empty states for unimplemented data sections.
 *
 * NO hardcoded names, emails, or business metrics.
 */

document.addEventListener('DOMContentLoaded', () => {
  // -----------------------------------------------------------------------
  // Auth guard — redirect to login if not authenticated
  // -----------------------------------------------------------------------
  Auth.requireAuth();

  const user = Auth.getCurrentUser();

  // Also enforce SUPER_ADMIN role client-side (backend enforces on API calls)
  if (!user || user.role !== 'SUPER_ADMIN') {
    Auth.logout();
    return;
  }

  // -----------------------------------------------------------------------
  // Populate user info from session (real data from backend JWT response)
  // -----------------------------------------------------------------------
  const fullName    = `${user.first_name} ${user.last_name}`.trim();
  const initials    = (user.first_name?.[0] ?? '') + (user.last_name?.[0] ?? '');
  const roleDisplay = 'Super Admin';

  // Topbar
  function updateGlobalAvatars(user) {
    const fullName = `${user.first_name} ${user.last_name}`.trim();
    const initials = (user.first_name?.[0] ?? '') + (user.last_name?.[0] ?? '');
    
    document.querySelectorAll('[data-user-name]').forEach(el => { el.textContent = fullName; });
    document.querySelectorAll('[data-user-initials]').forEach(el => { el.textContent = initials.toUpperCase(); });
    document.querySelectorAll('[data-user-email]').forEach(el => { el.textContent = user.email; });
    document.querySelectorAll('[data-user-role]').forEach(el => { el.textContent = roleDisplay; });

    const profileImgSrc = user.profile_image_url ? `${CONFIG.API_BASE_URL}${user.profile_image_url}` : null;
    document.querySelectorAll('.topbar-avatar, .sidebar-avatar, [data-user-avatar]').forEach(el => {
      if (profileImgSrc) {
        el.style.backgroundImage = `url(${profileImgSrc})`;
        el.style.backgroundSize = 'cover';
        el.style.backgroundPosition = 'center';
        el.style.color = 'transparent';
      } else {
        el.style.backgroundImage = '';
        el.style.color = '';
      }
    });
  }

  updateGlobalAvatars(user);
  window.updateGlobalAvatars = updateGlobalAvatars; // export for ProfileModule

  // -----------------------------------------------------------------------
  // Sidebar navigation
  // -----------------------------------------------------------------------
  const navItems = document.querySelectorAll('.nav-item[data-page], .nav-submenu-item[data-page], .dropdown-item[data-page]');

  navItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();

      // Remove active from all nav items
      document.querySelectorAll('.nav-item, .nav-submenu-item').forEach(n => n.classList.remove('active'));
      item.classList.add('active');

      // Update page title
      const pageTitle = item.dataset.page;
      const titleEl = document.getElementById('pageTitle');
      if (titleEl) titleEl.textContent = pageTitle;

      // Show correct content section
      document.querySelectorAll('.page-section').forEach(s => s.hidden = true);
      const sectionId = item.dataset.section;
      if (sectionId) {
        const section = document.getElementById(sectionId);
        if (section) section.hidden = false;
        
        if (sectionId === 'section-subscription-plans' && typeof SubscriptionPlansModule !== 'undefined') {
          SubscriptionPlansModule.loadPlans();
        } else if (sectionId === 'section-billing-cycles' && typeof BillingCyclesModule !== 'undefined') {
          BillingCyclesModule.loadCycles();
        } else if (sectionId === 'section-tenants' && typeof TenantsModule !== 'undefined') {
          TenantsModule.loadTenants();
        } else if (sectionId === 'section-profile') {
          ProfileModule.init(user);
        }
      }
    });
  });

  // Parent menu toggle (Accordion)
  const navParents = document.querySelectorAll('.nav-parent-item');
  navParents.forEach(parent => {
    parent.addEventListener('click', () => {
      const isExpanded = parent.classList.contains('open');
      parent.classList.toggle('open', !isExpanded);
      parent.setAttribute('aria-expanded', !isExpanded);
      
      const submenuId = parent.getAttribute('aria-controls');
      if (submenuId) {
        const submenu = document.getElementById(submenuId);
        if (submenu) submenu.classList.toggle('open', !isExpanded);
      }
    });
  });

  // -----------------------------------------------------------------------
  // Profile dropdown
  // -----------------------------------------------------------------------
  const profileToggle  = document.getElementById('profileToggle');
  const profileDropdown = document.getElementById('profileDropdown');

  profileToggle?.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = profileDropdown.classList.contains('open');
    profileDropdown.classList.toggle('open', !isOpen);
    profileToggle.classList.toggle('open', !isOpen);
  });

  document.addEventListener('click', (e) => {
    profileDropdown?.classList.remove('open');
    profileToggle?.classList.remove('open');
    if (!e.target.closest('.cell-actions')) {
      document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));
    }
  });

  // -----------------------------------------------------------------------
  // Logout
  // -----------------------------------------------------------------------
  document.querySelectorAll('[data-action="logout"]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (typeof LogoutModule !== 'undefined') {
        LogoutModule.openModal();
      } else {
        Auth.logout();
      }
    });
  });
});

/**
 * Profile Module
 */
const ProfileModule = {
  selectedImageFile: null,

  init(user) {
    this.populateForm(user);
    this.bindEvents();
  },

  populateForm(user) {
    document.getElementById('profileFirstName').value = user.first_name || '';
    document.getElementById('profileLastName').value = user.last_name || '';
    document.getElementById('profileEmail').value = user.email || '';
    document.getElementById('profilePhone').value = user.phone_number || '';
    
    // Fallback UI update based on current data
    const fullName = `${user.first_name} ${user.last_name}`.trim();
    document.getElementById('profileNameDisplay').textContent = fullName;
    document.getElementById('profileEmailDisplay').textContent = user.email;

    const initials = (user.first_name?.[0] ?? '') + (user.last_name?.[0] ?? '');
    document.getElementById('profileInitialsDisplay').textContent = initials.toUpperCase();
    
    // Set profile images if any
    const profileImgSrc = user.profile_image_url ? `${CONFIG.API_BASE_URL}${user.profile_image_url}` : null;
    if (profileImgSrc) {
      document.getElementById('profileImageDisplay').src = profileImgSrc;
      document.getElementById('profileImageDisplay').style.display = 'block';
      document.getElementById('profileInitialsDisplay').style.display = 'none';

      document.getElementById('profileImagePreview').src = profileImgSrc;
      document.getElementById('profileImagePreviewContainer').style.display = 'block';
    } else {
      document.getElementById('profileImageDisplay').style.display = 'none';
      document.getElementById('profileInitialsDisplay').style.display = 'flex';
      document.getElementById('profileImagePreviewContainer').style.display = 'none';
    }
  },

  bindEvents() {
    const fileInput = document.getElementById('profileImageInput');
    const removeBtn = document.getElementById('removeProfileImageBtn');
    const form = document.getElementById('profileForm');

    // Only bind once
    if (fileInput.hasAttribute('data-bound')) return;
    fileInput.setAttribute('data-bound', 'true');

    fileInput.addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (file) {
        if (file.size > 2 * 1024 * 1024) {
          window.showToast('File is too large. Max size is 2MB.', 'error');
          return;
        }
        this.selectedImageFile = file;
        const reader = new FileReader();
        reader.onload = (e) => {
          document.getElementById('profileImagePreview').src = e.target.result;
          document.getElementById('profileImagePreviewContainer').style.display = 'block';
        };
        reader.readAsDataURL(file);
      }
    });

    removeBtn.addEventListener('click', () => {
      this.selectedImageFile = null;
      fileInput.value = '';
      document.getElementById('profileImagePreviewContainer').style.display = 'none';
      document.getElementById('profileImagePreview').src = '';
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await this.saveProfile();
    });
  },

  async saveProfile() {
    const btn = document.getElementById('btnSaveProfile');
    const originalText = btn.textContent;
    
    try {
      btn.disabled = true;
      btn.textContent = 'Saving...';
      
      const formData = new FormData();
      formData.append('first_name', document.getElementById('profileFirstName').value);
      formData.append('last_name', document.getElementById('profileLastName').value);
      formData.append('phone_number', document.getElementById('profilePhone').value);
      
      if (this.selectedImageFile) {
        formData.append('profile_image', this.selectedImageFile);
      }

      const token = Auth.getToken();
      const response = await fetch(`${CONFIG.API_ROOT}/users/me`, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`
        },
        body: formData
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to update profile');
      }

      const updatedUser = await response.json();
      
      // Update session storage manually so that Auth.getCurrentUser returns fresh data
      localStorage.setItem(CONFIG.STORAGE_KEYS.CURRENT_USER, JSON.stringify(updatedUser));
      
      window.showToast('Profile updated successfully');
      
      // Update UI displays
      this.populateForm(updatedUser);
      
      // Update global UI like topbar and sidebar names
      if (window.updateGlobalAvatars) {
        window.updateGlobalAvatars(updatedUser);
      }

    } catch (err) {
      window.showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
};

/**
 * Change Password Module
 */
const ChangePasswordModule = {
  init() {
    this.bindEvents();
  },

  bindEvents() {
    const form = document.getElementById('changePasswordForm');
    if (!form || form.hasAttribute('data-bound')) return;
    form.setAttribute('data-bound', 'true');

    // Toggle password visibility
    document.querySelectorAll('.password-toggle-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const input = e.currentTarget.previousElementSibling;
        const openIcon = e.currentTarget.querySelector('.eye-open');
        const closedIcon = e.currentTarget.querySelector('.eye-closed');
        
        if (input.type === 'password') {
          input.type = 'text';
          if (openIcon) openIcon.style.display = 'none';
          if (closedIcon) closedIcon.style.display = 'block';
        } else {
          input.type = 'password';
          if (openIcon) openIcon.style.display = 'block';
          if (closedIcon) closedIcon.style.display = 'none';
        }
      });
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await this.savePassword();
    });
  },

  async savePassword() {
    const btn = document.getElementById('btnSavePassword');
    const oldPassword = document.getElementById('cpOldPassword').value;
    const newPassword = document.getElementById('cpNewPassword').value;
    const confirmPassword = document.getElementById('cpConfirmPassword').value;

    if (newPassword !== confirmPassword) {
      window.showToast('New passwords do not match.', 'error');
      return;
    }

    if (newPassword.length < 6) {
      window.showToast('New password must be at least 6 characters long.', 'error');
      return;
    }

    const originalText = btn.textContent;
    try {
      btn.disabled = true;
      btn.textContent = 'Saving...';

      const token = Auth.getToken();
      const response = await fetch(`${CONFIG.API_ROOT}/users/me/password`, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to update password');
      }

      window.showToast('Password updated successfully');
      document.getElementById('changePasswordForm').reset();
      
      // Reset visibility icons
      document.querySelectorAll('#changePasswordForm input[type="text"]').forEach(input => {
        input.type = 'password';
        const btn = input.nextElementSibling;
        const openIcon = btn.querySelector('.eye-open');
        const closedIcon = btn.querySelector('.eye-closed');
        if (openIcon) openIcon.style.display = 'block';
        if (closedIcon) closedIcon.style.display = 'none';
      });
      
    } catch (err) {
      window.showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
};

/**
 * Logout Module
 */
const LogoutModule = {
  openModal() {
    document.getElementById('logoutModal').classList.add('active');
  },
  closeModal() {
    document.getElementById('logoutModal').classList.remove('active');
  }
};
window.LogoutModule = LogoutModule;

document.addEventListener('DOMContentLoaded', () => {
  ChangePasswordModule.init();
});

/**
 * Global Toast Notification Helper
 */
window.showToast = function(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  const icon = type === 'success'
    ? '<svg class="toast-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>'
    : '<svg class="toast-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>';

  function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe.toString()
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  toast.innerHTML = `
    ${icon}
    <span>${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);

  setTimeout(() => toast.classList.add('show'), 10);

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
};
