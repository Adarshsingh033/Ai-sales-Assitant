/**
 * Web Chat Simulator Controller
 * Provides an in-dashboard interactive widget to test the AI Assistant in real time.
 * Supports persistent session continuity, dynamic identity, real-time token streaming,
 * and an animated AI thinking indicator.
 */

const ChatSimulatorModule = (() => {
  let isOpen = false;
  let isStreaming = false;
  let lastMessageCount = 0;
  let lastMessageId = null;
  let simPollTimer = null;

  let testPhone = localStorage.getItem('sim_customer_phone') || '+15550199001';
  let testName = localStorage.getItem('sim_customer_name') || 'Alex Mercer';
  let currentSessionId = localStorage.getItem('sim_session_id') || null;

  let currentConfig = {
    assistant_name: 'AI Assistant',
    greeting_message: "Hello! I'm your AI Sales Assistant. How can I help you explore our products or services today?",
    personality: 'Friendly'
  };

  function escapeHtml(str) {
    if (!str) return '';
    return str.toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatMsg(rawText) {
    if (!rawText) return '';
    const cleaned = rawText.trim().split('\n').map(l => l.trimStart()).join('\n');
    return escapeHtml(cleaned);
  }

  function updateStatusIndicator(convStatus) {
    const stageEl = document.getElementById('simLeadStage');
    if (!stageEl) return;
    if (convStatus === 'ESCALATED_HUMAN' || convStatus === 'HUMAN_AGENT') {
      stageEl.innerHTML = '<span style="color:#b45309; font-weight:700;">👤 HUMAN TAKEOVER</span>';
    } else if (convStatus === 'RESOLVED') {
      stageEl.innerHTML = '<span style="color:#059669; font-weight:700;">✅ RESOLVED</span>';
    }
  }

  async function loadConfig() {
    try {
      const cfg = await Api.getAIConfig();
      if (cfg) {
        if (cfg.assistant_name) currentConfig.assistant_name = cfg.assistant_name;
        if (cfg.greeting_message) currentConfig.greeting_message = cfg.greeting_message;
        if (cfg.personality) currentConfig.personality = cfg.personality;
        updateSimulatorIdentity();
      }
    } catch (err) {
      console.warn('Could not load AI config for simulator:', err);
    }
  }

  function updateSimulatorIdentity() {
    // Update drawer header title
    const titleEl = document.querySelector('#chatSimulatorDrawer .chat-sim-header-title span');
    if (titleEl) {
      titleEl.textContent = `🤖 ${currentConfig.assistant_name} - Sales Simulator`;
    }

    // Update toggle button text if present
    const toggleBtnSpan = document.querySelector('#chatSimulatorToggle span');
    if (toggleBtnSpan) {
      toggleBtnSpan.textContent = `Test ${currentConfig.assistant_name}`;
    }
  }

  function renderMessageList(history) {
    const messagesContainer = document.getElementById('simMessagesContainer');
    if (!messagesContainer) return;

    if (!Array.isArray(history) || history.length === 0) {
      messagesContainer.innerHTML = `
        <div class="message-bubble-wrapper assistant">
          <span class="message-sender-label">${escapeHtml(currentConfig.assistant_name)} 🤖</span>
          <div class="message-bubble assistant">${formatMsg(currentConfig.greeting_message)}</div>
        </div>
      `;
      lastMessageCount = 0;
      lastMessageId = null;
      return;
    }

    messagesContainer.innerHTML = history.map(m => {
      const isAI = m.sender_type === 'AI' || m.sender_type === 'AI_ASSISTANT';
      const isAgent = m.sender_type === 'HUMAN_AGENT';
      let senderLabel;
      let bubbleClass;

      if (isAI) {
        senderLabel = `${escapeHtml(currentConfig.assistant_name)} 🤖`;
        bubbleClass = 'assistant';
      } else if (isAgent) {
        senderLabel = 'Sales Rep 👤';
        bubbleClass = 'agent';
      } else {
        senderLabel = escapeHtml(testName);
        bubbleClass = 'customer';
      }

      return `
        <div class="message-bubble-wrapper ${bubbleClass}">
          <span class="message-sender-label">${senderLabel}</span>
          <div class="message-bubble ${bubbleClass}">${formatMsg(m.content)}</div>
        </div>
      `;
    }).join('');

    lastMessageCount = history.length;
    lastMessageId = history[history.length - 1]?.id || null;
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  async function syncMessages() {
    if (isStreaming || !currentSessionId) return;
    try {
      const [conv, msgList] = await Promise.all([
        Api.getConversation(currentSessionId).catch(() => null),
        Api.getConversationMessages(currentSessionId).catch(() => null)
      ]);

      if (conv) {
        updateStatusIndicator(conv.status);
      }

      const history = (Array.isArray(msgList) && msgList.length > 0)
        ? msgList
        : (Array.isArray(conv?.messages) ? conv.messages : []);

      if (!Array.isArray(history) || history.length === 0) return;

      const lastMsg = history[history.length - 1];
      if (history.length === lastMessageCount && lastMsg && lastMsg.id === lastMessageId) {
        return; // nothing changed
      }

      renderMessageList(history);
    } catch (err) {
      // Ignore background sync errors silently
    }
  }

  async function switchSession(convId, phone, name) {
    if (!convId) return;
    currentSessionId = convId;
    localStorage.setItem('sim_session_id', convId);

    if (phone) {
      testPhone = phone;
      localStorage.setItem('sim_customer_phone', testPhone);
      const phoneInput = document.getElementById('simCustomerPhone');
      if (phoneInput) phoneInput.value = testPhone;
    }
    if (name) {
      testName = name;
      localStorage.setItem('sim_customer_name', testName);
      const nameInput = document.getElementById('simCustomerName');
      if (nameInput) nameInput.value = testName;
    }

    lastMessageCount = 0;
    lastMessageId = null;
    await syncMessages();
  }

  function startSimPolling() {
    if (simPollTimer) return;
    simPollTimer = setInterval(() => {
      if (isOpen && currentSessionId && !isStreaming) {
        syncMessages();
      }
    }, 2500);
  }

  function stopSimPolling() {
    if (simPollTimer) {
      clearInterval(simPollTimer);
      simPollTimer = null;
    }
  }

  function toggleDrawer() {
    isOpen = !isOpen;
    const drawer = document.getElementById('chatSimulatorDrawer');
    if (!drawer) return;

    if (isOpen) {
      drawer.classList.add('open');
      const input = document.getElementById('simMessageInput');
      if (input) input.focus();
      startSimPolling();
      syncMessages();
    } else {
      drawer.classList.remove('open');
      stopSimPolling();
    }
  }

  async function restoreSessionHistory() {
    const phoneInput = document.getElementById('simCustomerPhone');
    if (phoneInput) phoneInput.value = testPhone;

    const nameInput = document.getElementById('simCustomerName');
    if (nameInput) nameInput.value = testName;

    const messagesContainer = document.getElementById('simMessagesContainer');
    if (!messagesContainer) return;

    // If no currentSessionId but testPhone is set, look up customer's active session
    if (!currentSessionId && testPhone) {
      try {
        const convList = await Api.getConversations({ search: testPhone, channel: 'WEB_SIMULATOR' });
        if (convList?.items?.length > 0) {
          currentSessionId = convList.items[0].id;
          localStorage.setItem('sim_session_id', currentSessionId);
        }
      } catch (e) {
        console.warn('Could not lookup active conversation for simulator phone:', e);
      }
    }

    if (currentSessionId) {
      try {
        const [conv, msgList] = await Promise.all([
          Api.getConversation(currentSessionId).catch(() => null),
          Api.getConversationMessages(currentSessionId).catch(() => null)
        ]);

        if (conv) {
          updateStatusIndicator(conv.status);
          if (conv.customer_name && conv.customer_name !== testName) {
            testName = conv.customer_name;
            localStorage.setItem('sim_customer_name', testName);
            if (nameInput) nameInput.value = testName;
          }
        }

        const history = (Array.isArray(msgList) && msgList.length > 0)
          ? msgList
          : (Array.isArray(conv?.messages) ? conv.messages : []);

        if (Array.isArray(history) && history.length > 0) {
          renderMessageList(history);
          return;
        }
      } catch (err) {
        console.warn('Could not load previous session history, starting fresh view:', err);
      }
    }

    // Default initial greeting bubble
    renderMessageList([]);
    const scoreEl = document.getElementById('simLeadScore');
    if (scoreEl) scoreEl.textContent = 'Score: —';

    const stageEl = document.getElementById('simLeadStage');
    if (stageEl) stageEl.textContent = 'Stage: NEW';
  }

  async function resetSession() {
    // Archive previous simulation conversation on the backend
    try {
      await Api.resetSimulatorSession({ customer_phone: testPhone });
    } catch (e) {
      console.warn('Error archiving session:', e);
    }

    // Clear session ID from memory and localStorage
    currentSessionId = null;
    localStorage.removeItem('sim_session_id');
    lastMessageCount = 0;
    lastMessageId = null;

    // Generate a fresh test phone number for the new customer session
    const randomSuffix = Math.floor(1000 + Math.random() * 9000);
    testPhone = `+1555019${randomSuffix}`;
    localStorage.setItem('sim_customer_phone', testPhone);

    const phoneInput = document.getElementById('simCustomerPhone');
    if (phoneInput) phoneInput.value = testPhone;

    renderMessageList([]);

    const scoreEl = document.getElementById('simLeadScore');
    if (scoreEl) scoreEl.textContent = 'Score: —';

    const stageEl = document.getElementById('simLeadStage');
    if (stageEl) stageEl.textContent = 'Stage: NEW';

    // Refresh inbox if Conversations module is loaded
    if (typeof ConversationsModule !== 'undefined' && ConversationsModule.loadConversations) {
      ConversationsModule.loadConversations();
    }
  }

  async function sendMessage() {
    const input = document.getElementById('simMessageInput');
    const sendBtn = document.getElementById('btnSimSend');
    const msg = input?.value?.trim();
    if (!msg) return;

    isStreaming = true;

    const phoneInput = document.getElementById('simCustomerPhone');
    if (phoneInput?.value?.trim()) {
      testPhone = phoneInput.value.trim();
      localStorage.setItem('sim_customer_phone', testPhone);
    }
    const nameInput = document.getElementById('simCustomerName');
    if (nameInput?.value?.trim()) {
      testName = nameInput.value.trim();
      localStorage.setItem('sim_customer_name', testName);
    }

    input.value = '';
    if (sendBtn) sendBtn.disabled = true;

    const messagesContainer = document.getElementById('simMessagesContainer');
    if (!messagesContainer) return;

    // 1. Append customer message bubble
    messagesContainer.innerHTML += `
      <div class="message-bubble-wrapper customer">
        <span class="message-sender-label">${escapeHtml(testName)}</span>
        <div class="message-bubble customer">${formatMsg(msg)}</div>
      </div>
    `;

    // 2. Prepare assistant bubble with animated Thinking Indicator
    const bubbleWrapperId = 'streamWrapper_' + Date.now();
    const bubbleContentId = 'streamBubble_' + Date.now();
    const assistantName = currentConfig.assistant_name || 'AI Assistant';

    messagesContainer.innerHTML += `
      <div id="${bubbleWrapperId}" class="message-bubble-wrapper assistant">
        <span class="message-sender-label">${escapeHtml(assistantName)} 🤖</span>
        <div id="${bubbleContentId}" class="message-bubble assistant" style="min-height: 32px;">
          <div class="ai-thinking-wrapper">
            <div class="ai-thinking-dots">
              <span class="ai-thinking-dot"></span>
              <span class="ai-thinking-dot"></span>
              <span class="ai-thinking-dot"></span>
            </div>
            <span class="ai-thinking-label">${escapeHtml(assistantName)} is thinking...</span>
          </div>
        </div>
      </div>
    `;
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    const bubbleEl = document.getElementById(bubbleContentId);
    let accumulatedText = '';
    let isFirstToken = true;
    let tokenQueue = [];
    let isStreamDone = false;
    let streamTimer = null;
    let finalMeta = null;

    function applyFinalMetadata(meta) {
      if (meta.session_id) {
        currentSessionId = meta.session_id;
        localStorage.setItem('sim_session_id', currentSessionId);
      }

      if (meta.human_takeover) {
        updateStatusIndicator('ESCALATED_HUMAN');
      }

      // Update Live Intel Header
      const score = meta.score ?? meta.qualification_score;
      const intentLabel = meta.intent ? meta.intent.replace(/_/g, ' ') : '';
      const stageLabel = meta.stage || (score >= 60 ? 'QUALIFIED' : (score >= 40 ? 'WARM' : 'COLD'));

      const scoreEl = document.getElementById('simLeadScore');
      if (scoreEl && score !== undefined && score !== null) {
        const scoreClass = score >= 70 ? 'score-hot' : score >= 40 ? 'score-warm' : 'score-cold';
        scoreEl.innerHTML = `Score: <span class="lead-score-pill ${scoreClass}">${Math.round(score)}/100</span>`;
      }

      const stageEl = document.getElementById('simLeadStage');
      if (stageEl && !meta.human_takeover) {
        stageEl.textContent = intentLabel ? `Intent: ${intentLabel}` : `Stage: ${stageLabel}`;
      }

      // Refresh leads / live inbox / dashboard tabs in background if loaded
      if (typeof ConversationsModule !== 'undefined' && ConversationsModule.loadConversations) {
        ConversationsModule.loadConversations(currentSessionId);
      }
      if (typeof LeadsModule !== 'undefined' && LeadsModule.loadLeads) {
        LeadsModule.loadLeads();
      }
      if (typeof TenantDashboardModule !== 'undefined' && TenantDashboardModule.refresh) {
        TenantDashboardModule.refresh();
      }
    }

    function renderLoop() {
      if (tokenQueue.length > 0) {
        const chunk = tokenQueue.shift();
        if (isFirstToken) {
          isFirstToken = false;
          accumulatedText = chunk;
        } else {
          accumulatedText += chunk;
        }

        if (bubbleEl) {
          bubbleEl.innerHTML = escapeHtml(accumulatedText) + '<span class="stream-cursor">▌</span>';
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
      }

      if (tokenQueue.length > 0 || !isStreamDone) {
        const delay = tokenQueue.length > 15 ? 8 : (tokenQueue.length > 5 ? 15 : 22);
        streamTimer = setTimeout(renderLoop, delay);
      } else {
        // Stream completed and visual typewriter queue finished
        isStreaming = false;
        if (finalMeta?.human_takeover) {
          // Remove the AI thinking bubble wrapper
          const wrapper = document.getElementById(bubbleWrapperId);
          if (wrapper) wrapper.remove();

          // Append takeover notice pill
          const existingNotice = document.getElementById('simTakeoverNotice');
          if (!existingNotice) {
            messagesContainer.innerHTML += `
              <div id="simTakeoverNotice" class="sim-takeover-notice">
                <span>👤 Human Sales Representative is active. Your message was delivered.</span>
              </div>
            `;
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
          }
        } else if (bubbleEl) {
          const finalReply = (finalMeta && finalMeta.reply) || accumulatedText || 'Thank you for reaching out!';
          bubbleEl.innerHTML = formatMsg(finalReply);
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        if (finalMeta) {
          applyFinalMetadata(finalMeta);
        }
        if (sendBtn) sendBtn.disabled = false;
        if (input) input.focus();
        streamTimer = null;
      }
    }

    const payload = {
      message: msg,
      session_id: currentSessionId,
      customer_name: testName,
      customer_phone: testPhone,
    };

    // 3. Initiate SSE Streaming from Groq
    await Api.simulateChatStream(
      payload,
      // onToken: Feed token into smooth typewriter queue
      (token) => {
        tokenQueue.push(token);
        if (!streamTimer) {
          renderLoop();
        }
      },
      // onDone: Signal completion to typewriter queue
      (meta) => {
        isStreamDone = true;
        finalMeta = meta;
        if (!streamTimer) {
          renderLoop();
        }
      },
      // onError
      (err) => {
        console.error('Chat simulator error:', err);
        isStreaming = false;
        isStreamDone = true;
        if (sendBtn) sendBtn.disabled = false;
        if (bubbleEl) {
          if (!accumulatedText && tokenQueue.length === 0) {
            bubbleEl.parentElement.innerHTML = `
              <span class="message-sender-label" style="color:#e11d48;">Error</span>
              <div class="message-bubble" style="background:#fee2e2; color:#b91c1c;">
                Error communicating with AI: ${escapeHtml(err.message || 'Stream interrupted')}
              </div>
            `;
          } else {
            bubbleEl.innerHTML = escapeHtml(accumulatedText);
          }
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
      }
    );
  }

  function setupEvents() {
    const toggleBtn = document.getElementById('chatSimulatorToggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', toggleDrawer);
    }

    const closeBtn = document.getElementById('btnSimClose');
    if (closeBtn) {
      closeBtn.addEventListener('click', toggleDrawer);
    }

    const resetBtn = document.getElementById('btnSimReset');
    if (resetBtn) {
      resetBtn.addEventListener('click', resetSession);
    }

    const sendBtn = document.getElementById('btnSimSend');
    if (sendBtn) {
      sendBtn.addEventListener('click', sendMessage);
    }

    const input = document.getElementById('simMessageInput');
    if (input) {
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          sendMessage();
        }
      });
    }

    // Save inputs when manually edited
    const phoneInput = document.getElementById('simCustomerPhone');
    if (phoneInput) {
      phoneInput.addEventListener('change', async (e) => {
        testPhone = e.target.value.trim();
        localStorage.setItem('sim_customer_phone', testPhone);
        try {
          const convList = await Api.getConversations({ search: testPhone, channel: 'WEB_SIMULATOR' });
          if (convList?.items?.length > 0) {
            currentSessionId = convList.items[0].id;
            localStorage.setItem('sim_session_id', currentSessionId);
            lastMessageCount = 0;
            lastMessageId = null;
            await syncMessages();
          }
        } catch (err) {}
      });
    }
    const nameInput = document.getElementById('simCustomerName');
    if (nameInput) {
      nameInput.addEventListener('change', (e) => {
        testName = e.target.value.trim();
        localStorage.setItem('sim_customer_name', testName);
      });
    }
  }

  return {
    async init() {
      setupEvents();
      await loadConfig();
      await restoreSessionHistory();
    },
    async reloadConfig() {
      await loadConfig();
    },
    getAssistantName() {
      return currentConfig.assistant_name || 'AI Assistant';
    },
    toggleDrawer,
    resetSession,
    sendMessage,
    syncMessages,
    switchSession
  };
})();

window.ChatSimulatorModule = ChatSimulatorModule;
