/**
 * In-App & Browser Push Notifications Module
 * Provides real-time bell counter, dropdown list, sound alerts, and web notifications.
 */

const NotificationsModule = (() => {
  let pollingInterval = null;
  let unreadCount = 0;
  let audioCtx = null;

  function init() {
    bindEvents();
    fetchUnreadCount();
    startPolling();
    requestDesktopPermission();
  }

  function bindEvents() {
    const bellBtn = document.getElementById('btnNotifBell');
    const dropdown = document.getElementById('notifDropdown');
    const markAllBtn = document.getElementById('btnMarkAllRead');

    if (bellBtn && dropdown) {
      bellBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = dropdown.classList.contains('active');
        if (!isOpen) {
          openDropdown();
        } else {
          closeDropdown();
        }
      });

      document.addEventListener('click', (e) => {
        if (!dropdown.contains(e.target) && e.target !== bellBtn) {
          closeDropdown();
        }
      });
    }

    if (markAllBtn) {
      markAllBtn.addEventListener('click', async () => {
        try {
          await Api.markAllNotificationsRead();
          unreadCount = 0;
          updateBadge();
          await loadNotifications();
          if (window.showToast) window.showToast('All notifications marked as read', 'success');
        } catch (err) {
          console.error('Error marking all as read:', err);
        }
      });
    }
  }

  function playAlertChime() {
    try {
      if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
      // Crisp pleasant two-tone sales alert (C5 -> E5)
      const now = audioCtx.currentTime;
      const osc1 = audioCtx.createOscillator();
      const gain1 = audioCtx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(523.25, now);
      osc1.frequency.exponentialRampToValueAtTime(659.25, now + 0.12);
      gain1.gain.setValueAtTime(0.15, now);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
      osc1.connect(gain1);
      gain1.connect(audioCtx.destination);
      osc1.start(now);
      osc1.stop(now + 0.35);
    } catch (e) {
      // Audio context not allowed before user gesture
    }
  }

  function requestDesktopPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }

  function triggerDesktopNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
      try {
        new Notification(title, {
          body: body,
          icon: '/favicon.ico',
        });
      } catch (e) {
        // ignore
      }
    }
  }

  async function fetchUnreadCount() {
    try {
      const res = await Api.getUnreadNotificationCount();
      const prevCount = unreadCount;
      unreadCount = res.unread_count || 0;
      updateBadge();

      if (unreadCount > prevCount && prevCount >= 0) {
        playAlertChime();
        // Check latest notification for desktop alert
        const recent = await Api.getNotifications(1);
        if (recent && recent.length > 0) {
          triggerDesktopNotification(recent[0].title, recent[0].message);
        }
      }
    } catch (err) {
      // ignore background polling errors
    }
  }

  function updateBadge() {
    const badge = document.getElementById('notifBadge');
    if (!badge) return;
    if (unreadCount > 0) {
      badge.textContent = unreadCount > 99 ? '99+' : unreadCount;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  }

  async function openDropdown() {
    const dropdown = document.getElementById('notifDropdown');
    if (!dropdown) return;
    dropdown.classList.add('active');
    await loadNotifications();
  }

  function closeDropdown() {
    const dropdown = document.getElementById('notifDropdown');
    if (dropdown) dropdown.classList.remove('active');
  }

  async function loadNotifications() {
    const listEl = document.getElementById('notifList');
    if (!listEl) return;
    listEl.innerHTML = '<div style="padding:16px;text-align:center;color:#94a3b8;font-size:12px;">Loading...</div>';

    try {
      const items = await Api.getNotifications(20);
      if (!items || items.length === 0) {
        listEl.innerHTML = '<div class="notif-empty">No notifications yet</div>';
        return;
      }

      listEl.innerHTML = items.map((it) => {
        let icon = '🔔';
        if (it.type === 'HOT_LEAD') icon = '🔥';
        else if (it.type === 'HUMAN_TAKEOVER_REQUESTED') icon = '🚨';
        else if (it.type === 'INVENTORY_LOW') icon = '⚠️';
        else if (it.type === 'FOLLOWUP_DUE') icon = '📩';

        const unreadClass = !it.is_read ? 'unread' : '';
        const timeAgo = formatTimeAgo(it.created_at);

        return `
          <div class="notif-item ${unreadClass}" onclick="NotificationsModule.handleItemClick('${it.id}', '${it.link_url || ''}')">
            <div class="notif-item-icon">${icon}</div>
            <div class="notif-item-content">
              <div class="notif-item-title">${escapeHtml(it.title)}</div>
              <div class="notif-item-msg">${escapeHtml(it.message)}</div>
              <div class="notif-item-time">${timeAgo}</div>
            </div>
          </div>
        `;
      }).join('');
    } catch (err) {
      listEl.innerHTML = '<div class="notif-empty">Failed to load notifications</div>';
    }
  }

  async function handleItemClick(id, linkUrl) {
    try {
      await Api.markNotificationRead(id);
      unreadCount = Math.max(0, unreadCount - 1);
      updateBadge();
    } catch (e) {
      // ignore
    }

    closeDropdown();

    if (linkUrl) {
      // Links look like "#tasks", "#pipeline?lead=<id>" or the older "#/leads?id=<id>"
      const legacy = { leads: 'pipeline', conversations: 'inbox', products: 'products' };
      const [rawKey, query] = linkUrl.replace(/^#\/?/, '').split('?');
      const key = legacy[rawKey] || rawKey;
      const params = Object.fromEntries(new URLSearchParams(query || ''));
      if (params.id && !params.lead && key === 'pipeline') { params.lead = params.id; delete params.id; }
      if (window.Crm && Crm.sectionId(key)) Crm.goto(key, Object.keys(params).length ? params : undefined);
    }
  }

  function startPolling() {
    if (pollingInterval) clearInterval(pollingInterval);
    pollingInterval = setInterval(fetchUnreadCount, 8000);
  }

  function formatTimeAgo(isoStr) {
    if (!isoStr) return '';
    const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function escapeHtml(text) {
    if (!text) return '';
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  return {
    init,
    fetchUnreadCount,
    handleItemClick,
  };
})();

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
  NotificationsModule.init();
});
