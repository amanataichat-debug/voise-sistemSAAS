/* ============================================================ */
/* Voicyfy AI - Main Application                                */
/* Voice LLM Interface - Voicyfy                                */
/* Version: 4.3.2 - IIFE wrapped                                */
/* ============================================================ */

'use strict';

(function() {

// ============================================================================
// GET MODULES (use Config.xxx - no destructuring to avoid redeclaration)
// ============================================================================

const Config = window.VoicyfyConfig;
const Audio = window.VoicyfyAudio;

// ============================================================================
// APPLICATION STATE
// ============================================================================

const urlParams = new URLSearchParams(window.location.search);
let ASSISTANT_ID = urlParams.get('assistant') || null;

// FIX: Все глобальные переменные с window. prefix для консистентности
window.userActivated = false;
window.websocket = null;
window.isConnected = false;
window.isReconnecting = false;
window.isStreamingLLM = false;

// Локальные переменные (не нужны в других модулях)
let llmWebSocket = null;
let isLLMConnected = false;
let reconnectAttempts = 0;
let llmReconnectAttempts = 0;
let pingInterval = null;
let lastPongTime = Date.now();

// LLM state
let streamingContent = "";
let currentRequestId = null;
let currentLLMContent = '';

// Agent Mode state
let agentConfig = null;
let isAgentMode = false;
let currentAgentRequestId = null;
let currentPlan = [];
let agentConfigId = null;

// Three.js state
let threeScene, threeCamera, threeRenderer, threeParticles;
let threeInitialized = false;

// ============================================================================
// DOM READY
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    Config.log('🚀 Voicyfy AI Interface v4.3.2 Starting...');
    Config.log(`   Mode: Gemini Voice (WS1) + LLM Text (WS2) + Agent Mode`);
    Config.log(`   Mute: Enabled`);
    Config.log(`   Assistant ID: ${ASSISTANT_ID || 'Not configured'}`);

    initializeApp();
    // initAgentMode() is called inside loadGeminiAssistantsList() after ASSISTANT_ID is resolved,
    // or here for embed mode where ASSISTANT_ID is already set from URL params
    if (urlParams.has('assistant')) {
        initAgentMode();
    }
    initAgentUI();
});

// ============================================================================
// INITIALIZATION
// ============================================================================

function initializeApp() {
    // Setup event listeners
    setupEventListeners();
    
    // Initialize visualizer
    Audio.createCircularVisualizer();
    
    // Initialize Three.js background
    initThreeJS();
    
    // Load assistants if not in embed mode
    const isEmbedMode = urlParams.has('assistant');
    if (!isEmbedMode) {
        loadGeminiAssistantsList();
    } else {
        Config.log('📦 Running in EMBED mode');
    }
    
    // Initialize chat history
    updateContextInfo();
    renderChatHistory();
    
    // Set initial status
    updateStatus('connecting', 'Нажмите чтобы начать', 'connecting');
}

function setupEventListeners() {
    // Start overlay
    const startButton = document.getElementById('startButton');
    const startOverlay = document.getElementById('startOverlay');
    
    if (startButton) {
        startButton.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            activateAudioAndStart();
        });
    }
    
    if (startOverlay) {
        startOverlay.addEventListener('click', function(e) {
            if (e.target === startOverlay) {
                activateAudioAndStart();
            }
        });
    }
    
    // Mute button
    const muteButton = document.getElementById('muteButton');
    if (muteButton) {
        muteButton.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            Audio.toggleMute();
        });
    }
    
    // Sphere click
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.addEventListener('click', handleSphereClick);
    }
    
    // Config buttons
    const saveBtn = document.getElementById('saveBtn');
    const testBtn = document.getElementById('testBtn');
    const copyBtn = document.getElementById('copyBtn');
    
    if (saveBtn) {
        saveBtn.addEventListener('click', saveConfig);
    }
    if (testBtn) {
        testBtn.addEventListener('click', testAgent);
    }
    if (copyBtn) {
        copyBtn.addEventListener('click', copyHTMLCode);
    }
    
    // Copy LLM response
    const copyLlmButton = document.getElementById('copyLlmButton');
    if (copyLlmButton) {
        copyLlmButton.addEventListener('click', copyLLMResponse);
    }
    
    // Mobile menu
    const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    
    if (mobileMenuToggle && sidebar) {
        mobileMenuToggle.addEventListener('click', function() {
            sidebar.classList.toggle('mobile-open');
            if (sidebarOverlay) sidebarOverlay.classList.toggle('show');
        });
    }
    
    if (sidebarOverlay && sidebar) {
        sidebarOverlay.addEventListener('click', function() {
            sidebar.classList.remove('mobile-open');
            sidebarOverlay.classList.remove('show');
        });
    }
    
    // Chat input
    const chatInput = document.getElementById('chatInput');
    const chatSendBtn = document.getElementById('chatSendBtn');
    
    if (chatInput && chatSendBtn) {
        chatInput.addEventListener('input', function() {
            chatSendBtn.disabled = !this.value.trim() || !isLLMConnected || window.isStreamingLLM;
        });
        
        chatInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!chatSendBtn.disabled) {
                    sendTextMessage();
                }
            }
        });
        
        chatSendBtn.addEventListener('click', sendTextMessage);
    }
    
    // Clear history
    const clearHistoryBtn = document.getElementById('clearHistoryBtn');
    if (clearHistoryBtn) {
        clearHistoryBtn.addEventListener('click', clearChatHistory);
    }
    
    // Window resize for Three.js
    window.addEventListener('resize', handleWindowResize);
}

// ============================================================================
// USER ACTIVATION
// ============================================================================

async function activateAudioAndStart() {
    Config.log('🎬 User activation triggered');
    
    try {
        const audioSuccess = await Audio.initializeAudio();
        if (!audioSuccess) {
            Config.log('❌ Audio initialization failed', 'error');
        }
        
        Audio.initPlaybackAudioContext();
        window.userActivated = true;
        
        const startOverlay = document.getElementById('startOverlay');
        if (startOverlay) {
            startOverlay.classList.add('hidden');
        }
        
        Config.log('✅ Overlay hidden, audio activated');
        
        if (ASSISTANT_ID) {
            Config.log('🔌 Connecting WebSocket after user activation...');
            connectWebSocket();
        } else {
            updateStatus('connecting', 'Выберите Gemini ассистента', 'connecting');
        }
        
    } catch (error) {
        Config.log(`❌ Activation error: ${error}`, 'error');
        const startOverlay = document.getElementById('startOverlay');
        if (startOverlay) {
            startOverlay.classList.add('hidden');
        }
        window.userActivated = true;
    }
}

// ============================================================================
// SPHERE CLICK HANDLER
// ============================================================================

