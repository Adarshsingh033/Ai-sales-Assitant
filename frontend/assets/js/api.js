/**
 * API client — thin wrapper around fetch.
 *
 * All backend communication goes through here.
 * Handles: base URL, authorization header, JSON serialization, error parsing.
 */

const Api = (() => {
  /**
   * Core request function.
   * @param {string} path - Endpoint path, e.g. '/auth/login'
   * @param {RequestInit} options - Fetch options
   * @returns {Promise<any>} - Parsed JSON response
   * @throws {ApiError}
   */
  async function request(path, options = {}) {
    const url = CONFIG.API_ROOT + path;

    const headers = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    // Attach Bearer token if available
    const token = Auth.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    let response;
    try {
      response = await fetch(url, { ...options, headers });
    } catch (networkError) {
      throw new ApiError('Unable to connect to the server. Please check your connection.', 0);
    }

    let data;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      const message = data?.detail || data?.message || `Request failed (${response.status})`;
      throw new ApiError(message, response.status, data);
    }

    return data;
  }

  return {
    /**
     * POST /api/v1/auth/login
     * @param {{ email: string, password: string }} credentials
     */
    async login(credentials) {
      return request('/auth/login', {
        method: 'POST',
        body: JSON.stringify(credentials),
      });
    },
  };
})();

/**
 * Structured API error.
 */
class ApiError extends Error {
  /**
   * @param {string} message
   * @param {number} status - HTTP status code (0 = network error)
   * @param {any} [data] - Raw response data
   */
  constructor(message, status, data = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}
