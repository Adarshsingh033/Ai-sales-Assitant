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
      }
    });
  });
});