async function handleSphereClick() {
    if (!window.userActivated) {
        const startOverlay = document.getElementById('startOverlay');
        if (startOverlay) {
            startOverlay.classList.remove('hidden');
        }
        return;
    }
    
    if (!window.audioInitialized) {
        const success = await Audio.initializeAudio();
        if (!success) return;
    }
    
    Audio.initPlaybackAudioContext();
    
    const isListening = Audio.getIsListening();
    const isPlayingAudio = Audio.getIsPlayingAudio();
    const isMuted = Audio.getMuteState();
    
    // FIX: Используем window.isStreamingLLM и window.isReconnecting
    if (!isListening && !isPlayingAudio && !window.isReconnecting && !window.isStreamingLLM && !isMuted) {
        if (window.isConnected) {
            Audio.startListening();
        } else {
            connectWebSocket();
        }
    }
}

// ============================================================================
// ASSISTANT MANAGEMENT
// ============================================================================

async function loadGeminiAssistantsList() {
    const assistantSelect = document.getElementById('assistantSelect');
    const testBtn = document.getElementById('testBtn');
    const copyBtn = document.getElementById('copyBtn');
    
    if (!assistantSelect) {
        Config.log('❌ assistantSelect element not found', 'error');
        return;
    }
    
    try {
        Config.log('📋 Loading Gemini assistants...');
        
        const response = await fetch(`${Config.SERVER_URL}/api/gemini-assistants`, {
            headers: {
                'Authorization': `Bearer ${getAuthToken()}`
            }
        });
        
        if (!response.ok) {
            throw new Error(`Failed to load Gemini assistants: ${response.status}`);
        }
        
        const assistants = await response.json();
        Config.log(`   Loaded: ${assistants.length} assistants`);
        
        assistantSelect.innerHTML = '<option value="">-- Выберите Gemini ассистента --</option>';
        
        assistants.forEach(assistant => {
            const option = document.createElement('option');
            option.value = assistant.id;
            option.textContent = `💎 ${assistant.name || assistant.id}`;
            assistantSelect.appendChild(option);
        });
        
        // Restore from URL or localStorage
        if (ASSISTANT_ID && assistantSelect.querySelector(`option[value="${ASSISTANT_ID}"]`)) {
            assistantSelect.value = ASSISTANT_ID;
            if (testBtn) testBtn.disabled = false;
            if (copyBtn) copyBtn.disabled = false;
            Config.log(`   Using assistant from URL: ${ASSISTANT_ID}`);
        } else {
            const savedAssistantId = localStorage.getItem(Config.STORAGE_KEY_ASSISTANT);
            if (savedAssistantId && assistantSelect.querySelector(`option[value="${savedAssistantId}"]`)) {
                assistantSelect.value = savedAssistantId;
                ASSISTANT_ID = savedAssistantId;
                if (testBtn) testBtn.disabled = false;
                if (copyBtn) copyBtn.disabled = false;
                Config.log(`   Restored from localStorage: ${savedAssistantId}`);
            }
        }
        
        // Load agent config for the resolved assistant
        await initAgentMode();

    } catch (error) {
        Config.log(`❌ Error loading assistants: ${error}`, 'error');
        assistantSelect.innerHTML = '<option value="">Ошибка загрузки</option>';
    }
}

function getAuthToken() {
    return localStorage.getItem('auth_token') ||
           document.cookie.split('; ').find(row => row.startsWith('auth_token='))?.split('=')[1] ||
           '';
}

// ============================================================================
// CONFIG ACTIONS
// ============================================================================

async function saveConfig() {
    const assistantSelect = document.getElementById('assistantSelect');
    const saveBtn = document.getElementById('saveBtn');
    const testBtn = document.getElementById('testBtn');
    const copyBtn = document.getElementById('copyBtn');
    
    if (!assistantSelect) return;
    
    const selectedAssistantId = assistantSelect.value;

    if (!selectedAssistantId) {
        alert('❌ Пожалуйста, выберите Gemini ассистента!');
        return;
    }

    try {
        setLoading(true);
        ASSISTANT_ID = selectedAssistantId;
        localStorage.setItem(Config.STORAGE_KEY_ASSISTANT, selectedAssistantId);
        Config.log(`💾 Assistant saved: ${selectedAssistantId}`);

        if (testBtn) testBtn.disabled = false;
        if (copyBtn) copyBtn.disabled = false;
        showSuccess('✅ Gemini ассистент сохранён!');

        // Re-init agent mode for the new assistant
        await initAgentMode();

        if (window.userActivated) {
            if (window.websocket) window.websocket.close();
            if (llmWebSocket) {
                llmWebSocket.close();
                llmWebSocket = null;
                isLLMConnected = false;
            }
            connectWebSocket();
        }

    } catch (error) {
        Config.log(`❌ Save error: ${error}`, 'error');
        alert('❌ Ошибка: ' + error.message);
    } finally {
        setLoading(false);
    }
}

function testAgent() {
    if (!ASSISTANT_ID) {
        alert('❌ Сначала выберите ассистента!');
        return;
    }

    if (!window.userActivated) {
        const startOverlay = document.getElementById('startOverlay');
        if (startOverlay) {
            startOverlay.classList.remove('hidden');
        }
        return;
    }

    if (!window.isConnected) {
        showSuccess('🔄 Подключение к Gemini...');
        connectWebSocket();
    } else {
        showSuccess('✅ Готов! Нажмите на сферу и говорите');
    }
}

function copyHTMLCode() {
    if (!ASSISTANT_ID) {
        alert('❌ Сначала выберите ассистента!');
        return;
    }

    const embedUrl = `${Config.SERVER_URL}/static/voice_llm_interface/?assistant=${ASSISTANT_ID}`;
    const embedCode = `<!-- Voicyfy AI (Gemini) Widget -->
<iframe
    src="${embedUrl}"
    width="100%"
    height="800px"
    frameborder="0"
    allow="microphone"
    style="border-radius: 16px; box-shadow: 0 4px 16px rgba(0,0,0,0.1);">
</iframe>`;

    navigator.clipboard.writeText(embedCode).then(() => {
        showSuccess('✅ Код скопирован в буфер обмена!');
    }).catch(() => {
        // Fallback
        const textarea = document.createElement('textarea');
        textarea.value = embedCode;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showSuccess('✅ Код скопирован!');
    });
}

function setLoading(isLoading) {
    const saveBtn = document.getElementById('saveBtn');
    const testBtn = document.getElementById('testBtn');
    const copyBtn = document.getElementById('copyBtn');
    
    if (isLoading) {
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Сохранение...';
        }
        if (testBtn) testBtn.disabled = true;
        if (copyBtn) copyBtn.disabled = true;
    } else {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="fas fa-save"></i> Сохранить';
        }
    }
}

// ============================================================================
// UI HELPERS
// ============================================================================

