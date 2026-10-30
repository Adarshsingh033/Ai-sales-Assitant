/**
 * AI Assistant Configuration Controller
 * Manages assistant personality, prompt instructions, qualification questions, and WhatsApp integration.
 */

const AIConfigModule = (() => {

  async function loadConfig() {
    try {
      const config = await Api.getAIConfig();
      populateForm(config);
      setupWebhookUrl();
    } catch (err) {
      console.error('Failed to load AI config:', err);
      if (window.showToast) window.showToast('Could not load AI settings: ' + err.message, 'error');
    }
  }

  function populateForm(config) {
    if (!config) return;

    const setValue = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val ?? '';
    };

    setValue('aiAssistantName', config.assistant_name || 'Ava');
    setValue('aiTone', config.personality || config.tone || 'Friendly');
    setValue('aiGroqModel', config.groq_model || 'openai/gpt-oss-20b');
    setValue('aiGreeting', config.greeting_message || 'Hello! How can I assist you today?');
    setValue('aiSystemPrompt', config.custom_instructions || config.system_prompt || '');
    setValue('aiMaxDiscount', config.max_discount_percent ?? config.max_discount_pct ?? 10);

    const questionsArea = document.getElementById('aiQualificationQuestions');
    if (questionsArea) {
      const q = config.qualification_questions;
      questionsArea.value = Array.isArray(q) ? q.join('\n') : (q || '');
    }

    const handoffInput = document.getElementById('aiHandoffTriggers');
    if (handoffInput) {
      const h = config.handoff_triggers;
      handoffInput.value = Array.isArray(h) ? h.join(', ') : (h || '');
    }

    setValue('aiWhatsappVerifyToken', config.whatsapp_verify_token || '');
    setValue('aiWhatsappAccessToken', config.whatsapp_access_token || '');
    setValue('aiWhatsappPhoneId', config.whatsapp_phone_number_id || '');

    const autoFollowEl = document.getElementById('aiAutoFollowup');
    if (autoFollowEl) autoFollowEl.checked = config.auto_followup_enabled !== false;

    setValue('aiFollowupDelayHours', config.followup_delay_hours || 24);

    const roundRobinEl = document.getElementById('aiRoundRobin');
    if (roundRobinEl) roundRobinEl.checked = config.round_robin_assignment !== false;

    const notifSoundEl = document.getElementById('aiNotificationSound');
    if (notifSoundEl) notifSoundEl.checked = config.notification_sound_enabled !== false;
  }

  function setupWebhookUrl() {
    const webhookUrl = `${CONFIG.API_ROOT}/webhooks/whatsapp`;
    const input = document.getElementById('aiWhatsappWebhookUrl');
    if (input) input.value = webhookUrl;
  }

  function copyWebhookUrl() {
    const input = document.getElementById('aiWhatsappWebhookUrl');
    if (input && input.value) {
      navigator.clipboard.writeText(input.value).then(() => {
        if (window.showToast) window.showToast('WhatsApp Webhook URL copied to clipboard!', 'success');
      }).catch(() => {
        input.select();
        document.execCommand('copy');
        if (window.showToast) window.showToast('WhatsApp Webhook URL copied to clipboard!', 'success');
      });
    }
  }

  async function saveConfig(e) {
    if (e) e.preventDefault();

    const groqKeyInput = document.getElementById('aiGroqApiKey');
    const groqKeyVal = groqKeyInput ? groqKeyInput.value.trim() : '';

    const payload = {
      assistant_name: document.getElementById('aiAssistantName')?.value.trim() || 'Ava',
      personality: document.getElementById('aiTone')?.value || 'Friendly',
      groq_model: document.getElementById('aiGroqModel')?.value || 'openai/gpt-oss-20b',
      greeting_message: document.getElementById('aiGreeting')?.value.trim() || 'Hello! How can I assist you today?',
      custom_instructions: document.getElementById('aiSystemPrompt')?.value.trim() || null,
      max_discount_percent: parseFloat(document.getElementById('aiMaxDiscount')?.value) || 0,
      whatsapp_verify_token: document.getElementById('aiWhatsappVerifyToken')?.value.trim() || null,
      whatsapp_access_token: document.getElementById('aiWhatsappAccessToken')?.value.trim() || null,
      whatsapp_phone_number_id: document.getElementById('aiWhatsappPhoneId')?.value.trim() || null,
      auto_followup_enabled: document.getElementById('aiAutoFollowup') ? document.getElementById('aiAutoFollowup').checked : true,
      followup_delay_hours: parseInt(document.getElementById('aiFollowupDelayHours')?.value, 10) || 24,
      round_robin_assignment: document.getElementById('aiRoundRobin') ? document.getElementById('aiRoundRobin').checked : true,
      notification_sound_enabled: document.getElementById('aiNotificationSound') ? document.getElementById('aiNotificationSound').checked : true,
    };

    if (groqKeyVal) {
      payload.groq_api_key = groqKeyVal;
    }

    const btnSave = document.getElementById('btnSaveAiConfig');
    if (btnSave) {
      btnSave.disabled = true;
      btnSave.textContent = 'Saving...';
    }

    try {
      await Api.updateAIConfig(payload);
      if (groqKeyInput) groqKeyInput.value = '';
      if (window.showToast) window.showToast('AI Assistant configuration updated successfully!', 'success');
      if (window.ChatSimulatorModule && window.ChatSimulatorModule.reloadConfig) {
        window.ChatSimulatorModule.reloadConfig();
      }
    } catch (err) {
      if (window.showToast) window.showToast('Failed to save configuration: ' + err.message, 'error');
    } finally {
      if (btnSave) {
        btnSave.disabled = false;
        btnSave.textContent = 'Save Changes';
      }
    }
  }

  function setupEvents() {
    const form = document.getElementById('aiConfigForm');
    if (form) {
      form.addEventListener('submit', saveConfig);
    }

    const btnCopy = document.getElementById('btnCopyWebhookUrl');
    if (btnCopy) {
      btnCopy.addEventListener('click', copyWebhookUrl);
    }
  }

  return {
    init() {
      setupEvents();
      loadConfig();
    },
    saveConfig,
    copyWebhookUrl
  };
})();

window.AIConfigModule = AIConfigModule;
