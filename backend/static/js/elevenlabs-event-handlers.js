// ============= EVENT HANDLERS И ИНИЦИАЛИЗАЦИЯ =============

document.addEventListener('DOMContentLoaded', function() {
  // Получаем элементы
  const apiKeyContainer = document.getElementById('api-key-container');
  const agentsListContainer = document.getElementById('agents-list-container');
  const editAgentContainer = document.getElementById('edit-agent-container');
  const apiKeyForm = document.getElementById('api-key-form');
  const agentForm = document.getElementById('agent-form');
  const notification = document.getElementById('notification');
  const notificationClose = document.getElementById('notification-close');
  
  // Элементы управления API ключом
  const changeApiKeyBtn = document.getElementById('change-api-key-btn');
  const removeApiKeyBtn = document.getElementById('remove-api-key-btn');
  const changeApiKeyForm = document.getElementById('change-api-key-form');
  const updateApiKeyForm = document.getElementById('update-api-key-form');
  const cancelApiKeyChange = document.getElementById('cancel-api-key-change');
  
  // Кнопки
  const addAgentBtn = document.getElementById('add-agent-btn');
  const saveAgentBtn = document.getElementById('save-agent');
  const deleteAgentBtn = document.getElementById('delete-agent');
  const viewEmbedCodeBtn = document.getElementById('view-embed-code');
  const cancelButton = document.getElementById('cancel-button');
  const copyEmbedCodeBtn = document.getElementById('copy-embed-code');
  
  // Табы
  const tabs = document.querySelectorAll('.tab');
  const tabContents = document.querySelectorAll('.tab-content');
  
  // Слайдеры
  const temperatureSlider = document.getElementById('llm-temperature');
  const temperatureValue = document.getElementById('temperature-value');
  const maxTokensSlider = document.getElementById('llm-max-tokens');
  const maxTokensValue = document.getElementById('max-tokens-value');
  const stabilitySlider = document.getElementById('tts-stability');
  const stabilityValue = document.getElementById('stability-value');
  const similaritySlider = document.getElementById('tts-similarity');
  const similarityValue = document.getElementById('similarity-value');
  
  // ============= API KEY EVENT LISTENERS =============
  
  // API Key форма
  if (apiKeyForm) {
    apiKeyForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const apiKey = document.getElementById('api-key').value.trim();
      if (apiKey) {
        await window.saveApiKey(apiKey);
      }
    });
  }
  
  // Управление API ключом
  if (changeApiKeyBtn) {
    changeApiKeyBtn.addEventListener('click', () => {
      changeApiKeyForm.style.display = 'block';
    });
  }
  
  if (cancelApiKeyChange) {
    cancelApiKeyChange.addEventListener('click', () => {
      changeApiKeyForm.style.display = 'none';
    });
  }
  
  if (updateApiKeyForm) {
    updateApiKeyForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const newApiKey = document.getElementById('new-api-key').value.trim();
      if (newApiKey) {
        await window.saveApiKey(newApiKey);
        changeApiKeyForm.style.display = 'none';
      }
    });
  }
  
  // ✅ ИСПРАВЛЕНО: Удаление API ключа с правильным методом
  if (removeApiKeyBtn) {
    removeApiKeyBtn.addEventListener('click', async () => {
      if (confirm('Удалить API ключ? Это отключит всех агентов.')) {
        try {
          // ✅ ИСПРАВЛЕНО: Используем функцию из core.js вместо прямого API вызова
          await window.removeApiKey();
        } catch (error) {
          window.ui.showNotification(`Ошибка: ${error.message}`, 'error');
        }
      }
    });
  }
  
  // ============= ОСНОВНЫЕ КНОПКИ =============
  
  if (addAgentBtn) {
    addAgentBtn.addEventListener('click', () => window.editAgent('new'));
  }
  
  if (saveAgentBtn) {
    saveAgentBtn.addEventListener('click', window.saveAgent);
  }
  
  if (deleteAgentBtn) {
    deleteAgentBtn.addEventListener('click', window.deleteAgent);
  }
  
  if (cancelButton) {
    cancelButton.addEventListener('click', () => {
      document.getElementById('agents-list-container').style.display = 'block';
      document.getElementById('edit-agent-container').style.display = 'none';
      
      // Скрываем кнопки в хедере
      document.getElementById('delete-agent').style.display = 'none';
      document.getElementById('view-embed-code').style.display = 'none';
      document.getElementById('save-agent').style.display = 'none';
    });
  }
  
  if (copyEmbedCodeBtn) {
    copyEmbedCodeBtn.addEventListener('click', () => {
      const embedCode = document.getElementById('embed-code').textContent;
      navigator.clipboard.writeText(embedCode).then(() => {
        window.ui.showNotification('Код скопирован в буфер обмена!', 'success');
      });
    });
  }
  
  // ============= ФОРМА АГЕНТА =============
  
  if (agentForm) {
    agentForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      await window.saveAgent();
    });
  }
  
  // ============= ТАБЫ =============
  
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const tabName = tab.dataset.tab;
      
      // Обновляем активные табы
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      
      tabContents.forEach(content => {
        content.classList.remove('active');
        if (content.id === `${tabName}-tab`) {
          content.classList.add('active');
        }
      });
      
      // Рендерим контент табов
      switch (tabName) {
        case 'knowledge':
          window.renderKnowledgeBase();
          setupKnowledgeBaseHandlers();
          break;
        case 'personalization':
          window.renderDynamicVariables();
          setupPersonalizationHandlers();
          break;
        case 'tools':
          window.renderTools();
          setupToolsHandlers();
          break;
        case 'system-tools':
          window.renderSystemTools();
          setupSystemToolsHandlers();
          break;
        case 'testing':
          window.renderTesting();
          setupTestingHandlers();
          break;
        case 'embed':
          window.renderEmbed();
          window.generateEmbedCode();
          setupEmbedHandlers();
          break;
      }
    });
  });
  
  // ============= СЛАЙДЕРЫ =============
  
  if (temperatureSlider) {
    temperatureSlider.addEventListener('input', (e) => {
      temperatureValue.textContent = e.target.value;
    });
  }
  
  if (maxTokensSlider) {
    maxTokensSlider.addEventListener('input', (e) => {
      maxTokensValue.textContent = e.target.value;
    });
  }
  
  if (stabilitySlider) {
    stabilitySlider.addEventListener('input', (e) => {
      stabilityValue.textContent = e.target.value;
    });
  }
  
  if (similaritySlider) {
    similaritySlider.addEventListener('input', (e) => {
      similarityValue.textContent = e.target.value;
    });
  }
  
  // ============= УВЕДОМЛЕНИЯ =============
  
  if (notificationClose) {
    notificationClose.addEventListener('click', () => {
      notification.classList.remove('show');
      setTimeout(() => {
        notification.style.display = 'none';
      }, 300);
    });
  }
  
  // ============= ИНИЦИАЛИЗАЦИЯ =============
  
  // Проверяем API ключ при загрузке
  window.checkApiKey();
});