function showSuccess(message) {
    const successMessage = document.getElementById('successMessage');
    const successText = document.getElementById('successText');
    
    if (successText) successText.textContent = message;
    if (successMessage) {
        successMessage.classList.add('show');
        setTimeout(() => successMessage.classList.remove('show'), 3000);
    }
}

function showError(title, text) {
    const errorContainer = document.getElementById('errorContainer');
    const errorTitle = document.getElementById('errorTitle');
    const errorText = document.getElementById('errorText');
    
    if (errorTitle) errorTitle.textContent = title;
    if (errorText) errorText.textContent = text;
    if (errorContainer) errorContainer.classList.add('active');
}

window.updateStatus = function(status, text, className) {
    const statusText = document.getElementById('statusText');
    const voiceStatus = document.getElementById('voiceStatus');
    
    if (statusText) statusText.textContent = text;
    if (voiceStatus) voiceStatus.className = `voice-status ${className}`;
};

// ============================================================================
// CHAT HISTORY (sessionStorage)
// ============================================================================

function loadChatHistory() {
    try {
        const saved = sessionStorage.getItem(Config.HISTORY_STORAGE_KEY);
        return saved ? JSON.parse(saved) : [];
    } catch (e) {
        Config.log(`Chat history load error: ${e}`, 'error');
        return [];
    }
}

function saveChatHistory(history) {
    try {
        sessionStorage.setItem(Config.HISTORY_STORAGE_KEY, JSON.stringify(history));
    } catch (e) {
        Config.log(`Chat history save error: ${e}`, 'error');
    }
}

function addToHistory(role, content) {
    const history = loadChatHistory();
    history.push({ role, content, timestamp: Date.now() });
    
    // Keep only last N pairs
    while (history.length > Config.MAX_HISTORY_PAIRS * 2) {
        history.shift();
    }
    
    saveChatHistory(history);
    updateContextInfo();
    return history;
}

function clearChatHistory() {
    sessionStorage.removeItem(Config.HISTORY_STORAGE_KEY);
    updateContextInfo();
    renderChatHistory();
    showSuccess('🗑️ История очищена');
}

function updateContextInfo() {
    const history = loadChatHistory();
    const pairs = Math.floor(history.length / 2);
    const contextInfo = document.getElementById('chatContextInfo');
    const clearBtn = document.getElementById('clearHistoryBtn');
    
    if (contextInfo) {
        contextInfo.textContent = `Контекст: ${pairs}/${Config.MAX_HISTORY_PAIRS} сообщений`;
    }
    if (clearBtn) {
        clearBtn.style.display = history.length > 0 ? 'block' : 'none';
    }
}

function renderChatHistory() {
    const history = loadChatHistory();
    const llmContent = document.getElementById('llmContent');
    const placeholder = document.getElementById('llmPlaceholder');
    
    if (!llmContent) return;
    
    if (history.length === 0) {
        if (placeholder) {
            llmContent.innerHTML = '';
            llmContent.appendChild(placeholder);
            placeholder.style.display = 'flex';
        }
        return;
    }
    
    if (placeholder) placeholder.style.display = 'none';
    
    let html = '';
    history.forEach(msg => {
        const roleLabel = msg.role === 'user' ? 'Вы' : 'ИИ';
        html += `
            <div class="chat-message ${msg.role}">
                <div class="chat-message-role">${roleLabel}</div>
                <div class="chat-message-content">${formatMarkdown(msg.content)}</div>
            </div>
        `;
    });
    
    llmContent.innerHTML = html;
    llmContent.scrollTop = llmContent.scrollHeight;
}

// ============================================================================
// TEXT CHAT
// ============================================================================

function sendTextMessage() {
    const input = document.getElementById('chatInput');
    const message = input ? input.value.trim() : '';
    
    if (!message) return;
    if (!isLLMConnected) {
        showSuccess('❌ LLM не подключен');
        return;
    }
    if (window.isStreamingLLM) {
        showSuccess('⏳ Дождитесь завершения ответа');
        return;
    }
    
    // Add user message to history
    addToHistory('user', message);
    
    // Clear input
    input.value = '';
    input.disabled = true;
    
    const sendBtn = document.getElementById('chatSendBtn');
    if (sendBtn) sendBtn.disabled = true;
    
    // Start streaming UI
    startStreamingUI(message);
    
    // Get history for context (without current message)
    const history = loadChatHistory();
    const contextHistory = history.slice(0, -1);
    
    // Send to WebSocket
    if (llmWebSocket && llmWebSocket.readyState === WebSocket.OPEN) {
        llmWebSocket.send(JSON.stringify({
            type: 'llm.query',
            query: message,
            history: contextHistory,
            request_id: `text_${Date.now()}`
        }));
        
        Config.log(`📝 Sent text message with ${contextHistory.length} history items`);
    }
}

// ============================================================================
// LLM STREAMING
// ============================================================================

function startStreamingUI(query) {
    Config.log(`📝 Starting LLM streaming for: ${query.substring(0, 50)}...`);
    
    // FIX: Используем window.isStreamingLLM
    window.isStreamingLLM = true;
    streamingContent = "";
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('listening', 'speaking', 'processing', 'muted');
        voicyfySphere.classList.add('streaming');
    }
    
    updateStatus('streaming', 'Генерация ответа...', 'streaming');
    
    const llmMeta = document.getElementById('llmMeta');
    if (llmMeta) llmMeta.textContent = 'Streaming...';
    
    // Render history + streaming message
    appendStreamingMessage();
    
    const copyButton = document.getElementById('copyLlmButton');
    if (copyButton) copyButton.style.display = 'none';
}

function appendStreamingMessage() {
    const llmContent = document.getElementById('llmContent');
    const placeholder = document.getElementById('llmPlaceholder');
    
    if (!llmContent) return;
    
    if (placeholder) placeholder.style.display = 'none';
    
    const history = loadChatHistory();
    let html = '';
    
    history.forEach(msg => {
        const roleLabel = msg.role === 'user' ? 'Вы' : 'ИИ';
        html += `
            <div class="chat-message ${msg.role}">
                <div class="chat-message-role">${roleLabel}</div>
                <div class="chat-message-content">${formatMarkdown(msg.content)}</div>
            </div>
        `;
    });
    
    // Add streaming container
    html += `
        <div class="chat-message assistant streaming" id="streamingMessage">
            <div class="chat-message-role">ИИ</div>
            <div class="chat-message-content" id="streamingText"></div>
            <span class="streaming-cursor"></span>
        </div>
    `;
    
    llmContent.innerHTML = html;
    llmContent.scrollTop = llmContent.scrollHeight;
}

function appendStreamingText(content) {
    if (!window.isStreamingLLM) return;
    streamingContent += content;
    
    const streamingText = document.getElementById('streamingText');
    const llmContent = document.getElementById('llmContent');
    
    if (streamingText) {
        streamingText.innerHTML = formatMarkdown(streamingContent);
        if (llmContent) llmContent.scrollTop = llmContent.scrollHeight;
    }
}

