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
  document.querySelectorAll('[data-user-name]').forEach(el => { el.textContent = fullName; });
  document.querySelectorAll('[data-user-initials]').forEach(el => { el.textContent = initials.toUpperCase(); });
  document.querySelectorAll('[data-user-email]').forEach(el => { el.textContent = user.email; });
  document.querySelectorAll('[data-user-role]').forEach(el => { el.textContent = roleDisplay; });

  // -----------------------------------------------------------------------
  // Sidebar navigation
  // -----------------------------------------------------------------------
  const navItems = document.querySelectorAll('.nav-item[data-page], .nav-submenu-item[data-page]');

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
    btn.addEventListener('click', () => Auth.logout());
  });
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
