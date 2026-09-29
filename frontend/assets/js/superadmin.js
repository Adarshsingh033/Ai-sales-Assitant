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
  const navItems = document.querySelectorAll('.nav-item[data-page]');

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      // Remove active from all
      navItems.forEach(n => n.classList.remove('active'));
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

  document.addEventListener('click', () => {
    profileDropdown?.classList.remove('open');
    profileToggle?.classList.remove('open');
  });

  // -----------------------------------------------------------------------
  // Logout
  // -----------------------------------------------------------------------
  document.querySelectorAll('[data-action="logout"]').forEach(btn => {
    btn.addEventListener('click', () => Auth.logout());
  });
});