function finishStreamingUI(data) {
    Config.log('✅ LLM streaming finished');
    
    // FIX: Используем window.isStreamingLLM
    window.isStreamingLLM = false;
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('streaming');
        if (Audio.getMuteState()) {
            voicyfySphere.classList.add('muted');
        }
    }
    
    const isMuted = Audio.getMuteState();
    if (isMuted) {
        updateStatus('muted', 'Микрофон отключён', 'muted');
    } else {
        updateStatus('connected', 'Готов', 'connected');
    }
    
    const fullContent = data.full_content || streamingContent;
    
    // Save AI response to history
    addToHistory('assistant', fullContent);
    
    // Render full history
    renderChatHistory();
    
    // Unlock input
    const input = document.getElementById('chatInput');
    const sendBtn = document.getElementById('chatSendBtn');
    if (input) {
        input.disabled = false;
        input.focus();
    }
    if (sendBtn) {
        sendBtn.disabled = !input || !input.value.trim();
    }
    
    const llmMeta = document.getElementById('llmMeta');
    if (llmMeta && data.duration_ms) {
        const seconds = (data.duration_ms / 1000).toFixed(1);
        llmMeta.textContent = `${data.model || 'gpt-4o-mini'} • ${seconds}s`;
    }
    
    // Show copy button
    const copyButton = document.getElementById('copyLlmButton');
    if (copyButton) copyButton.style.display = 'flex';
    
    currentLLMContent = formatMarkdown(fullContent);
    
    streamingContent = "";
    currentRequestId = null;
}

function showStreamingError(message) {
    Config.log(`❌ LLM streaming error: ${message}`, 'error');
    
    // FIX: Используем window.isStreamingLLM
    window.isStreamingLLM = false;
    streamingContent = "";
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('streaming');
        if (Audio.getMuteState()) {
            voicyfySphere.classList.add('muted');
        }
    }
    
    updateStatus('error', 'Ошибка', 'error');
    
    // Show history + error
    const history = loadChatHistory();
    const llmContent = document.getElementById('llmContent');
    const llmMeta = document.getElementById('llmMeta');
    
    if (llmContent) {
        let html = '';
        history.forEach(msg => {
            const roleLabel = msg.role === 'user' ? 'Вы' : 'ИИ';
            html += `
                <div class="chat-message ${msg.role}">
                    <div class="chat-message-role">${roleLabel}</div>
                    <div class="chat-message-content">${formatMarkdown(msg.content)}</div>
                </div>
            `;
        });
        
        html += `
            <div class="llm-error">
                <div class="llm-error-icon">❌</div>
                <div class="llm-error-title">Ошибка генерации</div>
                <div class="llm-error-text">${message}</div>
            </div>
        `;
        
        llmContent.innerHTML = html;
    }
    
    if (llmMeta) llmMeta.textContent = 'Ошибка';
    
    // Unlock input
    const input = document.getElementById('chatInput');
    const sendBtn = document.getElementById('chatSendBtn');
    if (input) {
        input.disabled = false;
        input.focus();
    }
    if (sendBtn) {
        sendBtn.disabled = !input || !input.value.trim() || !isLLMConnected;
    }
    
    setTimeout(() => {
        const isMuted = Audio.getMuteState();
        if (isMuted) {
            updateStatus('muted', 'Микрофон отключён', 'muted');
        } else {
            updateStatus('connected', 'Готов', 'connected');
        }
    }, 3000);
}

// ============================================================================
// TEXT FORMATTING
// ============================================================================

