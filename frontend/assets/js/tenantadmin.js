/**
 * Tenant Admin Dashboard controller.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Auth guard
  Auth.requireAuth();

  const user = Auth.getCurrentUser();

  // Enforce TENANT_ADMIN role
  if (!user || user.role !== 'TENANT_ADMIN') {
    Auth.logout();
    return;
  }

  // Populate user info
  function updateGlobalAvatars(user) {
    const fullName = `${user.first_name} ${user.last_name}`.trim();
    const initials = (user.first_name?.[0] ?? '') + (user.last_name?.[0] ?? '');
    
    document.querySelectorAll('[data-user-name]').forEach(el => { el.textContent = fullName; });
    document.querySelectorAll('[data-user-initials]').forEach(el => { el.textContent = initials.toUpperCase(); });
    
    let profileImgSrc = null;
    if (user.profile_image_url) {
      if (user.profile_image_url.startsWith('data:') || user.profile_image_url.startsWith('http')) {
        profileImgSrc = user.profile_image_url;
      } else {
        profileImgSrc = `${CONFIG.API_BASE_URL}${user.profile_image_url}`;
      }
    }
    
    document.querySelectorAll('.topbar-avatar, .sidebar-avatar, [data-user-avatar]').forEach(el => {
      if (profileImgSrc) {
        el.style.backgroundImage = `url("${profileImgSrc}")`;
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
  window.updateGlobalAvatars = updateGlobalAvatars;

  // Sidebar navigation
  const navItems = document.querySelectorAll('.nav-item[data-page]');

  navItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();

      // Remove active from all nav items
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
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

        if (sectionId === 'section-dashboard' && typeof TenantDashboardModule !== 'undefined') {
          TenantDashboardModule.init();
        } else if (sectionId === 'section-leads' && typeof LeadsModule !== 'undefined') {
          LeadsModule.init();
        } else if (sectionId === 'section-conversations' && typeof ConversationsModule !== 'undefined') {
          ConversationsModule.init();
        } else if (sectionId === 'section-products' && typeof ProductsModule !== 'undefined') {
          ProductsModule.init();
        } else if (sectionId === 'section-inventory' && typeof InventoryModule !== 'undefined') {
          InventoryModule.loadData();
        } else if (sectionId === 'section-knowledge' && typeof KnowledgeModule !== 'undefined') {
          KnowledgeModule.init();
        } else if (sectionId === 'section-ai-config' && typeof AIConfigModule !== 'undefined') {
          AIConfigModule.init();
        } else if (sectionId === 'section-user-management' && typeof SalesUsersModule !== 'undefined') {
          SalesUsersModule.loadUsers();
        }
      }
    });
  });

  // Initialize initial section & chat simulator
  if (typeof TenantDashboardModule !== 'undefined') {
    TenantDashboardModule.init();
  }
  if (typeof ChatSimulatorModule !== 'undefined') {
    ChatSimulatorModule.init();
  }
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
  }, 4000);
};
