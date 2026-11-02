/**
 * Workspace controller — shared by Tenant Admins and Sales Users.
 *
 * - Auth guard (admin or sales rep) and role switch: reps never see the Setup section or admin-only widgets.
 * - Sidebar navigation with deep-linkable URLs (#home, #contacts, #pipeline?lead=…, #tasks, …).
 * - Starts each section's module when it is opened, and keeps the sidebar badges fresh.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Auth guard
  Auth.requireAuth();

  const user = Auth.getCurrentUser();

  // Admins and sales reps use this workspace; everyone else goes back to login
  if (!user || (user.role !== 'TENANT_ADMIN' && user.role !== 'SALES_USER')) {
    Auth.logout();
    return;
  }
  const isRep = user.role === 'SALES_USER';
  document.body.dataset.role = isRep ? 'rep' : 'admin';

  const portalLabel = document.getElementById('portalLabel');
  if (portalLabel) portalLabel.textContent = isRep ? 'Sales Portal' : 'Admin Portal';
  document.querySelectorAll('[data-user-role]').forEach((el) => { el.textContent = isRep ? 'Sales' : 'Admin'; });

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

  // ---------------------------------------------------------------- sections
  const navItems = Array.from(document.querySelectorAll('.nav-item[data-section]'));
  const allowedNav = (item) => !(isRep && item.hasAttribute('data-admin-only'));

  /** What to start when a section is opened. Every init() is safe to call repeatedly. */
  // (`typeof` rather than `window.X`: some modules are top-level `const`s, which are not window properties)
  const SECTION_INIT = {
    'section-dashboard': () => { if (typeof TenantDashboardModule !== 'undefined') TenantDashboardModule.init(); },
    'section-leads': () => { if (typeof PipelineModule !== 'undefined') PipelineModule.init(); },
    'section-contacts': () => { if (typeof ContactsModule !== 'undefined') ContactsModule.init(); },
    'section-tasks': () => { if (typeof TasksModule !== 'undefined') TasksModule.init(); },
    'section-conversations': () => { if (typeof ConversationsModule !== 'undefined') ConversationsModule.init(); },
    'section-products': () => { if (typeof ProductsModule !== 'undefined') ProductsModule.init(); },
    'section-inventory': () => { if (typeof InventoryModule !== 'undefined') InventoryModule.loadData(); },
    'section-knowledge': () => { if (typeof KnowledgeModule !== 'undefined') KnowledgeModule.init(); },
    'section-ai-config': () => { if (typeof AIConfigModule !== 'undefined') AIConfigModule.init(); },
    'section-user-management': () => { if (typeof SalesUsersModule !== 'undefined') SalesUsersModule.loadUsers(); },
  };

  function parseHash() {
    const raw = window.location.hash.replace(/^#\/?/, '');
    const [key, query] = raw.split('?');
    return { key: key || 'home', params: Object.fromEntries(new URLSearchParams(query || '')) };
  }

  let activeKey = null;

  function activate(item, params = {}) {
    Crm.closeDialogs(); // a dialog from the previous screen must not linger over the next one
    document.querySelectorAll('.nav-item').forEach((n) => { n.classList.remove('active'); n.removeAttribute('aria-current'); });
    item.classList.add('active');
    item.setAttribute('aria-current', 'page');

    const titleEl = document.getElementById('pageTitle');
    if (titleEl) titleEl.textContent = item.dataset.page;
    document.title = `${item.dataset.page} — AI Sales Assistant`;

    document.querySelectorAll('.page-section').forEach((s) => { s.hidden = true; });
    const section = document.getElementById(item.dataset.section);
    if (section) section.hidden = false;
    document.getElementById('mainContent')?.scrollTo?.(0, 0);

    activeKey = item.dataset.key;
    try { SECTION_INIT[item.dataset.section]?.(); } catch (err) { console.error('Section failed to start:', err); }
    Crm.emit('route:' + item.dataset.key, params);
    Crm.emit('route', { key: item.dataset.key, params });
  }

  function route() {
    const { key, params } = parseHash();
    const item = navItems.find((n) => n.dataset.key === key && allowedNav(n)) || navItems[0];
    activate(item, item.dataset.key === key ? params : {});
  }

  navItems.forEach((item) => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      if (!allowedNav(item)) return;
      const target = `#${item.dataset.key}`;
      if (window.location.hash === target || (!window.location.hash && item.dataset.key === 'home')) {
        route(); // re-clicking the current page refreshes it
      } else {
        window.location.hash = target; // hashchange does the rest, so Back works
      }
    });
  });
  window.addEventListener('hashchange', route);

  // ---------------------------------------------------------------- mobile menu (the sidebar slides in on small screens)
  const sidebar = document.getElementById('sidebar');
  const menuBtn = document.getElementById('btnMenu');
  const scrim = document.createElement('div');
  scrim.className = 'sidebar-scrim';
  document.body.appendChild(scrim);
  function setMenu(open) {
    sidebar?.classList.toggle('open', open);
    document.body.classList.toggle('nav-open', open);
    menuBtn?.setAttribute('aria-expanded', String(open));
  }
  menuBtn?.addEventListener('click', () => setMenu(!sidebar.classList.contains('open')));
  scrim.addEventListener('click', () => setMenu(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });
  navItems.forEach((item) => item.addEventListener('click', () => setMenu(false)));
  window.addEventListener('hashchange', () => setMenu(false));

  // ---------------------------------------------------------------- start
  if (!isRep && typeof ChatSimulatorModule !== 'undefined') {
    ChatSimulatorModule.init();
  }
  route();

  // Sidebar badges (overdue tasks, chats waiting for a person): now, after changes, and every minute
  Crm.refreshBadges();
  ['tasks-changed', 'deals-changed'].forEach((ev) => Crm.on(ev, () => Crm.refreshBadges()));
  setInterval(() => { if (!document.hidden) Crm.refreshBadges(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) Crm.refreshBadges(); });
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