function formatMarkdown(text) {
    if (!text) return '<p>Нет контента</p>';
    return text
        .replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        .replace(/^### (.*$)/gim, '<h3>$1</h3>')
        .replace(/^## (.*$)/gim, '<h2>$1</h2>')
        .replace(/^# (.*$)/gim, '<h1>$1</h1>')
        .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
        .replace(/^\* (.+)$/gim, '<li>$1</li>')
        .replace(/^- (.+)$/gim, '<li>$1</li>')
        .replace(/\n\n/g, '</p><p>')
        .replace(/\n/g, '<br>');
}

function copyLLMResponse() {
    if (!currentLLMContent) {
        showSuccess('❌ Нет контента для копирования');
        return;
    }

    const copyButton = document.getElementById('copyLlmButton');
    const copyButtonText = document.getElementById('copyButtonText');
    const copyIcon = copyButton ? copyButton.querySelector('i') : null;

    // Convert HTML to plain text
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = currentLLMContent;
    const plainText = tempDiv.textContent || tempDiv.innerText || '';

    navigator.clipboard.writeText(plainText).then(() => {
        if (copyButton) copyButton.classList.add('copied');
        if (copyIcon) copyIcon.className = 'fas fa-check';
        if (copyButtonText) copyButtonText.textContent = 'Скопировано!';

        setTimeout(() => {
            if (copyButton) copyButton.classList.remove('copied');
            if (copyIcon) copyIcon.className = 'fas fa-copy';
            if (copyButtonText) copyButtonText.textContent = 'Копировать';
        }, 2000);
    }).catch(() => showSuccess('❌ Ошибка копирования'));
}

// ============================================================================
// WEBSOCKET - GEMINI VOICE
// ============================================================================

async function connectWebSocket() {
    if (!ASSISTANT_ID) {
        Config.log('⏳ Waiting for Gemini assistant configuration');
        updateStatus('connecting', 'Выберите Gemini ассистента', 'connecting');
        return;
    }
    
    if (!window.userActivated) {
        Config.log('⏳ Waiting for user activation');
        return;
    }

    try {
        const WS_URL = Config.SERVER_URL.replace(/^http/, 'ws') + '/ws/gemini-browser/' + ASSISTANT_ID;
        
        Config.log(`🔌 Connecting to Gemini WebSocket...`);
        Config.log(`   URL: ${WS_URL}`);
        
        window.isReconnecting = true;
        
        // Close existing connections
        if (window.websocket) {
            try { window.websocket.close(); } catch (e) {}
        }
        
        if (pingInterval) {
            clearInterval(pingInterval);
            pingInterval = null;
        }
        
        window.websocket = new WebSocket(WS_URL);
        window.websocket.binaryType = 'arraybuffer';
        
        window.websocket.onopen = function() {
            Config.log('✅ Gemini WebSocket connected');
            window.isConnected = true;
            window.isReconnecting = false;
            reconnectAttempts = 0;
            
            // Activate HUD
            const hudFrames = document.querySelectorAll('.hud-frame');
            hudFrames.forEach(frame => frame.classList.add('active'));
            
            const isMuted = Audio.getMuteState();
            if (isMuted) {
                updateStatus('muted', 'Микрофон отключён', 'muted');
            } else {
                updateStatus('connected', 'Готов', 'connected');
            }
            
            // Connect LLM WebSocket
            connectLLMWebSocket();
            
            // Start listening (if not muted)
            setTimeout(() => {
                if (!Audio.getMuteState()) {
                    Audio.startListening();
                }
            }, 500);
            
            // Setup ping
            const pingIntervalTime = Config.isMobile ? _MOBILEConfig.PING_INTERVAL : Config.PING_INTERVAL;
            pingInterval = setInterval(() => {
                if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
                    window.websocket.send(JSON.stringify({ type: "ping" }));
                }
            }, pingIntervalTime);
        };
        
        window.websocket.onmessage = function(event) {
            handleGeminiMessage(event);
        };
        
        window.websocket.onclose = function(event) {
            Config.log(`🔌 WebSocket closed: ${event.code}`);
            window.isConnected = false;
            Audio.stopListening();
            
            if (pingInterval) {
                clearInterval(pingInterval);
                pingInterval = null;
            }
            
            if (event.code === 1000 || event.code === 1001) return;
            
            // Reconnect logic
            const maxAttempts = Config.isMobile ? _MOBILEConfig.MAX_RECONNECT_ATTEMPTS : Config.MAX_RECONNECT_ATTEMPTS;
            if (reconnectAttempts < maxAttempts && window.userActivated) {
                reconnectAttempts++;
                const delay = Math.min(30000, Math.pow(2, reconnectAttempts) * 1000);
                Config.log(`🔄 Reconnecting in ${delay}ms (attempt ${reconnectAttempts}/${maxAttempts})`);
                updateStatus('connecting', 'Переподключение...', 'connecting');
                setTimeout(() => connectWebSocket(), delay);
            } else {
                updateStatus('error', 'Ошибка подключения', 'error');
                showError('Ошибка подключения', 'Не удалось подключиться к серверу. Обновите страницу.');
            }
        };
        
        window.websocket.onerror = function(error) {
            Config.log(`❌ WebSocket error: ${error}`, 'error');
        };
        
        return true;
        
    } catch (error) {
        Config.log(`❌ Connection error: ${error}`, 'error');
        window.isReconnecting = false;
        return false;
    }
}

function handleGeminiMessage(event) {
    try {
        if (event.data instanceof Blob || !event.data) return;

        try {
            const data = JSON.parse(event.data);
            lastPongTime = Date.now();
            
            // Agent request from voice (query_orchestrator)
            if (data.type === 'agent.request') {
                console.log('[AGENT DEBUG] Received agent.request:', data);
                console.log('[AGENT DEBUG] isAgentMode:', isAgentMode);
                console.log('[AGENT DEBUG] agentConfigId:', agentConfigId);
                console.log('[AGENT DEBUG] llmWebSocket state:', llmWebSocket?.readyState);
                Config.log(`🤖 Agent request received: ${data.task}`);
                if (isAgentMode && agentConfigId && llmWebSocket && llmWebSocket.readyState === WebSocket.OPEN) {
                    const payload = {
                        type: 'agent.query',
                        task: data.task,
                        request_id: data.request_id,
                        agent_config_id: agentConfigId
                    };
                    console.log('[AGENT DEBUG] Sending to LLM WS:', payload);
                    llmWebSocket.send(JSON.stringify(payload));
                } else {
                    // Fallback to regular LLM query
                    startStreamingUI(data.task);
                    sendLLMQuery(data.task, data.request_id);
                }
                return;
            }

            // LLM request from voice
            if (data.type === 'llm.request') {
                Config.log(`📝 LLM request received: ${data.query}`);
                startStreamingUI(data.query);
                sendLLMQuery(data.query, data.request_id);
                return;
            }
            
            // LLM streaming events
            if (data.type === 'llm.stream.start') {
                currentRequestId = data.request_id;
                startStreamingUI(data.query);
                return;
            }
            
            if (data.type === 'llm.stream.delta') {
                if (data.request_id === currentRequestId || !currentRequestId) {
                    appendStreamingText(data.content);
                }
                return;
            }
            
            if (data.type === 'llm.stream.done') {
                finishStreamingUI(data);
                return;
            }
            
            if (data.type === 'llm.stream.error') {
                showStreamingError(data.message);
                return;
            }
            
            // Connection status
            if (data.type === 'connection_status') {
                Config.log(`Gemini connection: ${data.status}`);
                if (data.status === 'connected') {
                    const isMuted = Audio.getMuteState();
                    if (isMuted) {
                        updateStatus('muted', 'Микрофон отключён', 'muted');
                    } else {
                        updateStatus('connected', 'Gemini готов', 'connected');
                    }
                }
                return;
            }
            
            if (data.type === 'gemini.setup.complete') {
                Config.log('Gemini setup complete');
                return;
            }
            
            // Function calls
            if (data.type === 'function_call.executing') {
                Config.log(`🔧 Function executing: ${data.function}`);
                if (data.function === 'query_llm') {
                    updateStatus('processing', 'Запрос к ИИ...', 'processing');
                    const voicyfySphere = document.getElementById('voicyfySphere');
                    if (voicyfySphere) voicyfySphere.classList.add('processing');
                }
                return;
            }
            
            if (data.type === 'function_call.completed') {
                Config.log(`✅ Function completed: ${data.function}`);
                return;
            }
            
            // Audio playback
            if (data.type === 'response.audio.delta') {
                if (data.delta) Audio.addAudioChunkToBuffer(data.delta);
                return;
            }
            
            if (data.type === 'assistant.speech.started') {
                Config.log('🔊 Assistant started speaking');
                return;
            }
            
            if (data.type === 'assistant.speech.ended') {
                Config.log('🔇 Assistant speech ended (server)');
                return;
            }
            
            if (data.type === 'response.done') return;
            
            // Transcriptions
            if (data.type === 'input.transcription') {
                Config.log(`👤 User: ${data.text}`);
                return;
            }
            
            if (data.type === 'output.transcription') {
                Config.log(`🤖 Gemini: ${data.text}`);
                return;
            }
            
            // Errors
            if (data.type === 'error') {
                Config.log(`Gemini API Error: ${JSON.stringify(data.error)}`, 'error');
                
                if (data.error && data.error.code === 'input_audio_buffer_commit_empty') {
                    const isPlayingAudio = Audio.getIsPlayingAudio();
                    const isMuted = Audio.getMuteState();
                    if (!isPlayingAudio && !window.isReconnecting && !window.isStreamingLLM && !isMuted) {
                        setTimeout(() => Audio.startListening(), 500);
                    }
                    return;
                }
                
                updateStatus('error', 'Ошибка', 'error');
                return;
            }
            
        } catch (parseError) {
            if (event.data === 'pong') {
                lastPongTime = Date.now();
            }
        }
    } catch (generalError) {
        Config.log(`Message processing error: ${generalError.message}`, "error");
    }
}

// ============================================================================
// WEBSOCKET - LLM TEXT
// ============================================================================

function connectLLMWebSocket() {
    if (llmWebSocket && llmWebSocket.readyState === WebSocket.OPEN) {
        Config.log('📝 LLM WebSocket already connected');
        return;
    }
    
    try {
        let LLM_WS_URL = Config.SERVER_URL.replace(/^http/, 'ws') + '/ws/llm-stream';
        if (ASSISTANT_ID) {
            LLM_WS_URL += '?assistant_id=' + encodeURIComponent(ASSISTANT_ID);
        }
        
        Config.log(`📝 Connecting to LLM WebSocket: ${LLM_WS_URL}`);
        
        llmWebSocket = new WebSocket(LLM_WS_URL);
        
        llmWebSocket.onopen = function() {
            Config.log('📝 ✅ LLM WebSocket connected');
            isLLMConnected = true;
            llmReconnectAttempts = 0;
            
            // Unlock chat input
            const chatInput = document.getElementById('chatInput');
            const chatSendBtn = document.getElementById('chatSendBtn');
            if (chatInput) {
                chatInput.disabled = false;
                chatInput.placeholder = 'Введите сообщение...';
            }
            if (chatSendBtn && chatInput && chatInput.value.trim()) {
                chatSendBtn.disabled = false;
            }
        };
        
        llmWebSocket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                console.log('[LLM-WS DEBUG] Received:', data.type, data);

                // Agent mode events
                if (data.type && data.type.startsWith('agent.')) {
                    handleAgentMessage(data);
                    return;
                }

                if (data.type === 'llm.stream.start') {
                    currentRequestId = data.request_id;
                    startStreamingUI(data.query);
                    return;
                }
                
                if (data.type === 'llm.stream.delta') {
                    if (data.request_id === currentRequestId || !currentRequestId) {
                        appendStreamingText(data.content);
                    }
                    return;
                }
                
                if (data.type === 'llm.stream.done') {
                    finishStreamingUI(data);
                    return;
                }
                
                if (data.type === 'llm.stream.error') {
                    const errMsg = typeof data.error === 'string' ? data.error : JSON.stringify(data.error);
                    showStreamingError(data.message || errMsg || 'Unknown error');
                    return;
                }
                
                if (data.type === 'error') {
                    const errText = typeof data.error === 'string' ? data.error : JSON.stringify(data.error);
                    Config.log(`📝 LLM error: ${errText || 'unknown'}`, 'error');
                    if (data.error_code === 'no_api_key') {
                        llmReconnectAttempts = 999;
                    }
                    return;
                }
                
                if (data.type === 'connection_status') {
                    Config.log(`📝 LLM connection: ${data.status}`);
                    return;
                }
                
            } catch (e) {
                Config.log(`📝 LLM WS parse error: ${e}`, 'error');
            }
        };
        
        llmWebSocket.onclose = function() {
            Config.log('📝 LLM WebSocket disconnected');
            isLLMConnected = false;
            
            // Lock chat input
            const chatInput = document.getElementById('chatInput');
            const chatSendBtn = document.getElementById('chatSendBtn');
            if (chatInput) {
                chatInput.placeholder = 'Подключение...';
            }
            if (chatSendBtn) {
                chatSendBtn.disabled = true;
            }
            
            // Reconnect with backoff (max 5 attempts)
            if (llmReconnectAttempts < 5 && window.isConnected && ASSISTANT_ID && window.userActivated) {
                llmReconnectAttempts++;
                const delay = Math.min(30000, Math.pow(2, llmReconnectAttempts) * 1000);
                Config.log(`📝 LLM WS reconnect in ${delay}ms (attempt ${llmReconnectAttempts}/5)`);
                setTimeout(() => {
                    connectLLMWebSocket();
                }, delay);
            }
        };
        
        llmWebSocket.onerror = function() {
            Config.log('📝 LLM WebSocket error', 'error');
        };
        
    } catch (error) {
        Config.log(`📝 LLM WebSocket connection error: ${error}`, 'error');
    }
}

