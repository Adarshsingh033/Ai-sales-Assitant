/**
 * Audit Logs Module
 * Handles loading, filtering, pagination, and viewing details of audit logs.
 */

const AuditLogsModule = (() => {
  // State
  let currentPage = 1;
  const pageSize = 10;
  let totalPages = 1;
  let currentSearch = '';
  let currentAction = '';
  let currentResource = '';
  let allLogs = [];

  // DOM Elements
  const els = {
    // List & Toolbar
    tableBody: document.getElementById('auditTableBody'),
    searchInput: document.getElementById('auditSearch'),
    actionFilter: document.getElementById('auditActionFilter'),
    resourceFilter: document.getElementById('auditResourceFilter'),
    btnFirst: document.getElementById('btnAuditFirst'),
    btnPrev: document.getElementById('btnAuditPrev'),
    btnNext: document.getElementById('btnAuditNext'),
    btnLast: document.getElementById('btnAuditLast'),
    pageIndicator: document.getElementById('auditPageIndicator'),
    emptyState: document.getElementById('auditEmptyState'),
    tableContainer: document.getElementById('auditTableContainer'),
    
    // Details Modal
    detailsModal: document.getElementById('auditDetailsModal'),
    detailsContent: document.getElementById('auditDetailsContent'),
  };

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------
  function init() {
    if (!els.tableBody) return;

    // Event Listeners for Toolbar
    let debounceTimer;
    els.searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        currentSearch = e.target.value.trim();
        currentPage = 1;
        loadLogs();
      }, 300);
    });

    els.actionFilter.addEventListener('change', (e) => {
      currentAction = e.target.value;
      currentPage = 1;
      loadLogs();
    });

    els.resourceFilter.addEventListener('change', (e) => {
      currentResource = e.target.value;
      currentPage = 1;
      loadLogs();
    });

    if (els.btnFirst) {
      els.btnFirst.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage = 1;
          loadLogs();
        }
      });
    }

    if (els.btnPrev) {
      els.btnPrev.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage--;
          loadLogs();
        }
      });
    }

    if (els.btnNext) {
      els.btnNext.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage++;
          loadLogs();
        }
      });
    }

    if (els.btnLast) {
      els.btnLast.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage = totalPages;
          loadLogs();
        }
      });
    }

    // Handle section changes to load data when Audit Logs section is shown
    document.querySelectorAll('[data-section]').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.currentTarget.getAttribute('data-section') === 'section-audit-logs') {
          loadLogs();
        }
      });
    });

    loadLogs();
  }

  // -------------------------------------------------------------------------
  // Loading & Rendering
  // -------------------------------------------------------------------------
  async function loadLogs() {
    renderLoading();
    
    try {
      const params = {
        page: currentPage,
        page_size: pageSize,
        resource_id: currentSearch,
        action: currentAction,
        resource_type: currentResource
      };
      
      const response = await Api.getAuditLogs(params);
      allLogs = response.items || [];
      totalPages = response.total_pages || 1;
      currentPage = response.page || 1;
      
      renderTable(allLogs);
      updatePagination(currentPage, totalPages);
      
    } catch (err) {
      console.error('Failed to load audit logs:', err);
      showToast(`Unable to load audit logs: ${err.message || 'Unknown error'}. Please try again.`, 'error');
      renderEmptyState('Error loading data', true);
    }
  }

  function renderLoading() {
    els.tableContainer.style.display = 'block';
    els.emptyState.style.display = 'none';
    els.tableBody.innerHTML = `
      <tr><td colspan="7" style="text-align: center; padding: 48px;">
        <div style="color:var(--color-gray-500);">Loading audit logs...</div>
      </td></tr>
    `;
  }

  function renderEmptyState(message = 'No logs found', isError = false) {
    els.tableContainer.style.display = 'block';
    els.emptyState.style.display = 'none';
    
    if (isError) {
      els.tableBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; padding: 48px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="color:var(--color-error); margin-bottom: 16px;"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width:48px;height:48px;"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg></div>
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${message}</h3>
            <button class="btn-secondary mt-3" onclick="AuditLogsModule.loadLogs()">Retry</button>
          </div>
        </td></tr>
      `;
    } else {
      els.tableBody.innerHTML = `
        <tr><td colspan="7" style="text-align: center; padding: 64px 24px;">
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="background: var(--color-gray-100); border-radius: 50%; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; color: var(--color-gray-400);">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25M9 16.5v.75m3-3v3M15 12v5.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" /></svg>
            </div>
            <h3 style="font-size: 15px; font-weight: 600; color: var(--color-gray-700); margin-bottom: 8px;">${message}</h3>
            <p style="font-size: 13px; color: var(--color-gray-500); margin-bottom: 16px;">There are no audit logs matching this criteria.</p>
          </div>
        </td></tr>
      `;
    }
  }

  function renderTable(logs) {
    if (logs.length === 0) {
      renderEmptyState(currentSearch ? 'No logs match your search' : 'No logs found');
      return;
    }

    els.emptyState.style.display = 'none';
    els.tableContainer.style.display = 'block';
    
    let html = '';
    for (const log of logs) {
      const timestamp = new Date(log.created_at).toLocaleString();
      
      html += `
        <tr>
          <td>${timestamp}</td>
          <td title="${escapeHtml(log.user_id)}">${escapeHtml(log.user_id ? log.user_id.substring(0, 8) + '...' : 'System')}</td>
          <td><span class="status-badge" style="background:#f1f5f9; color:#475569; border-radius:4px; padding:2px 8px; font-size:12px; font-weight:500;">${escapeHtml(log.action)}</span></td>
          <td>${escapeHtml(log.resource_type || '—')}</td>
          <td title="${escapeHtml(log.resource_id)}">${escapeHtml(log.resource_id ? log.resource_id.substring(0, 8) + '...' : '—')}</td>
          <td>${escapeHtml(log.ip_address || '—')}</td>
        </tr>
      `;
    }
    
    els.tableBody.innerHTML = html;
  }

  function updatePagination(page, total) {
    els.pageIndicator.textContent = `Page ${page} of ${total}`;
    if (els.btnFirst) els.btnFirst.disabled = page <= 1;
    if (els.btnPrev) els.btnPrev.disabled = page <= 1;
    if (els.btnNext) els.btnNext.disabled = page >= total;
    if (els.btnLast) els.btnLast.disabled = page >= total;
    
    const currentBtn = document.getElementById('btnAuditCurrent');
    if (currentBtn) currentBtn.textContent = page;
  }

  // -------------------------------------------------------------------------
  // Log Details Modal
  // -------------------------------------------------------------------------
  async function openDetailsModal(id) {
    try {
      const log = await Api.getAuditLog(id);
      const timestamp = new Date(log.created_at).toLocaleString();
      
      let payloadHtml = '<span style="color:#9ca3af;">None</span>';
      if (log.new_values || log.old_values) {
        const payloadData = {};
        if (log.old_values) payloadData.old_values = log.old_values;
        if (log.new_values) payloadData.new_values = log.new_values;
        payloadHtml = `<pre style="background:#f8fafc; padding:12px; border-radius:6px; border:1px solid #e2e8f0; font-size:12px; max-height:200px; overflow-y:auto; overflow-x:auto;">${escapeHtml(JSON.stringify(payloadData, null, 2))}</pre>`;
      }
      
      let html = `
        <div class="detail-section">
          <div class="detail-section-title">Event Overview</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">Action</span>
              <span class="detail-value"><span class="status-badge" style="background:#f1f5f9; color:#475569; border-radius:4px; padding:2px 8px; font-weight:500;">${escapeHtml(log.action)}</span></span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Timestamp</span>
              <span class="detail-value">${timestamp}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Resource Type</span>
              <span class="detail-value">${escapeHtml(log.resource_type || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Resource ID</span>
              <span class="detail-value" style="font-size:12px; font-family:monospace; padding-top:4px;">${escapeHtml(log.resource_id || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Status Code</span>
              <span class="detail-value">${escapeHtml(log.status_code || '—')}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <div class="detail-section-title">Context & Origin</div>
          <div class="detail-grid">
            <div class="detail-row">
              <span class="detail-label">User ID</span>
              <span class="detail-value" style="font-size:12px; font-family:monospace; padding-top:4px;">${escapeHtml(log.user_id || 'System')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Tenant ID</span>
              <span class="detail-value" style="font-size:12px; font-family:monospace; padding-top:4px;">${escapeHtml(log.tenant_id || '—')}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">IP Address</span>
              <span class="detail-value">${escapeHtml(log.ip_address || '—')}</span>
            </div>
            <div class="detail-row" style="flex-direction:column; align-items:flex-start;">
              <span class="detail-label" style="margin-bottom:4px;">User Agent</span>
              <span class="detail-value" style="word-break: break-all; font-size:12px; color:var(--color-gray-500); line-height:1.4;">${escapeHtml(log.user_agent || '—')}</span>
            </div>
          </div>
        </div>
        
        <div class="detail-section" style="border-bottom:none;">
          <div class="detail-section-title">Payload Data</div>
          <div class="detail-row" style="flex-direction:column; align-items:flex-start;">
            ${payloadHtml}
          </div>
        </div>
      `;

      els.detailsContent.innerHTML = html;
      els.detailsModal.classList.add('active');
    } catch (err) {
      console.error(err);
      showToast('Unable to load audit log details.', 'error');
    }
  }

  function closeDetailsModal() {
    els.detailsModal.classList.remove('active');
  }

  function toggleActionMenu(event) {
    if (event) event.stopPropagation();
    const cell = event ? event.currentTarget.closest('.cell-actions') : null;
    if (!cell) return;
    const isCurrentlyActive = cell.classList.contains('active');

    document.querySelectorAll('.cell-actions.active').forEach(el => el.classList.remove('active'));

    if (!isCurrentlyActive) {
      cell.classList.add('active');
    }
  }

  // Utility
  function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe.toString()
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
  }

  function showToast(message, type = 'success') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
    }
  }

  // Public API
  return {
    init,
    loadLogs,
    openDetailsModal,
    closeDetailsModal,
    toggleActionMenu,
  };
})();

document.addEventListener('DOMContentLoaded', AuditLogsModule.init);
