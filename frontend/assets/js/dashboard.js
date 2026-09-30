/**
 * Dashboard Module — Real-time analytics for the Super Admin portal.
 * Fetches live data from GET /api/v1/super-admin/dashboard and renders
 * all KPI cards, recent tenants, activity feed, and industry breakdown.
 */

const DashboardModule = (() => {
  let _liveClockInterval = null;

  // -------------------------------------------------------------------------
  // Initialise
  // -------------------------------------------------------------------------
  function init() {
    _startLiveClock();
    _loadStats();
  }

  // -------------------------------------------------------------------------
  // Live clock in the welcome banner
  // -------------------------------------------------------------------------
  function _startLiveClock() {
    const timeEl = document.getElementById('dbLiveTime');
    const dateEl = document.getElementById('dbLiveDate');
    if (!timeEl) return;

    function tick() {
      const now = new Date();
      timeEl.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      if (dateEl) {
        dateEl.textContent = now.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
      }
    }
    tick();
    if (_liveClockInterval) clearInterval(_liveClockInterval);
    _liveClockInterval = setInterval(tick, 1000);
  }

  // -------------------------------------------------------------------------
  // Load all stats from backend
  // -------------------------------------------------------------------------
  async function _loadStats() {
    _setLoadingState(true);
    try {
      const data = await Api.getDashboardStats();
      _renderKPIs(data);
      _renderSecondaryStats(data);
      _renderRecentTenants(data.recent_tenants || []);
      _renderActivityFeed(data.recent_activity || []);
      _renderIndustryBreakdown(data.industry_breakdown || []);
    } catch (err) {
      console.error('Dashboard stats load failed:', err);
      showToast('Could not load dashboard data: ' + (err.message || 'Unknown error'), 'error');
    } finally {
      _setLoadingState(false);
    }
  }

  // -------------------------------------------------------------------------
  // Remove skeleton loaders
  // -------------------------------------------------------------------------
  function _setLoadingState(loading) {
    document.querySelectorAll('.db-kpi-value, .db-stat-pill-value').forEach(el => {
      if (loading) {
        el.dataset.original = el.textContent;
        el.innerHTML = '<span class="db-skeleton" style="display:inline-block;width:60px;height:28px;"></span>';
      } else {
        // Values already written by render functions
      }
    });
  }

  // -------------------------------------------------------------------------
  // Render KPI cards
  // -------------------------------------------------------------------------
  function _renderKPIs(data) {
    _setText('dbTotalTenants', data.total_tenants);
    _setText('dbActiveTenants', data.active_tenants);
    _setText('dbTotalPlans', data.active_plans + ' / ' + data.total_plans);
    _setText('dbAuditEvents', data.audit_events_today);

    // Trend badge for tenants this month vs last month
    _renderTrend(
      'dbTenantTrend',
      data.new_tenants_this_month,
      data.new_tenants_last_month,
      `${data.new_tenants_this_month} new this month`
    );

    _setText('dbActiveTenantFooter', `${data.inactive_tenants} inactive`);
    _setText('dbPlanFooter', `${data.active_billing_cycles}/${data.total_billing_cycles} billing cycles active`);
    _setText('dbAuditFooter', `${data.total_audit_events} total events logged`);
  }

  function _renderTrend(id, current, previous, label) {
    const el = document.getElementById(id);
    if (!el) return;
    const diff = current - previous;
    let cls = 'neutral', arrow = '→', text = `${label}`;
    if (diff > 0) { cls = 'up'; arrow = '↑'; }
    else if (diff < 0) { cls = 'down'; arrow = '↓'; }
    el.className = `db-kpi-trend ${cls}`;
    el.textContent = `${arrow} ${label}`;
  }

  // -------------------------------------------------------------------------
  // Secondary stat pills
  // -------------------------------------------------------------------------
  function _renderSecondaryStats(data) {
    _setText('dbTotalUsers', data.total_users);
    _setText('dbTotalBillingCycles', data.total_billing_cycles);
    _setText('dbInactiveTenants', data.inactive_tenants);
  }

  // -------------------------------------------------------------------------
  // Recent tenants list
  // -------------------------------------------------------------------------
  function _renderRecentTenants(tenants) {
    const el = document.getElementById('dbRecentTenants');
    if (!el) return;

    if (!tenants.length) {
      el.innerHTML = '<p class="db-empty">No tenants yet.</p>';
      return;
    }

    el.innerHTML = '<ul class="db-tenant-list">' +
      tenants.map(t => {
        const initials = t.name.split(' ').map(w => w[0]).join('').substring(0, 2).toUpperCase();
        const statusClass = (t.status || '').toLowerCase();
        return `
          <li class="db-tenant-row">
            <div class="db-tenant-avatar">${initials}</div>
            <div class="db-tenant-info">
              <div class="db-tenant-name">${_esc(t.name)}</div>
              <div class="db-tenant-industry">${_esc(t.industry || 'Unknown Industry')}</div>
            </div>
            <span class="db-tenant-status ${statusClass}">${t.status}</span>
          </li>`;
      }).join('') + '</ul>';
  }

  // -------------------------------------------------------------------------
  // Activity feed
  // -------------------------------------------------------------------------
  function _renderActivityFeed(activities) {
    const el = document.getElementById('dbActivityFeed');
    if (!el) return;

    if (!activities.length) {
      el.innerHTML = '<p class="db-empty">No recent activity.</p>';
      return;
    }

    el.innerHTML = '<ul class="db-activity-list">' +
      activities.map(a => {
        const dotCls = _activityDotClass(a.action);
        const timeStr = _relativeTime(a.created_at);
        const actionLabel = a.action.replace(/_/g, ' ');
        return `
          <li class="db-activity-item">
            <div class="db-activity-dot ${dotCls}"></div>
            <div class="db-activity-content">
              <div class="db-activity-action">${_esc(actionLabel)}</div>
              <div class="db-activity-desc">${_esc(a.description || a.resource_type)}</div>
            </div>
            <span class="db-activity-time">${timeStr}</span>
          </li>`;
      }).join('') + '</ul>';
  }

  function _activityDotClass(action) {
    if (!action) return 'other';
    const a = action.toUpperCase();
    if (a.includes('CREATE') || a.includes('LOGIN')) return 'create';
    if (a.includes('UPDATE') || a.includes('CHANGE') || a.includes('ASSIGN')) return 'update';
    if (a.includes('DELETE') || a.includes('DEACTIVAT') || a.includes('SUSPEND')) return 'delete';
    if (a.includes('AUTH') || a.includes('LOGOUT')) return 'auth';
    return 'other';
  }

  function _relativeTime(isoString) {
    if (!isoString) return '';
    const now = new Date();
    const then = new Date(isoString);
    const diff = Math.floor((now - then) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  // -------------------------------------------------------------------------
  // Industry breakdown
  // -------------------------------------------------------------------------
  function _renderIndustryBreakdown(industries) {
    const el = document.getElementById('dbIndustryBreakdown');
    if (!el) return;

    if (!industries.length) {
      el.innerHTML = '<p class="db-empty">No industry data yet.</p>';
      return;
    }

    const max = Math.max(...industries.map(i => i.count), 1);
    el.innerHTML = industries.map((ind, idx) => {
      const pct = Math.round((ind.count / max) * 100);
      return `
        <div class="db-industry-item">
          <span class="db-industry-label" title="${_esc(ind.industry)}">${_esc(ind.industry)}</span>
          <div class="db-industry-bar-bg">
            <div class="db-industry-bar-fill bar-color-${idx % 6}" style="width:${pct}%"></div>
          </div>
          <span class="db-industry-count">${ind.count}</span>
        </div>`;
    }).join('');
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  function _setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value ?? '—';
  }

  function _esc(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  return { init, reload: _loadStats };
})();

// Auto-init when the dashboard section becomes visible
document.addEventListener('DOMContentLoaded', () => {
  // Hook into section switching if superadmin.js fires a custom event,
  // otherwise also init on page load for the default landing section
  document.querySelectorAll('[data-section="section-dashboard"]').forEach(link => {
    link.addEventListener('click', () => {
      setTimeout(DashboardModule.init, 80);
    });
  });

  // Init immediately on load (Dashboard is the default section)
  DashboardModule.init();
});