function sendLLMQuery(query, requestId) {
    if (!llmWebSocket || llmWebSocket.readyState !== WebSocket.OPEN) {
        Config.log('📝 LLM WebSocket not connected, connecting...', 'warn');
        connectLLMWebSocket();
        setTimeout(() => sendLLMQuery(query, requestId), 500);
        return;
    }
    
    Config.log(`📝 Sending query to LLM: ${query.substring(0, 50)}...`);
    llmWebSocket.send(JSON.stringify({
        type: 'llm.query',
        query: query,
        request_id: requestId
    }));
}

// ============================================================================
// THREE.JS BACKGROUND
// ============================================================================

function createCircleTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    
    const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    gradient.addColorStop(0, 'rgba(255, 255, 255, 1)');
    gradient.addColorStop(0.5, 'rgba(255, 255, 255, 0.5)');
    gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
    
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 64, 64);
    
    const texture = new THREE.Texture(canvas);
    texture.needsUpdate = true;
    return texture;
}

function initThreeJS() {
    try {
        const canvas = document.getElementById('voicyfy-three-canvas');
        if (!canvas) return;

        threeScene = new THREE.Scene();
        threeCamera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 2000);
        threeCamera.position.z = 500;

        threeRenderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: !Config.isMobile });
        threeRenderer.setSize(window.innerWidth, window.innerHeight);
        threeRenderer.setPixelRatio(Config.isMobile ? 1 : Math.min(window.devicePixelRatio, 2));

        const particleCount = Config.isMobile ? 800 : 1500;
        const positions = new Float32Array(particleCount * 3);
        const colors = new Float32Array(particleCount * 3);

        const colorPalette = [
            new THREE.Color(0x8b5cf6),
            new THREE.Color(0x6366f1),
            new THREE.Color(0xa855f7)
        ];

        for (let i = 0; i < particleCount; i++) {
            const i3 = i * 3;
            positions[i3] = (Math.random() - 0.5) * 2000;
            positions[i3 + 1] = (Math.random() - 0.5) * 2000;
            positions[i3 + 2] = (Math.random() - 0.5) * 1500;

            const color = colorPalette[Math.floor(Math.random() * colorPalette.length)];
            colors[i3] = color.r;
            colors[i3 + 1] = color.g;
            colors[i3 + 2] = color.b;
        }

        const particleGeometry = new THREE.BufferGeometry();
        particleGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        particleGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

        const particleMaterial = new THREE.PointsMaterial({
            size: Config.isMobile ? 2 : 3,
            vertexColors: true,
            transparent: true,
            opacity: 0.6,
            sizeAttenuation: true,
            blending: THREE.AdditiveBlending,
            map: createCircleTexture()
        });

        threeParticles = new THREE.Points(particleGeometry, particleMaterial);
        threeScene.add(threeParticles);

        threeInitialized = true;
        animateThreeJS();
        
        Config.log('🎨 Three.js initialized');

    } catch (error) {
        Config.log(`Three.js error: ${error.message}`, 'error');
    }
}

