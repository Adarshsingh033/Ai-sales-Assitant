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
      if (response.status === 401) {
        Auth.logout();
        throw new ApiError('Session expired. Please log in again.', 401, data);
      }
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

    // ---------------------------------------------------------------------
    // Tenant Management API (Super Admin)
    // ---------------------------------------------------------------------
    async getTenants(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.search) query.append('search', params.search);
      if (params.status) query.append('status', params.status);
      if (params.industry) query.append('industry', params.industry);
      
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/super-admin/tenants${qs}`, { method: 'GET' });
    },

    async getTenant(id) {
      return request(`/super-admin/tenants/${id}`, { method: 'GET' });
    },

    async createTenant(data) {
      return request('/super-admin/tenants', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async updateTenant(id, data) {
      return request(`/super-admin/tenants/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      });
    },

    async updateTenantStatus(id, status) {
      return request(`/super-admin/tenants/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      });
    },

    async deleteTenant(id) {
      return request(`/super-admin/tenants/${id}`, {
        method: 'DELETE',
      });
    },

    async assignSubscriptionPlan(tenantId, planId) {
      return request(`/super-admin/tenants/${tenantId}/subscription-plan`, {
        method: 'PATCH',
        body: JSON.stringify({ subscription_plan_id: planId }),
      });
    },

    async getSubscriptionPlans(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/super-admin/subscription-plans${qs}`, { method: 'GET' });
    },

    async getSubscriptionPlan(id) {
      return request(`/super-admin/subscription-plans/${id}`, { method: 'GET' });
    },

    async createSubscriptionPlan(data) {
      return request('/super-admin/subscription-plans', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async updateSubscriptionPlan(id, data) {
      return request(`/super-admin/subscription-plans/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      });
    },

    async updateSubscriptionPlanStatus(id, isActive) {
      return request(`/super-admin/subscription-plans/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: isActive }),
      });
    },

    // ---------------------------------------------------------------------
    // Billing Cycles API (Super Admin)
    // ---------------------------------------------------------------------
    async getBillingCycles(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.active_only) query.append('active_only', params.active_only);
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/super-admin/billing-cycles${qs}`, { method: 'GET' });
    },

    async getBillingCycle(id) {
      return request(`/super-admin/billing-cycles/${id}`, { method: 'GET' });
    },

    async createBillingCycle(data) {
      return request('/super-admin/billing-cycles', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async updateBillingCycle(id, data) {
      return request(`/super-admin/billing-cycles/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      });
    },

    async updateBillingCycleStatus(id, isActive) {
      return request(`/super-admin/billing-cycles/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: isActive }),
      });
    },

    async deleteBillingCycle(id) {
      return request(`/super-admin/billing-cycles/${id}`, {
        method: 'DELETE',
      });
    },

    // -----------------------------------------------------------------------
    // Dashboard Stats
    // -----------------------------------------------------------------------
    async getDashboardStats() {
      return request('/super-admin/dashboard', { method: 'GET' });
    },

    // -----------------------------------------------------------------------
    // Audit Logs
    // -----------------------------------------------------------------------
    async getAuditLogs(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.action) query.append('action', params.action);
      if (params.resource_type) query.append('resource_type', params.resource_type);
      if (params.resource_id) query.append('resource_id', params.resource_id);
      
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/super-admin/audit-logs${qs}`, { method: 'GET' });
    },

    async getAuditLog(id) {
      return request(`/super-admin/audit-logs/${id}`, { method: 'GET' });
    }
  };

  // Expose Api to global window
  window.Api = Api;

  // Optional: add a global error handler for uncaught promise rejections
  window.addEventListener('unhandledrejection', (event) => {
    // console.error('Unhandled API Error:', event.reason);
  });
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
