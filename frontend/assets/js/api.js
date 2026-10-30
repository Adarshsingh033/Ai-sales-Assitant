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

    const headers = { ...options.headers };
    if (!(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }

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
    },

    // -----------------------------------------------------------------------
    // Sales User Management API (Tenant Admin)
    // -----------------------------------------------------------------------
    async getSalesUsers(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.search) query.append('search', params.search);
      if (params.is_active !== undefined && params.is_active !== '') query.append('is_active', params.is_active);
      if (params.is_available !== undefined && params.is_available !== '') query.append('is_available', params.is_available);
      if (params.sort_by) query.append('sort_by', params.sort_by);
      if (params.sort_order) query.append('sort_order', params.sort_order);

      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/users${qs}`, { method: 'GET' });
    },

    async getSalesUser(id) {
      return request(`/users/${id}`, { method: 'GET' });
    },

    async createSalesUser(data) {
      return request('/users', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async updateSalesUser(id, data) {
      return request(`/users/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    async updateSalesUserStatus(id, isActive) {
      return request(`/users/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: isActive }),
      });
    },

    async resetSalesUserPassword(id, newPassword) {
      return request(`/users/${id}/reset-password`, {
        method: 'POST',
        body: JSON.stringify({ new_password: newPassword }),
      });
    },

    async deleteSalesUser(id) {
      return request(`/users/${id}`, {
        method: 'DELETE',
      });
    },

    // -----------------------------------------------------------------------
    // Tenant Sales Dashboard & Intelligence (Phase 1 MVP)
    // -----------------------------------------------------------------------
    async getTenantDashboard() {
      return request('/tenant-admin/dashboard', { method: 'GET' });
    },

    // -----------------------------------------------------------------------
    // Leads & Pipeline
    // -----------------------------------------------------------------------
    async getLeads(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.search) query.append('search', params.search);
      const statusVal = params.status || params.stage;
      if (statusVal) query.append('status', statusVal);
      if (params.priority) query.append('priority', params.priority);
      if (params.min_score !== undefined && params.min_score !== '') query.append('min_score', params.min_score);

      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/leads${qs}`, { method: 'GET' });
    },

    async getLead(id) {
      return request(`/tenant-admin/leads/${id}`, { method: 'GET' });
    },

    async updateLead(id, data) {
      return request(`/tenant-admin/leads/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    async deleteLead(id) {
      return request(`/tenant-admin/leads/${id}`, {
        method: 'DELETE',
      });
    },

    async getLeadPipelineStats() {
      return request('/tenant-admin/leads/stats/pipeline', { method: 'GET' });
    },

    // -----------------------------------------------------------------------
    // Conversations & Human Takeover
    // -----------------------------------------------------------------------
    async getConversations(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.status) query.append('status', params.status);
      if (params.channel) query.append('channel', params.channel);
      if (params.search) query.append('search', params.search);

      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/conversations${qs}`, { method: 'GET' });
    },

    async getConversation(id) {
      return request(`/tenant-admin/conversations/${id}`, { method: 'GET' });
    },

    async getConversationMessages(id) {
      return request(`/tenant-admin/conversations/${id}/messages`, { method: 'GET' });
    },

    async takeoverConversation(id) {
      return request(`/tenant-admin/conversations/${id}/takeover`, { method: 'POST' });
    },

    async handbackConversation(id) {
      return request(`/tenant-admin/conversations/${id}/handback`, { method: 'POST' });
    },

    async resolveConversation(id) {
      return request(`/tenant-admin/conversations/${id}/resolve`, { method: 'POST' });
    },

    async sendHumanReply(id, message) {
      return request(`/tenant-admin/conversations/${id}/reply`, {
        method: 'POST',
        body: JSON.stringify({ content: message }),
      });
    },

    // -----------------------------------------------------------------------
    // Products Catalog
    // -----------------------------------------------------------------------
    async getProducts(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.search) query.append('search', params.search);
      if (params.category) query.append('category', params.category);
      if (params.is_active !== undefined && params.is_active !== '') query.append('is_active', params.is_active);

      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/products${qs}`, { method: 'GET' });
    },

    async getProduct(id) {
      return request(`/tenant-admin/products/${id}`, { method: 'GET' });
    },

    async createProduct(data) {
      return request('/tenant-admin/products', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async updateProduct(id, data) {
      return request(`/tenant-admin/products/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    async deleteProduct(id) {
      return request(`/tenant-admin/products/${id}`, {
        method: 'DELETE',
      });
    },

    // -----------------------------------------------------------------------
    // Knowledge Base & RAG
    // -----------------------------------------------------------------------
    async getKnowledgeDocuments() {
      return request('/tenant-admin/knowledge/documents', { method: 'GET' });
    },

    async uploadKnowledgeDocument(formData) {
      return request('/tenant-admin/knowledge/upload', {
        method: 'POST',
        body: formData,
      });
    },

    async deleteKnowledgeDocument(id) {
      return request(`/tenant-admin/knowledge/documents/${id}`, {
        method: 'DELETE',
      });
    },

    async searchKnowledge(query, limit = 5) {
      return request('/tenant-admin/knowledge/test-search', {
        method: 'POST',
        body: JSON.stringify({ query, top_k: limit }),
      });
    },

    // -----------------------------------------------------------------------
    // AI Assistant Config
    // -----------------------------------------------------------------------
    async getAIConfig() {
      return request('/tenant-admin/ai-config', { method: 'GET' });
    },

    async updateAIConfig(data) {
      return request('/tenant-admin/ai-config', {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    // -----------------------------------------------------------------------
    // Chat Simulator
    // -----------------------------------------------------------------------
    async simulateChat(data) {
      return request('/tenant-admin/chat/simulate', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    /**
     * POST /api/v1/tenant-admin/chat/simulate-stream
     * Streams responses token-by-token via Server-Sent Events (SSE)
     * @param {Object} data - { message, session_id, customer_name, customer_phone }
     * @param {Function} onToken - callback(tokenText)
     * @param {Function} onDone - callback(finalMetadataObject)
     * @param {Function} onError - callback(error)
     */
    async simulateChatStream(data, onToken, onDone, onError) {
      const url = CONFIG.API_ROOT + '/tenant-admin/chat/simulate-stream';
      const headers = {
        'Content-Type': 'application/json',
      };
      const token = Auth.getToken();
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      try {
        const response = await fetch(url, {
          method: 'POST',
          headers,
          body: JSON.stringify(data),
        });

        if (!response.ok) {
          let errText = `Request failed (${response.status})`;
          try {
            const errJson = await response.json();
            errText = errJson.detail || errText;
          } catch {}
          if (onError) onError(new Error(errText));
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop(); // keep partial line in buffer

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith('data: ')) continue;
            const dataStr = trimmed.slice(6).trim();
            try {
              const parsed = JSON.parse(dataStr);
              if (parsed.token && onToken) {
                onToken(parsed.token);
              }
              if (parsed.done && onDone) {
                onDone(parsed);
              }
            } catch (parseErr) {
              // ignore parse errors for chunks
            }
          }
        }
      } catch (err) {
        if (onError) onError(err);
      }
    },


    async getChatHistory(sessionId) {
      return request(`/tenant-admin/chat/history/${encodeURIComponent(sessionId)}`, {
        method: 'GET',
      });
    },

    async resetSimulatorSession(data = {}) {
      return request('/tenant-admin/chat/reset-session', {
        method: 'POST',
        body: JSON.stringify(data),
      });
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