function animateThreeJS() {
    if (!threeInitialized) return;
    requestAnimationFrame(animateThreeJS);

    if (threeParticles) {
        threeParticles.rotation.y += 0.0002;
        threeParticles.rotation.x += 0.0001;

        const positions = threeParticles.geometry.attributes.position.array;
        for (let i = 0; i < positions.length; i += 3) {
            positions[i + 1] += Math.sin(Date.now() * 0.0001 + i) * 0.05;
            if (positions[i + 1] > 1000) positions[i + 1] = -1000;
            if (positions[i + 1] < -1000) positions[i + 1] = 1000;
        }
        threeParticles.geometry.attributes.position.needsUpdate = true;
    }

    threeRenderer.render(threeScene, threeCamera);
}

function handleWindowResize() {
    if (!threeInitialized) return;
    threeCamera.aspect = window.innerWidth / window.innerHeight;
    threeCamera.updateProjectionMatrix();
    threeRenderer.setSize(window.innerWidth, window.innerHeight);
}

// ============================================================================
// AGENT MODE
// ============================================================================

function initAgentUI() {
    // Checkbox toggle
    const checkbox = document.getElementById('agentModeCheckbox');
    if (checkbox) {
        checkbox.addEventListener('change', function() {
            toggleAgentMode(this.checked);
        });
    }

    // Settings button
    const settingsBtn = document.getElementById('agentSettingsBtn');
    if (settingsBtn) {
        settingsBtn.addEventListener('click', openAgentSettingsModal);
    }

    // Modal buttons
    const modalClose = document.getElementById('agentModalClose');
    const modalCancel = document.getElementById('agentModalCancel');
    const modalSave = document.getElementById('agentModalSave');
    if (modalClose) modalClose.addEventListener('click', closeAgentSettingsModal);
    if (modalCancel) modalCancel.addEventListener('click', closeAgentSettingsModal);
    if (modalSave) modalSave.addEventListener('click', saveAgentSettingsFromModal);

    // Collapse toggle
    const collapseHeader = document.getElementById('agentStepsHeader');
    if (collapseHeader) {
        collapseHeader.addEventListener('click', toggleAgentStepsCollapse);
    }
}

