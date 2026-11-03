/**
 * Conversations & Live Inbox Controller
 * Supports 2-pane inbox, message history, live human takeover, and human replies.
 */

const ConversationsModule = (() => {
  let conversations = [];
  let selectedConvId = null;
  let selectedLeadId = null; // the deal behind the open chat, for the "Open deal" button
  let dealListenerAdded = false;
  let activeFilter = ''; // '', 'ACTIVE_AI', 'HUMAN_TAKEOVER', 'RESOLVED'
  let searchTimeout = null;

  function formatDate(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function formatTime(isoStr) {
    if (!isoStr) return '';
    return new Date(isoStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str.toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function renderMessageContent(rawText) {
    if (!rawText) return '';
    // Strip leading spaces from every line so lines never start with accidental indentation
    const cleaned = rawText.trim().split('\n').map(l => l.trimStart()).join('\n');
    let text = escapeHtml(cleaned);
    // Convert markdown links: [label](url) -> <a href="url" target="_blank" rel="noopener noreferrer">$1</a>
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    // Convert bold text: **text** -> <strong>text</strong>
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return text.trim();
  }

  function getStatusBadge(status) {
    if (status === 'ACTIVE_AI') {
      return `<span class="conv-status-badge ai"><span class="pulse-dot"></span> AI Active</span>`;
    } else if (status === 'ESCALATED_HUMAN' || status === 'HUMAN_TAKEOVER') {
      return `<span class="conv-status-badge human">Human Takeover</span>`;
    } else if (status === 'RESOLVED') {
      return `<span class="conv-status-badge resolved">Resolved</span>`;
    }
    return `<span class="conv-status-badge">${escapeHtml(status || 'Active')}</span>`;
  }

  async function loadConversations(autoSelectId = null) {
    const searchInput = document.getElementById('inboxSearchInput');
    const params = {
      search: searchInput?.value?.trim() || '',
      status: activeFilter || '',
      page_size: 50
    };

    const threadList = document.getElementById('inboxThreadList');
    if (threadList && conversations.length === 0) {
      threadList.innerHTML = `<li class="db-empty">Loading conversations...</li>`;
    }

    try {
      const res = await Api.getConversations(params);
      conversations = res.items || [];
      renderThreadList();

      if (autoSelectId) {
        selectConversation(autoSelectId);
      } else if (selectedConvId) {
        // Refresh currently active conversation messages so new messages appear in real time!
        selectConversation(selectedConvId, false);
      } else if (conversations.length > 0) {
        selectConversation(conversations[0].id);
      }
    } catch (err) {
      console.error('Failed to load conversations:', err);
      if (threadList) threadList.innerHTML = `<li class="db-empty" style="color:#e11d48;">Error: ${escapeHtml(err.message)}</li>`;
    }
  }

  function renderThreadList() {
    const threadList = document.getElementById('inboxThreadList');
    if (!threadList) return;

    if (conversations.length === 0) {
      threadList.innerHTML = `<li class="db-empty">No conversations found.</li>`;
      return;
    }

    threadList.innerHTML = conversations.map(c => {
      const custName = c.customer_name || c.customer?.name || c.customer_phone || c.customer?.phone || 'Customer';
      const isActive = c.id === selectedConvId;
      const lastMsg = c.last_message_preview || 'No messages';

      return `
        <li class="inbox-thread-item ${isActive ? 'active' : ''}" onclick="ConversationsModule.selectConversation('${c.id}')">
          <div class="inbox-thread-top">
            <span class="inbox-customer-name">${escapeHtml(custName)}</span>
            <span class="inbox-thread-time">${formatDate(c.last_activity_at || c.updated_at || c.created_at)}</span>
          </div>
          <div class="inbox-thread-snippet">${escapeHtml(lastMsg)}</div>
          <div class="inbox-thread-meta">
            <span class="channel-badge ${c.channel?.toLowerCase()}">${escapeHtml(c.channel || 'WHATSAPP')}</span>
            ${getStatusBadge(c.status)}
          </div>
        </li>
      `;
    }).join('');
  }

  async function selectConversation(convId, updateThreadList = true) {
    selectedConvId = convId;
    if (updateThreadList) {
      renderThreadList();
    }

    const emptyPane = document.getElementById('inboxEmptyPane');
    const activePane = document.getElementById('inboxActivePane');
    if (emptyPane) emptyPane.style.display = 'none';
    if (activePane) activePane.style.display = 'flex';

    try {
      const [conv, messages] = await Promise.all([
        Api.getConversation(convId),
        Api.getConversationMessages(convId)
      ]);
      conv.messages = messages;
      renderChatPane(conv);

      if (conv.channel === 'WEB_SIMULATOR' && window.ChatSimulatorModule && typeof window.ChatSimulatorModule.switchSession === 'function') {
        window.ChatSimulatorModule.switchSession(convId, conv.customer_phone, conv.customer_name);
      }
    } catch (err) {
      console.error('Failed to fetch conversation details:', err);
      if (window.showToast) window.showToast('Could not load message history', 'error');
    }
  }

  function renderChatPane(conv) {
    const cust = conv.customer || {};
    const name = conv.customer_name || cust.name || conv.customer_phone || cust.phone || 'Anonymous Customer';
    const phone = conv.customer_phone || cust.phone || '';
    const initials = name.slice(0, 2).toUpperCase();

    // Customer info header
    document.getElementById('inboxChatAvatar').textContent = initials;
    document.getElementById('inboxChatCustomerName').textContent = name;
    document.getElementById('inboxChatCustomerPhone').textContent = phone ? `${phone} • ${conv.channel}` : conv.channel;
    document.getElementById('inboxChatStatusBadge').innerHTML = getStatusBadge(conv.status);

    const isHumanTakeover = conv.status === 'ESCALATED_HUMAN' || conv.status === 'HUMAN_TAKEOVER';

    // Actions
    const btnTakeover = document.getElementById('btnTakeover');
    const btnHandback = document.getElementById('btnHandback');
    const btnResolve = document.getElementById('btnResolve');

    if (btnTakeover) btnTakeover.style.display = conv.status === 'ACTIVE_AI' ? 'inline-block' : 'none';
    if (btnHandback) btnHandback.style.display = isHumanTakeover ? 'inline-block' : 'none';
    if (btnResolve) btnResolve.style.display = conv.status !== 'RESOLVED' ? 'inline-block' : 'none';
    // talk, then close or hand over: the deal behind this chat is one click away
    selectedLeadId = conv.lead_id || null;
    const btnOpenDeal = document.getElementById('btnOpenDeal');
    if (btnOpenDeal) btnOpenDeal.style.display = selectedLeadId ? 'inline-block' : 'none';

    // Composer input & notice banner
    const noticeBar = document.getElementById('inboxTakeoverNotice');
    const replyInput = document.getElementById('inboxReplyInput');
    const btnSend = document.getElementById('btnSendReply');

    if (conv.status === 'ACTIVE_AI') {
      if (noticeBar) {
        noticeBar.style.display = 'flex';
        noticeBar.style.background = '#fffbeb';
        noticeBar.style.color = '#92400e';
        noticeBar.innerHTML = `<span>🤖 AI Assistant is actively replying to this customer.</span>
          <button class="btn-primary" style="padding:4px 10px; font-size:12px;" onclick="ConversationsModule.takeover()">Take Over as Human</button>`;
      }
      if (replyInput) replyInput.disabled = true;
      if (btnSend) btnSend.disabled = true;
    } else if (isHumanTakeover) {
      if (noticeBar) {
        noticeBar.style.display = 'flex';
        noticeBar.style.background = '#ffedd5';
        noticeBar.style.color = '#9a3412';
        noticeBar.innerHTML = `<span>👤 You have taken over this conversation. AI responses are paused.</span>
          <button class="btn-secondary" style="padding:4px 10px; font-size:12px;" onclick="ConversationsModule.handback()">Return to AI</button>`;
      }
      if (replyInput) replyInput.disabled = false;
      if (btnSend) btnSend.disabled = false;
    } else {
      if (noticeBar) {
        noticeBar.style.display = 'flex';
        noticeBar.style.background = '#ecfdf5';
        noticeBar.style.color = '#065f46';
        noticeBar.innerHTML = `<span>✅ Conversation marked as resolved.</span>`;
      }
      if (replyInput) replyInput.disabled = true;
      if (btnSend) btnSend.disabled = true;
    }

    // Render messages
    const messagesContainer = document.getElementById('inboxMessagesContainer');
    if (!messagesContainer) return;

    const msgs = conv.messages || [];
    if (msgs.length === 0) {
      messagesContainer.innerHTML = `<div class="db-empty">No messages in this conversation yet.</div>`;
    } else {
      messagesContainer.innerHTML = msgs.map(m => {
        let typeClass = 'customer';
        let senderName = cust.name || 'Customer';

        if (m.sender_type === 'AI' || m.sender_type === 'AI_ASSISTANT') {
          typeClass = 'assistant';
          const asstName = (window.ChatSimulatorModule && typeof window.ChatSimulatorModule.getAssistantName === 'function') 
            ? window.ChatSimulatorModule.getAssistantName() 
            : 'AI Assistant';
          senderName = `${asstName} 🤖`;
        } else if (m.sender_type === 'HUMAN_AGENT') {
          typeClass = 'agent';
          senderName = 'Sales Rep 👤';
        }

        return `
          <div class="message-bubble-wrapper ${typeClass}">
            <span class="message-sender-label">${escapeHtml(senderName)}</span>
            <div class="message-bubble ${typeClass}">${renderMessageContent(m.content)}</div>
            <span class="message-time">${formatTime(m.created_at)}</span>
          </div>
        `;
      }).join('');

      // Scroll to bottom reliably
      requestAnimationFrame(() => {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
      });
    }
  }

  async function takeover() {
    if (!selectedConvId) return;
    try {
      await Api.takeoverConversation(selectedConvId);
      if (window.showToast) window.showToast('Human takeover active. You can now reply.', 'success');
      selectConversation(selectedConvId);
      loadConversations();
      if (window.ChatSimulatorModule && typeof window.ChatSimulatorModule.syncMessages === 'function') {
        window.ChatSimulatorModule.syncMessages();
      }
    } catch (err) {
      if (window.showToast) window.showToast('Failed to take over: ' + err.message, 'error');
    }
  }

  async function handback() {
    if (!selectedConvId) return;
    try {
      await Api.handbackConversation(selectedConvId);
      if (window.showToast) window.showToast('Conversation handed back to AI Assistant.', 'success');
      selectConversation(selectedConvId);
      loadConversations();
      if (window.ChatSimulatorModule && typeof window.ChatSimulatorModule.syncMessages === 'function') {
        window.ChatSimulatorModule.syncMessages();
      }
    } catch (err) {
      if (window.showToast) window.showToast('Failed to hand back: ' + err.message, 'error');
    }
  }

  async function resolve() {
    if (!selectedConvId) return;
    try {
      await Api.resolveConversation(selectedConvId);
      if (window.showToast) window.showToast('Conversation marked as resolved.', 'success');
      selectConversation(selectedConvId);
      loadConversations();
      if (window.ChatSimulatorModule && typeof window.ChatSimulatorModule.syncMessages === 'function') {
        window.ChatSimulatorModule.syncMessages();
      }
    } catch (err) {
      if (window.showToast) window.showToast('Failed to resolve: ' + err.message, 'error');
    }
  }

  /** Open the deal behind this chat: that is where it is closed with feedback, or handed to a teammate. */
  function openDeal() {
    if (selectedLeadId && typeof LeadsModule !== 'undefined') LeadsModule.openLeadDetail(selectedLeadId);
  }

  /** A deal was closed or handed over (here or on the Pipeline): the chat may be resolved, or no longer ours to show. */
  async function refreshAfterDealChange() {
    if (selectedConvId) {
      try {
        await Api.getConversation(selectedConvId);
      } catch (err) {
        selectedConvId = null;
        selectedLeadId = null;
        const emptyPane = document.getElementById('inboxEmptyPane');
        const activePane = document.getElementById('inboxActivePane');
        if (emptyPane) emptyPane.style.display = 'flex';
        if (activePane) activePane.style.display = 'none';
      }
    }
    loadConversations();
  }

  async function sendReply() {
    if (!selectedConvId) return;
    const input = document.getElementById('inboxReplyInput');
    const text = input?.value?.trim();
    if (!text) return;

    input.value = '';
    try {
      await Api.sendHumanReply(selectedConvId, text);
      selectConversation(selectedConvId);
      loadConversations();
      if (window.ChatSimulatorModule && typeof window.ChatSimulatorModule.syncMessages === 'function') {
        window.ChatSimulatorModule.syncMessages();
      }
    } catch (err) {
      if (window.showToast) window.showToast('Failed to send reply: ' + err.message, 'error');
    }
  }

  function filterByCustomer(phoneOrId) {
    const searchInput = document.getElementById('inboxSearchInput');
    if (searchInput) {
      searchInput.value = phoneOrId;
      loadConversations();
    }
  }

  let initialized = false;
  let pollInterval = null;

  function startPolling() {
    if (pollInterval) return;
    pollInterval = setInterval(() => {
      const convSection = document.getElementById('section-conversations');
      if (convSection && !convSection.hidden) {
        loadConversations();
      }
    }, 4000);
  }

  function setupEvents() {
    if (initialized) return;
    initialized = true;

    // Search input
    const searchInput = document.getElementById('inboxSearchInput');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => loadConversations(), 300);
      });
    }

    // Filter tabs
    const filterBtns = document.querySelectorAll('.inbox-filter-btn');
    filterBtns.forEach(btn => {
      btn.addEventListener('click', (e) => {
        filterBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeFilter = btn.dataset.filter || '';
        loadConversations();
      });
    });

    // Send reply on Enter key
    const replyInput = document.getElementById('inboxReplyInput');
    if (replyInput) {
      replyInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendReply();
        }
      });
    }
  }

  return {
    init() {
      setupEvents();
      startPolling();
      loadConversations();
      if (!dealListenerAdded && typeof Crm !== 'undefined') { // init() runs on every visit to the Inbox
        dealListenerAdded = true;
        Crm.on('deals-changed', refreshAfterDealChange);
      }
    },
    loadConversations,
    selectConversation,
    takeover,
    handback,
    resolve,
    openDeal,
    sendReply,
    filterByCustomer
  };
})();

window.ConversationsModule = ConversationsModule;
