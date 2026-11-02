/**
 * Frontend configuration.
 *
 * API_BASE_URL is the only value that differs between environments.
 * To deploy to a different environment, change this file only — never
 * scatter the URL across multiple JS files.
 *
 * In a build-tool setup this would be replaced with env vars at build time.
 * For the current vanilla JS setup, edit this file per environment.
 */

const CONFIG = Object.freeze({
  /**
   * Base URL of the FastAPI backend.
   * No trailing slash.
   */
  API_BASE_URL: 'http://localhost:8000',

  /**
   * API version prefix.
   */
  API_VERSION: '/api/v1',

  /**
   * Full API root — convenience shorthand.
   */
  get API_ROOT() {
    return this.API_BASE_URL + this.API_VERSION;
  },

  /**
   * LocalStorage keys — defined once, used everywhere.
   */
  STORAGE_KEYS: {
    ACCESS_TOKEN: 'asa_access_token',
    CURRENT_USER: 'asa_current_user',
  },

  /**
   * Role → dashboard path mapping.
   * Admins and sales reps share one workspace page; the menu and data adapt to the role
   * (reps only see what is assigned to them, and have no Setup section).
   */
  ROLE_DASHBOARDS: {
    SUPER_ADMIN:  '/pages/superadmin/dashboard.html',
    TENANT_ADMIN: '/pages/tenantadmin/dashboard.html',
    SALES_USER:   '/pages/tenantadmin/dashboard.html',
  },
});