async function initAgentMode() {
    if (!ASSISTANT_ID) return;

    // Reset state
    agentConfig = null;
    agentConfigId = null;

    try {
        const token = getAuthToken();
        if (!token) {
            Config.log('🤖 No auth token, skipping agent config load');
            return;
        }

        const res = await fetch(`/api/llm/agent-config?assistant_id=${ASSISTANT_ID}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (res.ok) {
            const configs = await res.json();
            if (configs && configs.length > 0) {
                agentConfig = configs.find(c => c.is_active) || configs[0];
                agentConfigId = agentConfig.id;
                Config.log(`🤖 Agent config loaded: ${agentConfig.name}`);
            }
        }
    } catch (e) {
        Config.log(`🤖 Agent config load error: ${e}`, 'warn');
    }

    // Restore checkbox state from localStorage
    const savedState = localStorage.getItem('agent_mode_enabled') === 'true';
    const checkbox = document.getElementById('agentModeCheckbox');
    if (checkbox) {
        // Only restore "on" if we have a config, otherwise force off
        if (savedState && agentConfig) {
            checkbox.checked = true;
            isAgentMode = true;
            const settingsBtn = document.getElementById('agentSettingsBtn');
            if (settingsBtn) settingsBtn.style.display = 'flex';
            Config.log('🤖 Agent mode restored from localStorage');
        } else {
            checkbox.checked = false;
            isAgentMode = false;
            const settingsBtn = document.getElementById('agentSettingsBtn');
            if (settingsBtn) settingsBtn.style.display = 'none';
        }
    }
}

function toggleAgentMode(enabled) {
    isAgentMode = enabled;
    localStorage.setItem('agent_mode_enabled', enabled);
    const settingsBtn = document.getElementById('agentSettingsBtn');
    if (settingsBtn) settingsBtn.style.display = enabled ? 'flex' : 'none';

    if (enabled) {
        if (!agentConfig) {
            openAgentSettingsModal();
            const checkbox = document.getElementById('agentModeCheckbox');
            if (checkbox) checkbox.checked = false;
            isAgentMode = false;
            localStorage.setItem('agent_mode_enabled', false);
            return;
        }
        Config.log('🤖 Agent mode enabled');
    } else {
        hideAgentPanel();
        Config.log('🤖 Agent mode disabled');
    }
}

function handleAgentMessage(data) {
    switch (data.type) {
        case 'agent.plan.start':
            showAgentPanel();
            updateAgentMeta('Построение плана...');
            break;

        case 'agent.plan.ready':
            currentPlan = data.steps.map(s => ({...s, status: 'pending'}));
            renderPlan(currentPlan);
            updateAgentMeta(`${data.steps.length} шагов`);
            break;

        case 'agent.step.start':
            updateStepStatus(data.step, 'running');
            break;

        case 'agent.function.call':
            appendStepDetail(data.step, `\u2192 Вызов функции: ${data.fn}`);
            break;

        case 'agent.function.result':
            appendStepDetail(data.step, `\u2190 ${(data.result || '').substring(0, 100)}`);
            break;

        case 'agent.step.done':
            updateStepStatus(data.step, 'done');
            if (data.summary) appendStepDetail(data.step, data.summary);
            break;

        case 'agent.step.error':
            updateStepStatus(data.step, 'error');
            appendStepDetail(data.step, `Error: ${data.error}`);
            break;

        case 'agent.plan.done':
            finishAgentPlan(data.final_answer);
            break;

        case 'agent.error':
            Config.log(`🤖 Agent error: ${data.error}`, 'error');
            updateAgentMeta('Ошибка');
            break;
    }
}

function showAgentPanel() {
    const container = document.getElementById('agentStepsContainer');
    if (container) container.style.display = 'block';
    const panel = document.getElementById('agentStepsPanel');
    if (panel) panel.innerHTML = '';
}

function hideAgentPanel() {
    const container = document.getElementById('agentStepsContainer');
    if (container) container.style.display = 'none';
}

function updateAgentMeta(text) {
    const meta = document.getElementById('agentStepsMeta');
    if (meta) meta.textContent = text;
}

function renderPlan(steps) {
    const panel = document.getElementById('agentStepsPanel');
    if (!panel) return;
    panel.innerHTML = steps.map(s => `
        <div class="agent-step" id="agent-step-${s.step}" data-status="pending">
            <div class="agent-step-header">
                <span class="agent-step-icon" id="agent-icon-${s.step}">\u23F3</span>
                <span class="agent-step-title">${s.title || 'Шаг ' + s.step}</span>
            </div>
            <div class="agent-step-details" id="agent-step-details-${s.step}"></div>
        </div>
    `).join('');
}

function updateStepStatus(stepNum, status) {
    const el = document.getElementById(`agent-step-${stepNum}`);
    const icon = document.getElementById(`agent-icon-${stepNum}`);
    if (!el) return;
    el.dataset.status = status;
    const icons = { pending: '\u23F3', running: '\uD83D\uDD04', done: '\u2705', error: '\u274C' };
    if (icon) {
        icon.textContent = icons[status] || '\u2022';
        if (status === 'running') icon.classList.add('spinning');
        else icon.classList.remove('spinning');
    }
}

function appendStepDetail(stepNum, text) {
    const el = document.getElementById(`agent-step-details-${stepNum}`);
    if (!el) return;
    const line = document.createElement('div');
    line.className = 'agent-step-detail-line';
    line.textContent = text;
    el.appendChild(line);
}

function finishAgentPlan(finalAnswer) {
    currentPlan.forEach((_, i) => updateStepStatus(i + 1, 'done'));
    updateAgentMeta('Готово');

    // Show final answer in llm-content
    const content = document.getElementById('llmContent');
    if (content) {
        const placeholder = document.getElementById('llmPlaceholder');
        if (placeholder) placeholder.style.display = 'none';
        content.innerHTML = (typeof formatMarkdown === 'function' ? formatMarkdown(finalAnswer) : finalAnswer);
    }

    // Show copy button
    const copyBtn = document.getElementById('copyLlmButton');
    if (copyBtn) copyBtn.style.display = 'flex';
}

function toggleAgentStepsCollapse() {
    const body = document.getElementById('agentStepsPanel');
    const btn = document.getElementById('agentStepsCollapse');
    if (!body) return;
    body.style.display = body.style.display === 'none' ? 'block' : 'none';
    if (btn) btn.classList.toggle('collapsed');
}

// ── Agent Settings Modal ──

function openAgentSettingsModal() {
    if (agentConfig) {
        const nameEl = document.getElementById('agentConfigName');
        const orchModel = document.getElementById('agentOrchestratorModel');
        const agentModel = document.getElementById('agentAgentModel');
        const orchPrompt = document.getElementById('agentOrchestratorPrompt');
        const maxSteps = document.getElementById('agentMaxSteps');
        const stepTimeout = document.getElementById('agentStepTimeout');

        if (nameEl) nameEl.value = agentConfig.name || '';
        if (orchModel) orchModel.value = agentConfig.orchestrator_model || 'gpt-4o';
        if (agentModel) agentModel.value = agentConfig.agent_model || 'gpt-4o-mini';
        if (orchPrompt) orchPrompt.value = agentConfig.orchestrator_prompt || '';
        if (maxSteps) maxSteps.value = agentConfig.max_steps || 10;
        if (stepTimeout) stepTimeout.value = agentConfig.step_timeout_sec || 60;
    }

    loadFunctionsForModal();
    const modal = document.getElementById('agentSettingsModal');
    if (modal) modal.style.display = 'flex';
}

function closeAgentSettingsModal() {
    const modal = document.getElementById('agentSettingsModal');
    if (modal) modal.style.display = 'none';
}

async function loadFunctionsForModal() {
    try {
        const res = await fetch('/api/functions/public/catalog');
        if (!res.ok) return;
        const fns = await res.json();
        const container = document.getElementById('agentFunctionsList');
        if (!container) return;
        const selected = agentConfig?.agent_functions || [];
        container.innerHTML = fns.map(fn => `
            <div class="agent-function-chip ${selected.includes(fn.name) ? 'selected' : ''}"
                 data-fn="${fn.name}">
                ${fn.display_name || fn.name}
            </div>
        `).join('');

        // Add click handlers
        container.querySelectorAll('.agent-function-chip').forEach(chip => {
            chip.addEventListener('click', function() {
                this.classList.toggle('selected');
            });
        });
    } catch (e) {
        Config.log(`🤖 Functions load error: ${e}`, 'warn');
    }
}

async function saveAgentSettingsFromModal() {
    const token = getAuthToken();
    if (!token) {
        Config.log('🤖 No auth token — cannot save agent config', 'error');
        return;
    }

    const selectedFns = [...document.querySelectorAll('.agent-function-chip.selected')]
        .map(el => el.dataset.fn);

    const payload = {
        name: document.getElementById('agentConfigName')?.value || 'Мой агент',
        assistant_id: ASSISTANT_ID || null,
        orchestrator_model: document.getElementById('agentOrchestratorModel')?.value || 'gpt-4o',
        orchestrator_prompt: document.getElementById('agentOrchestratorPrompt')?.value || '',
        agent_model: document.getElementById('agentAgentModel')?.value || 'gpt-4o-mini',
        agent_functions: selectedFns,
        max_steps: parseInt(document.getElementById('agentMaxSteps')?.value) || 10,
        step_timeout_sec: parseInt(document.getElementById('agentStepTimeout')?.value) || 60,
        is_active: true
    };

    try {
        let res;
        const headers = {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };

        if (agentConfig?.id) {
            res = await fetch(`/api/llm/agent-config/${agentConfig.id}`, {
                method: 'PUT',
                headers: headers,
                body: JSON.stringify(payload)
            });
        } else {
            res = await fetch('/api/llm/agent-config', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });
        }

        if (res.ok) {
            agentConfig = await res.json();
            agentConfigId = agentConfig.id;
            closeAgentSettingsModal();
            Config.log('🤖 Agent config saved');

            // Enable agent mode after saving
            isAgentMode = true;
            const checkbox = document.getElementById('agentModeCheckbox');
            if (checkbox) checkbox.checked = true;
            const settingsBtn = document.getElementById('agentSettingsBtn');
            if (settingsBtn) settingsBtn.style.display = 'flex';
        } else {
            Config.log('🤖 Agent config save error', 'error');
        }
    } catch (e) {
        Config.log(`🤖 Agent config save error: ${e}`, 'error');
    }
}

// ============================================================================
// EXPOSE GLOBAL FUNCTIONS (for compatibility)
// ============================================================================

window.saveConfig = saveConfig;
window.testAgent = testAgent;
window.copyHTMLCode = copyHTMLCode;
window.copyLLMResponse = copyLLMResponse;
window.clearChatHistory = clearChatHistory;
window.toggleAgentMode = toggleAgentMode;
window.openAgentSettingsModal = openAgentSettingsModal;
window.closeAgentSettingsModal = closeAgentSettingsModal;
window.saveAgentSettingsFromModal = saveAgentSettingsFromModal;
window.toggleAgentStepsCollapse = toggleAgentStepsCollapse;

})();