// ============= ФУНКЦИИ НАСТРОЙКИ ОБРАБОТЧИКОВ ДЛЯ ТАБОВ =============

function setupKnowledgeBaseHandlers() {
  // Knowledge Base обработчики
  const fileUploadArea = document.getElementById('file-upload-area');
  const fileUploadInput = document.getElementById('file-upload-input');
  const fileSelectText = document.getElementById('file-select-text');
  const knowledgeUrl = document.getElementById('knowledge-url');
  const addUrlBtn = document.getElementById('add-url-btn');
  const knowledgeText = document.getElementById('knowledge-text');
  const addTextBtn = document.getElementById('add-text-btn');
  
  // Drag & Drop для файлов
  if (fileUploadArea) {
    fileUploadArea.addEventListener('dragover', (e) => {
      e.preventDefault();
      fileUploadArea.classList.add('drag-over');
    });
    
    fileUploadArea.addEventListener('dragleave', () => {
      fileUploadArea.classList.remove('drag-over');
    });
    
    fileUploadArea.addEventListener('drop', (e) => {
      e.preventDefault();
      fileUploadArea.classList.remove('drag-over');
      const files = Array.from(e.dataTransfer.files);
      // ✅ РЕАЛЬНАЯ загрузка файлов через функцию из paste.txt
      window.handleFileUpload(files);
    });
  }
  
  if (fileSelectText) {
    fileSelectText.addEventListener('click', () => {
      fileUploadInput.click();
    });
  }
  
  if (fileUploadInput) {
    fileUploadInput.addEventListener('change', (e) => {
      const files = Array.from(e.target.files);
      // ✅ РЕАЛЬНАЯ загрузка файлов через функцию из paste.txt
      window.handleFileUpload(files);
      e.target.value = ''; // Сброс для повторного выбора
    });
  }
  
  // ✅ ИСПРАВЛЕНО: URL добавление через реальный API
  if (addUrlBtn) {
    addUrlBtn.addEventListener('click', async () => {
      const url = knowledgeUrl.value.trim();
      if (!url) return;
      
      if (!isValidUrl(url)) {
        window.ui.showNotification('Введите корректный URL', 'error');
        return;
      }
      
      // ✅ РЕАЛЬНАЯ обработка URL через функцию из paste.txt
      await window.handleUrlAdd(url);
    });
  }
  
  // ✅ ИСПРАВЛЕНО: Текст добавление через реальный API
  if (addTextBtn) {
    addTextBtn.addEventListener('click', async () => {
      const text = knowledgeText.value.trim();
      if (!text) return;
      
      // ✅ РЕАЛЬНАЯ обработка текста через функцию из paste.txt
      await window.handleTextAdd(text);
    });
  }
}

function setupPersonalizationHandlers() {
  const addVariableBtn = document.getElementById('add-variable-btn');
  
  if (addVariableBtn) {
    addVariableBtn.addEventListener('click', () => {
      window.dynamicVariables.push({ name: '', value: '' });
      window.renderVariablesList();
    });
  }
}

