/**
 * Auth utility — manages authentication state in the browser.
 *
 * Stores: access token and user object in localStorage.
 * The backend is ALWAYS the source of truth for authorization.
 * Frontend role checks are for UX routing only — never trust-only.
 */

const Auth = (() => {
  const KEYS = CONFIG.STORAGE_KEYS;

  // -----------------------------------------------------------------------
  // Token management
  // -----------------------------------------------------------------------

  function getToken() {
    return localStorage.getItem(KEYS.ACCESS_TOKEN) || null;
  }

  function setToken(token) {
    if (token) {
      localStorage.setItem(KEYS.ACCESS_TOKEN, token);
    }
  }

  // -----------------------------------------------------------------------
  // User management
  // -----------------------------------------------------------------------

  function getCurrentUser() {
    const raw = localStorage.getItem(KEYS.CURRENT_USER);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function setCurrentUser(user) {
    if (user) {
      localStorage.setItem(KEYS.CURRENT_USER, JSON.stringify(user));
    }
  }

  // -----------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------

  function isAuthenticated() {
    return !!getToken() && !!getCurrentUser();
  }

  // -----------------------------------------------------------------------
  // Session management
  // -----------------------------------------------------------------------

  /**
   * Store token and user after a successful login response.
   * @param {{ access_token: string, user: object }} loginResponse
   */
  function setSession(loginResponse) {
    setToken(loginResponse.access_token);
    setCurrentUser(loginResponse.user);
  }

  /**
   * Clear all authentication state and redirect to login.
   */
  function logout() {
    localStorage.removeItem(KEYS.ACCESS_TOKEN);
    localStorage.removeItem(KEYS.CURRENT_USER);
    window.location.href = '/pages/login.html';
  }

  // -----------------------------------------------------------------------
  // Role-based routing
  // -----------------------------------------------------------------------

  /**
   * Redirect the user to their role-appropriate dashboard.
   * Falls back to login if role is unrecognised.
   */
  function redirectByRole() {
    const user = getCurrentUser();
    if (!user) {
      window.location.href = '/pages/login.html';
      return;
    }
    const destination = CONFIG.ROLE_DASHBOARDS[user.role];
    if (destination) {
      window.location.href = destination;
    } else {
      console.warn('Unknown role:', user.role);
      logout();
    }
  }

  // -----------------------------------------------------------------------
  // Guards — call at top of protected pages
  // -----------------------------------------------------------------------

  /**
   * Redirect to login if the user is not authenticated.
   * Call at the top of every protected page script.
   */
  function requireAuth() {
    if (!isAuthenticated()) {
      window.location.href = '/pages/login.html';
    }
  }

  /**
   * Redirect already-authenticated users away from the login page.
   * Call at the top of login.js.
   */
  function redirectIfAuthenticated() {
    if (isAuthenticated()) {
      redirectByRole();
    }
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------
  return {
    getToken,
    getCurrentUser,
    isAuthenticated,
    setSession,
    logout,
    redirectByRole,
    requireAuth,
    redirectIfAuthenticated,
  };
})();
