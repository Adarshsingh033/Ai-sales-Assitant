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
      if (params.assigned_user_id) query.append('assigned_user_id', params.assigned_user_id);
      if (params.unassigned) query.append('unassigned', 'true');
      if (params.customer_id) query.append('customer_id', params.customer_id);

      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/leads${qs}`, { method: 'GET' });
    },

    /** Deals grouped by stage for the Kanban board: { columns: [{stage,label,count,total_value,cards}], unassigned } */
    async getLeadBoard(params = {}) {
      const query = new URLSearchParams();
      if (params.search) query.append('search', params.search);
      if (params.assigned_user_id) query.append('assigned_user_id', params.assigned_user_id);
      if (params.unassigned) query.append('unassigned', 'true');
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/leads/board${qs}`, { method: 'GET' });
    },

    /** Create a deal by hand: { customer_id, title, budget?, status?, priority?, notes?, assigned_user_id? } */
    async createLead(data) {
      return request('/tenant-admin/leads', { method: 'POST', body: JSON.stringify(data) });
    },

    // -----------------------------------------------------------------------
    // Contacts (one list of customers + detail with deals, chats, tasks, timeline)
    // -----------------------------------------------------------------------
    async getContacts(params = {}) {
      const query = new URLSearchParams();
      if (params.page) query.append('page', params.page);
      if (params.page_size) query.append('page_size', params.page_size);
      if (params.search) query.append('search', params.search);
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/contacts${qs}`, { method: 'GET' });
    },

    async getContact(id) {
      return request(`/tenant-admin/contacts/${id}`, { method: 'GET' });
    },

    /** { name, phone?, email?, company_name?, owner_user_id? } */
    async createContact(data) {
      return request('/tenant-admin/contacts', { method: 'POST', body: JSON.stringify(data) });
    },

    async updateContact(id, data) {
      return request(`/tenant-admin/contacts/${id}`, { method: 'PUT', body: JSON.stringify(data) });
    },

    async addContactNote(id, text, leadId) {
      const body = { text };
      if (leadId) body.lead_id = leadId;
      return request(`/tenant-admin/contacts/${id}/notes`, { method: 'POST', body: JSON.stringify(body) });
    },

    async deleteContactNote(id, noteId) {
      return request(`/tenant-admin/contacts/${id}/notes/${noteId}`, { method: 'DELETE' });
    },

    // -----------------------------------------------------------------------
    // Tasks & reminders
    // -----------------------------------------------------------------------
    /** params: bucket (all|overdue|today|upcoming|no_date|done), day_end (ISO), mine, assigned_user_id, customer_id, lead_id, page, page_size */
    async getTasks(params = {}) {
      const query = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '' && v !== false) query.append(k, v === true ? 'true' : v);
      });
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/tasks${qs}`, { method: 'GET' });
    },

    /** { overdue, today, upcoming, no_date, open } */
    async getTaskSummary(params = {}) {
      const query = new URLSearchParams();
      if (params.day_end) query.append('day_end', params.day_end);
      if (params.mine) query.append('mine', 'true');
      const qs = query.toString() ? `?${query.toString()}` : '';
      return request(`/tenant-admin/tasks/summary${qs}`, { method: 'GET' });
    },

    /** { title, notes?, due_at?, customer_id?, lead_id?, assigned_user_id? } */
    async createTask(data) {
      return request('/tenant-admin/tasks', { method: 'POST', body: JSON.stringify(data) });
    },

    async updateTask(id, data) {
      return request(`/tenant-admin/tasks/${id}`, { method: 'PUT', body: JSON.stringify(data) });
    },

    async completeTask(id) {
      return request(`/tenant-admin/tasks/${id}/complete`, { method: 'POST' });
    },

    async reopenTask(id) {
      return request(`/tenant-admin/tasks/${id}/reopen`, { method: 'POST' });
    },

    async deleteTask(id) {
      return request(`/tenant-admin/tasks/${id}`, { method: 'DELETE' });
    },

    // -----------------------------------------------------------------------
    // Team & home summary
    // -----------------------------------------------------------------------
    /** Active people a deal/contact/task can be given to: [{ id, name, role, is_me }] */
    async getTeam() {
      return request('/tenant-admin/team', { method: 'GET' });
    },

    /** Roles an admin has created for the team (Team screen, admin only). */
    async getRoles() {
      return request('/tenant-admin/roles', { method: 'GET' });
    },

    async createRole(data) {
      return request('/tenant-admin/roles', { method: 'POST', body: JSON.stringify(data) });
    },

    async updateRole(id, data) {
      return request(`/tenant-admin/roles/${id}`, { method: 'PUT', body: JSON.stringify(data) });
    },

    async deleteRole(id) {
      return request(`/tenant-admin/roles/${id}`, { method: 'DELETE' });
    },

    /** What needs attention today: tasks counts, focus tasks, unassigned deals, chats needing a human, setup checklist (admins). */
    async getHomeSummary(dayEnd) {
      const qs = dayEnd ? `?day_end=${encodeURIComponent(dayEnd)}` : '';
      return request(`/tenant-admin/dashboard/home${qs}`, { method: 'GET' });
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
      return request('/tenant-admin/leads/pipeline-stats', { method: 'GET' });
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

    async reindexKnowledgeDocument(id) {
      return request(`/tenant-admin/knowledge/documents/${id}/reindex`, { method: 'POST' });
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

    /**
     * Ask the knowledge base (any admin or sales user). `history` is the chat so far: [{ role: 'user'|'assistant', text }].
     * Returns { status: answered|not_found|not_stated|unverified|empty, answer, sources: [{ n, kind, title, location, text }], searched_for }.
     */
    async askKnowledge(question, history = []) {
      return request('/tenant-admin/knowledge/ask', {
        method: 'POST',
        body: JSON.stringify({ question, history }),
      });
    },

    /** Read-only view of what the assistant can answer from: { documents, product_count, product_names }. */
    async getKnowledgeLibrary() {
      return request('/tenant-admin/knowledge/library', { method: 'GET' });
    },

    /** Admins: questions nobody could answer: { items: [{ id, question, times_asked, last_asked_by, last_asked_at }], open_count }. */
    async getKnowledgeGaps() {
      return request('/tenant-admin/knowledge/gaps', { method: 'GET' });
    },

    async dismissKnowledgeGap(id) {
      return request(`/tenant-admin/knowledge/gaps/${id}/dismiss`, { method: 'POST' });
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
    },

    // ---------------------------------------------------------------------
    // AI Usage Monitoring API
    // ---------------------------------------------------------------------
    async getAIUsageStats(days = 30) {
      return request(`/tenant-admin/ai-usage/stats?days=${days}`, { method: 'GET' });
    },

    // ---------------------------------------------------------------------
    // In-App & Browser Notifications API
    // ---------------------------------------------------------------------
    async getNotifications(limit = 25, unreadOnly = false) {
      return request(`/tenant-admin/notifications?limit=${limit}&unread_only=${unreadOnly}`, { method: 'GET' });
    },

    async getUnreadNotificationCount() {
      return request('/tenant-admin/notifications/unread-count', { method: 'GET' });
    },

    async markNotificationRead(id) {
      return request(`/tenant-admin/notifications/${id}/read`, { method: 'POST' });
    },

    async markAllNotificationsRead() {
      return request('/tenant-admin/notifications/read-all', { method: 'POST' });
    },

    // ---------------------------------------------------------------------
    // Warehouse & Custom ERP Inventory API
    // ---------------------------------------------------------------------
    async getWarehouses() {
      return request('/tenant-admin/inventory/warehouses', { method: 'GET' });
    },

    async createWarehouse(data) {
      return request('/tenant-admin/inventory/warehouses', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    async getInventory(lowStockOnly = false) {
      return request(`/tenant-admin/inventory/stock?low_stock_only=${lowStockOnly}`, { method: 'GET' });
    },

    async updateInventoryStock(data) {
      return request('/tenant-admin/inventory/stock', {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    // ---------------------------------------------------------------------
    // Automated Follow-Ups API
    // ---------------------------------------------------------------------
    async getFollowUps(status = '') {
      const q = status ? `?status=${encodeURIComponent(status)}` : '';
      return request(`/tenant-admin/follow-ups${q}`, { method: 'GET' });
    },

    async processDueFollowUps() {
      return request('/tenant-admin/follow-ups/process-due', { method: 'POST' });
    },

    async cancelFollowUp(id) {
      return request(`/tenant-admin/follow-ups/${id}`, { method: 'DELETE' });
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