function setupToolsHandlers() {
  const addServerToolBtn = document.getElementById('add-server-tool-btn');
  const addClientToolBtn = document.getElementById('add-client-tool-btn');
  
  if (addServerToolBtn) {
    addServerToolBtn.addEventListener('click', () => {
      window.serverTools.push({
        name: '',
        description: '',
        url: '',
        method: 'GET',
        parameters: ''
      });
      window.renderServerTools();
    });
  }
  
  if (addClientToolBtn) {
    addClientToolBtn.addEventListener('click', () => {
      window.clientTools.push({
        name: '',
        description: '',
        parameters: '',
        code: ''
      });
      window.renderClientTools();
    });
  }
}

function setupSystemToolsHandlers() {
  const enableEndCall = document.getElementById('enable-end-call');
  const enableAgentTransfer = document.getElementById('enable-agent-transfer');
  const enableHumanHandoff = document.getElementById('enable-human-handoff');
  const enableLanguageDetection = document.getElementById('enable-language-detection');
  
  if (enableEndCall) {
    enableEndCall.addEventListener('change', (e) => {
      window.systemTools.endCall = e.target.checked;
      window.toggleSystemToolConfig('end-call', e.target.checked);
    });
  }
  
  if (enableAgentTransfer) {
    enableAgentTransfer.addEventListener('change', (e) => {
      window.systemTools.agentTransfer = e.target.checked;
      window.toggleSystemToolConfig('agent-transfer', e.target.checked);
    });
  }
  
  if (enableHumanHandoff) {
    enableHumanHandoff.addEventListener('change', (e) => {
      window.systemTools.humanHandoff = e.target.checked;
      window.toggleSystemToolConfig('human-handoff', e.target.checked);
    });
  }
  
  if (enableLanguageDetection) {
    enableLanguageDetection.addEventListener('change', (e) => {
      window.systemTools.languageDetection = e.target.checked;
      window.toggleSystemToolConfig('language-detection', e.target.checked);
    });
  }
}

function setupTestingHandlers() {
  const startTestBtn = document.getElementById('start-test-btn');
  const stopTestBtn = document.getElementById('stop-test-btn');
  const retryConnectionBtn = document.getElementById('retry-connection-btn');
  const testMicrophoneBtn = document.getElementById('test-microphone-btn');
  
  if (startTestBtn) {
    startTestBtn.addEventListener('click', async () => {
      if (!window.currentAgentId || window.currentAgentId === 'new') {
        window.ui.showNotification('Сначала сохраните агента', 'warning');
        return;
      }
      
      window.conversationManager = new window.EnhancedElevenLabsConversationManager();
      const success = await window.conversationManager.startConversation(window.currentAgentId);
      
      if (!success) {
        window.conversationManager = null;
      }
    });
  }
  
  if (stopTestBtn) {
    stopTestBtn.addEventListener('click', async () => {
      if (window.conversationManager) {
        await window.conversationManager.stopConversation();
        window.conversationManager = null;
      }
    });
  }
  
  if (retryConnectionBtn) {
    retryConnectionBtn.addEventListener('click', async () => {
      if (window.conversationManager) {
        await window.conversationManager.stopConversation();
      }
      
      setTimeout(async () => {
        window.conversationManager = new window.EnhancedElevenLabsConversationManager();
        await window.conversationManager.startConversation(window.currentAgentId);
      }, 1000);
    });
  }
  
  if (testMicrophoneBtn) {
    testMicrophoneBtn.addEventListener('click', async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(track => track.stop());
        window.ui.showNotification('Микрофон работает исправно!', 'success');
      } catch (error) {
        window.ui.showNotification(`Ошибка микрофона: ${error.message}`, 'error');
      }
    });
  }
}

function setupEmbedHandlers() {
  const copyEmbedCodeBtn = document.getElementById('copy-embed-code');
  
  if (copyEmbedCodeBtn) {
    copyEmbedCodeBtn.addEventListener('click', () => {
      const embedCode = document.getElementById('embed-code').textContent;
      navigator.clipboard.writeText(embedCode).then(() => {
        window.ui.showNotification('Код скопирован в буфер обмена!', 'success');
      });
    });
  }
}

// ============= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =============

function isValidUrl(string) {
  try {
    new URL(string);
    return true;
  } catch (_) {
    return false;
  }
}

// ============= ЭКСПОРТ ФУНКЦИЙ =============

window.setupKnowledgeBaseHandlers = setupKnowledgeBaseHandlers;
window.setupPersonalizationHandlers = setupPersonalizationHandlers;
window.setupToolsHandlers = setupToolsHandlers;
window.setupSystemToolsHandlers = setupSystemToolsHandlers;
window.setupTestingHandlers = setupTestingHandlers;
window.setupEmbedHandlers = setupEmbedHandlers;
window.isValidUrl = isValidUrl;
