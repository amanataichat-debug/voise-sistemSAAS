/**
 * ElevenLabs Conversational AI Widget
 * Версия: 1.0.0 - Production Ready
 * 
 * ✅ Прямое подключение к ElevenLabs API
 * ✅ Поддержка публичных и приватных агентов
 * ✅ 16kHz audio (ElevenLabs requirement)
 * ✅ iOS/Android/Desktop support
 * ✅ Автоматические прерывания
 */

(function() {
  'use strict';

  // Настройки виджета
  const DEBUG_MODE = true;
  const MAX_RECONNECT_ATTEMPTS = 5;
  const MOBILE_MAX_RECONNECT_ATTEMPTS = 10;
  const PING_INTERVAL = 30000; // ElevenLabs рекомендует реже пинговать
  const CONNECTION_TIMEOUT = 20000;
  
  // Глобальное хранение состояния
  let reconnectAttempts = 0;
  let pingIntervalId = null;
  let lastPongTime = Date.now();
  let isReconnecting = false;
  
  // Определяем тип устройства
  const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
  const isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
  const isAndroid = /Android/i.test(navigator.userAgent);
  
  // Глобальные флаги аудио
  window.audioInitialized = false;
  window.globalAudioContext = null;
  window.globalMicStream = null;
  window.silentAudioBuffer = null;

  // Логирование
  const widgetLog = (message, type = 'info') => {
    if (!DEBUG_MODE && type !== 'error') return;
    
    const prefix = '[ElevenLabs Widget]';
    const timestamp = new Date().toISOString().slice(11, 23);
    const formattedMessage = `${timestamp} | ${type.toUpperCase()} | ${message}`;
    
    if (type === 'error') {
      console.error(`${prefix} ERROR:`, message);
    } else if (type === 'warn') {
      console.warn(`${prefix} WARNING:`, message);
    } else {
      console.log(`${prefix}`, formattedMessage);
    }
  };

  // Получение конфигурации из script tag
  const getConfigFromScript = () => {
    const scriptTags = document.querySelectorAll('script');
    const config = {
      agentId: null,
      apiKey: null,
      requiresAuth: false,
      position: 'bottom-right'
    };
    
    for (let script of scriptTags) {
      // Agent ID
      if (script.hasAttribute('data-agent-id')) {
        config.agentId = script.getAttribute('data-agent-id');
      }
      
      // API Key (для приватных агентов)
      if (script.hasAttribute('data-api-key')) {
        config.apiKey = script.getAttribute('data-api-key');
        config.requiresAuth = true;
      }
      
      // Позиция виджета
      if (script.hasAttribute('data-position')) {
        config.position = script.getAttribute('data-position');
      }
      
      if (config.agentId) break;
    }
    
    return config;
  };

  const CONFIG = getConfigFromScript();
  
  if (!CONFIG.agentId) {
    console.error('[ElevenLabs Widget] Agent ID not found! Add data-agent-id="your_agent_id" to script tag');
    return;
  }

  widgetLog(`Configuration: Agent ID: ${CONFIG.agentId}, Auth: ${CONFIG.requiresAuth}, Position: ${CONFIG.position}`);
  widgetLog(`Device: ${isIOS ? 'iOS' : (isAndroid ? 'Android' : (isMobile ? 'Mobile' : 'Desktop'))}`);

  // Парсинг позиции виджета
  const parsePosition = (positionString) => {
    const defaultPosition = {
      horizontal: 'right',
      vertical: 'bottom',
      distance: '20px'
    };
    
    if (!positionString) return defaultPosition;
    
    const parts = positionString.toLowerCase().split('-');
    const position = { ...defaultPosition };
    
    if (parts.length === 2) {
      if (parts[0] === 'top' || parts[0] === 'bottom') {
        position.vertical = parts[0];
        position.horizontal = parts[1];
      } else {
        position.horizontal = parts[0];
        position.vertical = parts[1];
      }
    }
    
    return position;
  };

  const WIDGET_POSITION = parsePosition(CONFIG.position);

  // ============= STYLES =============
  function createStyles() {
    const styleEl = document.createElement('style');
    styleEl.id = 'elevenlabs-widget-styles';
    styleEl.textContent = `
      .elevenlabs-widget-container {
        position: fixed;
        ${WIDGET_POSITION.vertical}: ${WIDGET_POSITION.distance};
        ${WIDGET_POSITION.horizontal}: ${WIDGET_POSITION.distance};
        z-index: 2147483647;
        transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        font-family: 'Segoe UI', 'Roboto', sans-serif;
      }
      
      .elevenlabs-widget-button {
        width: 60px;
        height: 60px;
        border-radius: 50%;
        background: linear-gradient(135deg, #7c3aed, #5b21b6);
        box-shadow: 0 8px 32px rgba(124, 58, 237, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.1);
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: all 0.3s ease;
        position: relative;
        overflow: hidden;
        border: none;
        outline: none;
      }
      
      .elevenlabs-widget-button:hover {
        transform: scale(1.05);
        box-shadow: 0 10px 30px rgba(124, 58, 237, 0.4);
      }
      
      .elevenlabs-button-inner {
        position: relative;
        width: 40px;
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      
      .elevenlabs-pulse-ring {
        position: absolute;
        width: 100%;
        height: 100%;
        border-radius: 50%;
        animation: elevenlabs-pulse-ring 3s ease-out infinite;
        background: radial-gradient(rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0) 70%);
        opacity: 0;
      }
      
      @keyframes elevenlabs-pulse-ring {
        0% { transform: scale(0.5); opacity: 0; }
        25% { opacity: 0.4; }
        100% { transform: scale(1.2); opacity: 0; }
      }
      
      .elevenlabs-audio-bars-mini {
        display: flex;
        align-items: center;
        height: 26px;
        gap: 4px;
        justify-content: center;
      }
      
      .elevenlabs-audio-bar-mini {
        width: 3px;
        height: 12px;
        background-color: #ffffff;
        border-radius: 1.5px;
        animation: elevenlabs-eq-animation 1.2s ease-in-out infinite;
        opacity: 0.9;
      }
      
      .elevenlabs-audio-bar-mini:nth-child(1) { animation-delay: 0.0s; height: 7px; }
      .elevenlabs-audio-bar-mini:nth-child(2) { animation-delay: 0.3s; height: 12px; }
      .elevenlabs-audio-bar-mini:nth-child(3) { animation-delay: 0.1s; height: 18px; }
      .elevenlabs-audio-bar-mini:nth-child(4) { animation-delay: 0.5s; height: 9px; }
      
      @keyframes elevenlabs-eq-animation {
        0% { height: 5px; }
        50% { height: 18px; }
        100% { height: 5px; }
      }
      
      .elevenlabs-widget-expanded {
        position: absolute;
        ${WIDGET_POSITION.vertical}: 0;
        ${WIDGET_POSITION.horizontal}: 0;
        width: 320px;
        height: 0;
        opacity: 0;
        pointer-events: none;
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(10px);
        border-radius: 20px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
        overflow: hidden;
        transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        display: flex;
        flex-direction: column;
        z-index: 2147483646;
      }
      
      .elevenlabs-widget-container.active .elevenlabs-widget-expanded {
        height: 460px;
        opacity: 1;
        pointer-events: all;
      }
      
      .elevenlabs-widget-header {
        padding: 15px 20px;
        background: linear-gradient(135deg, #7c3aed, #5b21b6);
        color: white;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 20px 20px 0 0;
      }
      
      .elevenlabs-widget-title {
        font-weight: 600;
        font-size: 16px;
        letter-spacing: 0.3px;
      }
      
      .elevenlabs-widget-close {
        background: none;
        border: none;
        color: white;
        font-size: 18px;
        cursor: pointer;
        opacity: 0.8;
        transition: all 0.2s;
      }
      
      .elevenlabs-widget-close:hover {
        opacity: 1;
        transform: scale(1.1);
      }
      
      .elevenlabs-widget-content {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        background: #f9fafc;
        position: relative;
        padding: 20px;
        padding-bottom: 10px;
      }
      
      .elevenlabs-main-circle {
        width: 180px;
        height: 180px;
        border-radius: 50%;
        background: linear-gradient(135deg, #f3f4f6, #e5e7eb);
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);
        position: relative;
        overflow: hidden;
        transition: all 0.3s ease;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      
      .elevenlabs-main-circle::before {
        content: '';
        position: absolute;
        width: 140%;
        height: 140%;
        background: linear-gradient(45deg, rgba(255, 255, 255, 0.3), rgba(124, 58, 237, 0.2));
        animation: elevenlabs-wave 8s linear infinite;
        border-radius: 40%;
      }
      
      @keyframes elevenlabs-wave {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
      
      .elevenlabs-main-circle.listening {
        background: linear-gradient(135deg, #ddd6fe, #ede9fe);
        box-shadow: 0 0 30px rgba(124, 58, 237, 0.5);
      }
      
      .elevenlabs-main-circle.listening::before {
        animation: elevenlabs-wave 4s linear infinite;
        background: linear-gradient(45deg, rgba(255, 255, 255, 0.5), rgba(124, 58, 237, 0.3));
      }
      
      .elevenlabs-main-circle.speaking {
        background: linear-gradient(135deg, #dcfce7, #ecfdf5);
        box-shadow: 0 0 30px rgba(5, 150, 105, 0.5);
      }
      
      .elevenlabs-main-circle.speaking::before {
        animation: elevenlabs-wave 3s linear infinite;
        background: linear-gradient(45deg, rgba(255, 255, 255, 0.5), rgba(5, 150, 105, 0.3));
      }
      
      .elevenlabs-main-circle.interrupted {
        background: linear-gradient(135deg, #fef3c7, #fffbeb);
        box-shadow: 0 0 30px rgba(217, 119, 6, 0.5);
      }
      
      .elevenlabs-mic-icon {
        color: #7c3aed;
        font-size: 32px;
        z-index: 10;
        transition: color 0.3s ease;
      }
      
      .elevenlabs-main-circle.listening .elevenlabs-mic-icon {
        color: #7c3aed;
      }
      
      .elevenlabs-main-circle.speaking .elevenlabs-mic-icon {
        color: #059669;
      }
      
      .elevenlabs-main-circle.interrupted .elevenlabs-mic-icon {
        color: #d97706;
      }
      
      .elevenlabs-audio-visualization {
        position: absolute;
        width: 100%;
        max-width: 160px;
        height: 30px;
        bottom: -5px;
        opacity: 0.8;
        pointer-events: none;
      }
      
      .elevenlabs-audio-bars {
        display: flex;
        align-items: flex-end;
        height: 30px;
        gap: 2px;
        width: 100%;
        justify-content: center;
      }
      
      .elevenlabs-audio-bar {
        width: 3px;
        height: 2px;
        background-color: #7c3aed;
        border-radius: 1px;
        transition: height 0.1s ease;
      }
      
      .elevenlabs-loader-modal {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: rgba(255, 255, 255, 0.85);
        backdrop-filter: blur(5px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2147483646;
        opacity: 0;
        visibility: hidden;
        transition: all 0.3s;
        border-radius: 20px;
      }
      
      .elevenlabs-loader-modal.active {
        opacity: 1;
        visibility: visible;
      }
      
      .elevenlabs-loader {
        width: 40px;
        height: 40px;
        border: 3px solid rgba(124, 58, 237, 0.2);
        border-radius: 50%;
        border-top-color: #7c3aed;
        animation: elevenlabs-spin 1s linear infinite;
      }
      
      @keyframes elevenlabs-spin {
        to { transform: rotate(360deg); }
      }
      
      .elevenlabs-message-display {
        position: absolute;
        width: 90%;
        bottom: 70px;
        left: 50%;
        transform: translateX(-50%);
        background: white;
        padding: 12px 15px;
        border-radius: 12px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.08);
        text-align: center;
        font-size: 14px;
        line-height: 1.4;
        opacity: 0;
        transition: all 0.3s;
        max-height: 100px;
        overflow-y: auto;
        z-index: 10;
      }
      
      .elevenlabs-message-display.show {
        opacity: 1;
      }
      
      .elevenlabs-connection-error {
        color: #ef4444;
        background-color: rgba(254, 226, 226, 0.8);
        border: 1px solid #ef4444;
        padding: 8px 12px;
        border-radius: 8px;
        font-size: 13px;
        font-weight: 500;
        margin-top: 10px;
        text-align: center;
        display: none;
      }
      
      .elevenlabs-connection-error.visible {
        display: block;
      }
      
      .elevenlabs-retry-button {
        background-color: #ef4444;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 5px 10px;
        font-size: 12px;
        cursor: pointer;
        margin-top: 8px;
        transition: all 0.2s;
      }
      
      .elevenlabs-retry-button:hover {
        background-color: #dc2626;
      }
      
      .elevenlabs-status-indicator {
        position: absolute;
        bottom: 50px;
        left: 50%;
        transform: translateX(-50%);
        font-size: 11px;
        color: #475569;
        padding: 4px 8px;
        border-radius: 10px;
        background-color: rgba(255, 255, 255, 0.8);
        display: flex;
        align-items: center;
        gap: 5px;
        opacity: 0;
        transition: opacity 0.3s;
      }
      
      .elevenlabs-status-indicator.show {
        opacity: 0.8;
      }
      
      .elevenlabs-status-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background-color: #10b981;
      }
      
      .elevenlabs-status-dot.disconnected {
        background-color: #ef4444;
      }
      
      .elevenlabs-status-dot.connecting {
        background-color: #f59e0b;
      }
      
      .elevenlabs-voicyfy-container {
        position: absolute;
        bottom: 10px;
        left: 50%;
        transform: translateX(-50%);
        text-align: center;
        padding: 8px;
        opacity: 0.8;
        transition: opacity 0.2s ease;
      }
      
      .elevenlabs-voicyfy-link {
        display: inline-block;
        text-decoration: none;
        transition: transform 0.2s ease;
      }
      
      .elevenlabs-voicyfy-link:hover {
        transform: translateY(-2px);
      }
      
      .elevenlabs-voicyfy-link img {
        height: 25px;
        width: auto;
        display: block;
      }
      
      .elevenlabs-pulse-animation {
        animation: elevenlabs-button-pulse 2s infinite;
      }
      
      @keyframes elevenlabs-button-pulse {
        0% { box-shadow: 0 0 0 0 rgba(124, 58, 237, 0.7); }
        70% { box-shadow: 0 0 0 10px rgba(124, 58, 237, 0); }
        100% { box-shadow: 0 0 0 0 rgba(124, 58, 237, 0); }
      }
    `;
    document.head.appendChild(styleEl);
    widgetLog("Styles created");
  }

  // Загрузка Font Awesome
  function loadFontAwesome() {
    if (!document.getElementById('font-awesome-css')) {
      const link = document.createElement('link');
      link.id = 'font-awesome-css';
      link.rel = 'stylesheet';
      link.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css';
      document.head.appendChild(link);
    }
  }

  // Создание HTML структуры
  function createWidgetHTML() {
    const widgetContainer = document.createElement('div');
    widgetContainer.className = 'elevenlabs-widget-container';
    widgetContainer.id = 'elevenlabs-widget-container';

    widgetContainer.innerHTML = `
      <div class="elevenlabs-widget-button" id="elevenlabs-widget-button">
        <div class="elevenlabs-button-inner">
          <div class="elevenlabs-pulse-ring"></div>
          <div class="elevenlabs-audio-bars-mini">
            <div class="elevenlabs-audio-bar-mini"></div>
            <div class="elevenlabs-audio-bar-mini"></div>
            <div class="elevenlabs-audio-bar-mini"></div>
            <div class="elevenlabs-audio-bar-mini"></div>
          </div>
        </div>
      </div>
      
      <div class="elevenlabs-widget-expanded" id="elevenlabs-widget-expanded">
        <div class="elevenlabs-widget-header">
          <div class="elevenlabs-widget-title">AI Ассистент</div>
          <button class="elevenlabs-widget-close" id="elevenlabs-widget-close">
            <i class="fas fa-times"></i>
          </button>
        </div>
        <div class="elevenlabs-widget-content">
          <div class="elevenlabs-main-circle" id="elevenlabs-main-circle">
            <i class="fas fa-microphone elevenlabs-mic-icon"></i>
            <div class="elevenlabs-audio-visualization" id="elevenlabs-audio-visualization">
              <div class="elevenlabs-audio-bars" id="elevenlabs-audio-bars"></div>
            </div>
          </div>
          
          <div class="elevenlabs-message-display" id="elevenlabs-message-display"></div>
          
          <div class="elevenlabs-connection-error" id="elevenlabs-connection-error">
            Ошибка соединения
            <button class="elevenlabs-retry-button" id="elevenlabs-retry-button">
              Повторить
            </button>
          </div>
          
          <div class="elevenlabs-status-indicator" id="elevenlabs-status-indicator">
            <div class="elevenlabs-status-dot" id="elevenlabs-status-dot"></div>
            <span id="elevenlabs-status-text">Подключено</span>
          </div>
          
          <div class="elevenlabs-voicyfy-container">
            <a href="https://voicyfy.ru/" target="_blank" rel="noopener noreferrer" class="elevenlabs-voicyfy-link">
              <img src="https://i.ibb.co/ccw6sjdk/photo-2025-06-03-05-04-02.jpg" alt="Powered by Voicyfy">
            </a>
          </div>
        </div>
      </div>
      
      <div id="elevenlabs-loader-modal" class="elevenlabs-loader-modal active">
        <div class="elevenlabs-loader"></div>
      </div>
    `;

    document.body.appendChild(widgetContainer);
    widgetLog("HTML structure created");
  }

  // ============= AUDIO FUNCTIONS =============
  
  // Инициализация аудио (16kHz для ElevenLabs)
  async function initializeAudio() {
    widgetLog(`Audio initialization for ${isIOS ? 'iOS' : (isAndroid ? 'Android' : 'Desktop')}`);
    
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("Browser doesn't support microphone access");
      }

      // Создаем AudioContext с 16kHz (ElevenLabs requirement)
      if (!window.globalAudioContext) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        window.globalAudioContext = new AudioContextClass({
          sampleRate: 16000, // 16kHz для ElevenLabs!
          latencyHint: 'interactive'
        });
        widgetLog(`AudioContext created: ${window.globalAudioContext.sampleRate}Hz`);
      }

      if (window.globalAudioContext.state === 'suspended') {
        await window.globalAudioContext.resume();
        widgetLog('AudioContext resumed');
      }

      // Получаем микрофон
      if (!window.globalMicStream) {
        window.globalMicStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            sampleRate: 16000,
            channelCount: 1
          }
        });
        widgetLog('Microphone activated');
      }

      // Для iOS - воспроизводим тишину для разблокировки
      if (isIOS && !window.silentAudioBuffer) {
        window.silentAudioBuffer = window.globalAudioContext.createBuffer(1, 1, window.globalAudioContext.sampleRate);
        const silentSource = window.globalAudioContext.createBufferSource();
        silentSource.buffer = window.silentAudioBuffer;
        silentSource.connect(window.globalAudioContext.destination);
        silentSource.start(0);
        widgetLog('iOS: Silent buffer played');
      }

      window.audioInitialized = true;
      widgetLog('Audio initialization complete');
      return true;

    } catch (error) {
      widgetLog(`Audio initialization failed: ${error.message}`, 'error');
      return false;
    }
  }

  // Ресемплинг аудио в 16kHz если нужно
  function resampleTo16kHz(audioBuffer) {
    const sampleRate = audioBuffer.sampleRate;
    
    // Если уже 16kHz - возвращаем как есть
    if (sampleRate === 16000) {
      return audioBuffer.getChannelData(0);
    }
    
    const channelData = audioBuffer.getChannelData(0);
    const ratio = sampleRate / 16000;
    const newLength = Math.round(channelData.length / ratio);
    const result = new Float32Array(newLength);
    
    let offsetResult = 0;
    let offsetBuffer = 0;
    
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      let accum = 0;
      let count = 0;
      
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < channelData.length; i++) {
        accum += channelData[i];
        count++;
      }
      
      result[offsetResult] = accum / count;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    
    return result;
  }

  // Преобразование ArrayBuffer в Base64
  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }
  
  // Преобразование Base64 в ArrayBuffer
  function base64ToArrayBuffer(base64) {
    try {
      const binaryString = atob(base64);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      return bytes.buffer;
    } catch (e) {
      widgetLog(`Base64 decode error: ${e.message}`, "error");
      return new ArrayBuffer(0);
    }
  }

  // Создание WAV из PCM
  function createWavFromPcm(pcmBuffer, sampleRate = 16000) {
    const wavHeader = new ArrayBuffer(44);
    const view = new DataView(wavHeader);
    
    // "RIFF"
    view.setUint32(0, 0x52494646, false);
    view.setUint32(4, 36 + pcmBuffer.byteLength, true);
    
    // "WAVE"
    view.setUint32(8, 0x57415645, false);
    
    // "fmt "
    view.setUint32(12, 0x666d7420, false);
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    
    // "data"
    view.setUint32(36, 0x64617461, false);
    view.setUint32(40, pcmBuffer.byteLength, true);
    
    const wavBuffer = new ArrayBuffer(wavHeader.byteLength + pcmBuffer.byteLength);
    const wavBytes = new Uint8Array(wavBuffer);
    
    wavBytes.set(new Uint8Array(wavHeader), 0);
    wavBytes.set(new Uint8Array(pcmBuffer), wavHeader.byteLength);
    
    return wavBuffer;
  }

  // ============= MAIN WIDGET LOGIC =============
  
  function initWidget() {
    const widgetContainer = document.getElementById('elevenlabs-widget-container');
    const widgetButton = document.getElementById('elevenlabs-widget-button');
    const widgetClose = document.getElementById('elevenlabs-widget-close');
    const mainCircle = document.getElementById('elevenlabs-main-circle');
    const audioBars = document.getElementById('elevenlabs-audio-bars');
    const loaderModal = document.getElementById('elevenlabs-loader-modal');
    const messageDisplay = document.getElementById('elevenlabs-message-display');
    const connectionError = document.getElementById('elevenlabs-connection-error');
    const retryButton = document.getElementById('elevenlabs-retry-button');
    const statusIndicator = document.getElementById('elevenlabs-status-indicator');
    const statusDot = document.getElementById('elevenlabs-status-dot');
    const statusText = document.getElementById('elevenlabs-status-text');
    
    if (!widgetButton || !mainCircle) {
      widgetLog("Required UI elements not found!", 'error');
      return;
    }
    
    // Переменные состояния
    let websocket = null;
    let audioProcessor = null;
    let isConnected = false;
    let isListening = false;
    let isPlayingAudio = false;
    let isWidgetOpen = false;
    let connectionFailedPermanently = false;
    let audioPlaybackQueue = [];
    let lastInterruptionId = 0;
    let conversationId = null;
    
    // Создаем аудио-бары
    function createAudioBars(count = 20) {
      audioBars.innerHTML = '';
      for (let i = 0; i < count; i++) {
        const bar = document.createElement('div');
        bar.className = 'elevenlabs-audio-bar';
        audioBars.appendChild(bar);
      }
    }
    createAudioBars();

    // Показать сообщение
    function showMessage(message, duration = 5000) {
      messageDisplay.textContent = message;
      messageDisplay.classList.add('show');
      
      if (duration > 0) {
        setTimeout(() => {
          messageDisplay.classList.remove('show');
        }, duration);
      }
    }

    // Скрыть сообщение
    function hideMessage() {
      messageDisplay.classList.remove('show');
    }
    
    // Обновление статуса
    function updateConnectionStatus(status, message) {
      if (!statusIndicator || !statusDot || !statusText) return;
      
      statusText.textContent = message || status;
      statusDot.className = 'elevenlabs-status-dot';
      
      if (status === 'connected') {
        statusDot.classList.add('connected');
      } else if (status === 'disconnected') {
        statusDot.classList.add('disconnected');
      } else {
        statusDot.classList.add('connecting');
      }
      
      statusIndicator.classList.add('show');
      setTimeout(() => statusIndicator.classList.remove('show'), 3000);
    }
    
    // Показать ошибку соединения
    function showConnectionError(message) {
      if (connectionError) {
        connectionError.innerHTML = `
          ${message || 'Ошибка соединения'}
          <button class="elevenlabs-retry-button" id="elevenlabs-retry-button-new">
            Повторить
          </button>
        `;
        connectionError.classList.add('visible');
        
        const newRetryButton = document.getElementById('elevenlabs-retry-button-new');
        if (newRetryButton) {
          newRetryButton.addEventListener('click', resetConnection);
        }
      }
    }
    
    // Скрыть ошибку
    function hideConnectionError() {
      if (connectionError) {
        connectionError.classList.remove('visible');
      }
    }
    
    // Сброс соединения
    function resetConnection() {
      reconnectAttempts = 0;
      connectionFailedPermanently = false;
      hideConnectionError();
      showMessage("Подключение...");
      connectWebSocket();
    }

    // ============= AUDIO PLAYBACK =============
    
    function playNextAudio() {
      if (audioPlaybackQueue.length === 0) {
        isPlayingAudio = false;
        mainCircle.classList.remove('speaking');
        
        // Автоматически возобновляем прослушивание
        if (isWidgetOpen && isConnected) {
          setTimeout(() => startListening(), 400);
        }
        return;
      }
      
      isPlayingAudio = true;
      mainCircle.classList.add('speaking');
      mainCircle.classList.remove('listening');
      
      const audioBase64 = audioPlaybackQueue.shift();
      
      try {
        const audioData = base64ToArrayBuffer(audioBase64);
        if (audioData.byteLength === 0) {
          playNextAudio();
          return;
        }
        
        const wavBuffer = createWavFromPcm(audioData, 16000);
        const blob = new Blob([wavBuffer], { type: 'audio/wav' });
        const audioUrl = URL.createObjectURL(blob);
        
        const audio = new Audio();
        audio.playsInline = true;
        audio.volume = 1.0;
        audio.src = audioUrl;
        
        audio.onended = () => {
          URL.revokeObjectURL(audioUrl);
          playNextAudio();
        };
        
        audio.onerror = (e) => {
          widgetLog(`Audio playback error: ${e}`, 'error');
          URL.revokeObjectURL(audioUrl);
          playNextAudio();
        };
        
        audio.play().catch(error => {
          widgetLog(`Audio play failed: ${error.message}`, 'error');
          URL.revokeObjectURL(audioUrl);
          playNextAudio();
        });
        
      } catch (error) {
        widgetLog(`Audio creation error: ${error.message}`, "error");
        playNextAudio();
      }
    }
    
    function addAudioToPlaybackQueue(audioBase64) {
      if (!audioBase64) return;
      audioPlaybackQueue.push(audioBase64);
      if (!isPlayingAudio) {
        playNextAudio();
      }
    }
    
    function stopAllAudioPlayback() {
      widgetLog('Stopping all audio playback');
      isPlayingAudio = false;
      audioPlaybackQueue = [];
      mainCircle.classList.remove('speaking');
    }

    // ============= AUDIO RECORDING =============
    
    function updateAudioVisualization(audioData) {
      const bars = audioBars.querySelectorAll('.elevenlabs-audio-bar');
      const step = Math.floor(audioData.length / bars.length);
      
      for (let i = 0; i < bars.length; i++) {
        let sum = 0;
        for (let j = 0; j < step; j++) {
          const index = i * step + j;
          if (index < audioData.length) {
            sum += Math.abs(audioData[index]);
          }
        }
        const average = sum / step;
        const height = 2 + Math.min(28, Math.floor(average * (isMobile ? 200 : 100)));
        bars[i].style.height = `${height}px`;
      }
    }
    
    function resetAudioVisualization() {
      const bars = audioBars.querySelectorAll('.elevenlabs-audio-bar');
      bars.forEach(bar => bar.style.height = '2px');
    }

    async function startListening() {
      if (!isConnected || isPlayingAudio || isListening) {
        widgetLog(`Cannot start listening: connected=${isConnected}, playing=${isPlayingAudio}, listening=${isListening}`);
        return;
      }
      
      if (!window.audioInitialized) {
        const success = await initializeAudio();
        if (!success) {
          showMessage("Ошибка доступа к микрофону");
          return;
        }
      }
      
      isListening = true;
      widgetLog('Starting to listen');
      
      if (window.globalAudioContext.state === 'suspended') {
        await window.globalAudioContext.resume();
      }
      
      if (!audioProcessor) {
        audioProcessor = window.globalAudioContext.createScriptProcessor(2048, 1, 1);
        
        audioProcessor.onaudioprocess = function(e) {
          if (isListening && websocket && websocket.readyState === WebSocket.OPEN) {
            const inputBuffer = e.inputBuffer;
            let inputData = inputBuffer.getChannelData(0);
            
            if (inputData.length === 0) return;
            
            // Ресемплинг если нужно (хотя AudioContext уже в 16kHz)
            if (inputBuffer.sampleRate !== 16000) {
              inputData = resampleTo16kHz(inputBuffer);
            }
            
            updateAudioVisualization(inputData);
            
            // Конвертируем в PCM16
            const pcm16Data = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
              pcm16Data[i] = Math.max(-32768, Math.min(32767, Math.floor(inputData[i] * 32767)));
            }
            
            // Отправляем через WebSocket
            try {
              websocket.send(JSON.stringify({
                user_audio_chunk: arrayBufferToBase64(pcm16Data.buffer)
              }));
            } catch (error) {
              widgetLog(`Error sending audio: ${error.message}`, "error");
            }
          }
        };
        
        const streamSource = window.globalAudioContext.createMediaStreamSource(window.globalMicStream);
        streamSource.connect(audioProcessor);
        
        const gainNode = window.globalAudioContext.createGain();
        gainNode.gain.value = 0;
        audioProcessor.connect(gainNode);
        gainNode.connect(window.globalAudioContext.destination);
      }
      
      mainCircle.classList.add('listening');
      widgetLog("Listening started");
    }
    
    function stopListening() {
      isListening = false;
      mainCircle.classList.remove('listening');
      resetAudioVisualization();
      widgetLog("Listening stopped");
    }

    // ============= WEBSOCKET CONNECTION =============
    
    async function getSignedUrl() {
      if (!CONFIG.requiresAuth) {
        // Публичный агент - прямой URL
        return `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=${CONFIG.agentId}`;
      }
      
      // Приватный агент - запрашиваем signed URL
      try {
        const response = await fetch(
          `https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=${CONFIG.agentId}`,
          {
            headers: {
              'xi-api-key': CONFIG.apiKey
            }
          }
        );
        
        if (!response.ok) {
          throw new Error(`Failed to get signed URL: ${response.status}`);
        }
        
        const data = await response.json();
        widgetLog('Signed URL obtained');
        return data.signed_url;
      } catch (error) {
        widgetLog(`Error getting signed URL: ${error.message}`, 'error');
        throw error;
      }
    }

    async function connectWebSocket() {
      try {
        loaderModal.classList.add('active');
        widgetLog("Connecting to ElevenLabs...");
        
        hideConnectionError();
        
        const wsUrl = await getSignedUrl();
        widgetLog(`WebSocket URL: ${wsUrl.substring(0, 50)}...`);
        
        if (websocket) {
          try { websocket.close(); } catch (e) {}
        }
        
        websocket = new WebSocket(wsUrl);
        websocket.binaryType = 'arraybuffer';
        
        const connectionTimeout = setTimeout(() => {
          widgetLog("Connection timeout", "error");
          if (websocket) websocket.close();
          
          reconnectAttempts++;
          const maxAttempts = isMobile ? MOBILE_MAX_RECONNECT_ATTEMPTS : MAX_RECONNECT_ATTEMPTS;
          
          if (reconnectAttempts >= maxAttempts) {
            connectionFailedPermanently = true;
            if (isWidgetOpen) {
              showConnectionError("Не удалось подключиться к серверу");
              updateConnectionStatus('disconnected', 'Отключено');
            }
          } else {
            setTimeout(connectWebSocket, 2000);
          }
        }, CONNECTION_TIMEOUT);
        
        websocket.onopen = function() {
          clearTimeout(connectionTimeout);
          widgetLog('✅ WebSocket connected to ElevenLabs');
          isConnected = true;
          reconnectAttempts = 0;
          connectionFailedPermanently = false;
          loaderModal.classList.remove('active');
          
          // Отправляем инициализацию
          websocket.send(JSON.stringify({
            type: "conversation_initiation_client_data",
            conversation_config_override: {},
            custom_llm_extra_body: {},
            dynamic_variables: {}
          }));
          
          hideConnectionError();
          
          if (isWidgetOpen) {
            updateConnectionStatus('connected', 'Подключено');
            startListening();
          }
        };
        
        websocket.onmessage = function(event) {
          try {
            const data = JSON.parse(event.data);
            
            // Логируем только важные события
            if (data.type !== 'ping' && data.type !== 'pong') {
              widgetLog(`Event: ${data.type}`);
            }
            
            // Conversation ID
            if (data.type === 'conversation_initiation_metadata') {
              conversationId = data.conversation_initiation_metadata_event?.conversation_id;
              widgetLog(`Conversation ID: ${conversationId}`);
            }
            
            // Аудио от ассистента
            if (data.type === 'audio') {
              const audioEvent = data.audio_event;
              if (audioEvent && parseInt(audioEvent.event_id) > lastInterruptionId) {
                addAudioToPlaybackQueue(audioEvent.audio_base_64);
              }
            }
            
            // Текстовый ответ ассистента
            if (data.type === 'agent_response') {
              const response = data.agent_response_event?.agent_response;
              if (response) {
                showMessage(response.trim(), 5000);
              }
            }
            
            // Транскрипция пользователя
            if (data.type === 'user_transcript') {
              const transcript = data.user_transcription_event?.user_transcript;
              if (transcript) {
                widgetLog(`User said: ${transcript}`);
              }
            }
            
            // Прерывание
            if (data.type === 'interruption') {
              const interruptionEvent = data.interruption_event;
              lastInterruptionId = parseInt(interruptionEvent.event_id);
              widgetLog('Interruption detected');
              
              stopAllAudioPlayback();
              mainCircle.classList.add('interrupted');
              
              setTimeout(() => {
                mainCircle.classList.remove('interrupted');
                if (isWidgetOpen && !isPlayingAudio) {
                  mainCircle.classList.add('listening');
                }
              }, 1000);
            }
            
            // Ping-pong для keep-alive
            if (data.type === 'ping') {
              websocket.send(JSON.stringify({
                type: "pong",
                event_id: data.ping_event?.event_id
              }));
            }
            
          } catch (parseError) {
            widgetLog(`Message parse error: ${parseError.message}`, 'warn');
          }
        };
        
        websocket.onclose = function(event) {
          widgetLog(`WebSocket closed: ${event.code}`);
          isConnected = false;
          isListening = false;
          
          if (event.code === 1000 || event.code === 1001) {
            return; // Чистое закрытие
          }
          
          // Переподключение
          reconnectAttempts++;
          const maxAttempts = isMobile ? MOBILE_MAX_RECONNECT_ATTEMPTS : MAX_RECONNECT_ATTEMPTS;
          
          if (reconnectAttempts < maxAttempts) {
            const delay = Math.min(30000, Math.pow(2, reconnectAttempts) * 1000);
            widgetLog(`Reconnecting in ${delay/1000}s (${reconnectAttempts}/${maxAttempts})`);
            
            setTimeout(() => {
              if (!isConnected) {
                connectWebSocket();
              }
            }, delay);
          } else {
            connectionFailedPermanently = true;
            if (isWidgetOpen) {
              showConnectionError("Не удалось восстановить соединение");
              updateConnectionStatus('disconnected', 'Отключено');
            }
          }
        };
        
        websocket.onerror = function(error) {
          widgetLog(`WebSocket error: ${error}`, 'error');
        };
        
      } catch (error) {
        widgetLog(`Connection error: ${error.message}`, 'error');
        loaderModal.classList.remove('active');
        
        reconnectAttempts++;
        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
          setTimeout(connectWebSocket, 2000);
        } else {
          connectionFailedPermanently = true;
          if (isWidgetOpen) {
            showConnectionError("Не удалось подключиться");
          }
        }
      }
    }

    // ============= UI HANDLERS =============
    
    async function openWidget() {
      widgetLog("Opening widget");
      
      widgetContainer.classList.add('active');
      isWidgetOpen = true;
      
      if (!window.audioInitialized) {
        const success = await initializeAudio();
        if (!success) {
          showMessage("Ошибка доступа к микрофону", 5000);
          return;
        }
      }
      
      if (connectionFailedPermanently) {
        showConnectionError('Не удалось подключиться. Нажмите "Повторить".');
        return;
      }
      
      if (isConnected && !isListening && !isPlayingAudio) {
        startListening();
        updateConnectionStatus('connected', 'Подключено');
      } else if (!isConnected) {
        connectWebSocket();
      }
      
      widgetButton.classList.remove('elevenlabs-pulse-animation');
    }
    
    function closeWidget() {
      widgetLog("Closing widget");
      
      stopListening();
      stopAllAudioPlayback();
      
      widgetContainer.classList.remove('active');
      isWidgetOpen = false;
      
      hideMessage();
      hideConnectionError();
      
      if (statusIndicator) {
        statusIndicator.classList.remove('show');
      }
    }

    // Event listeners
    widgetButton.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      openWidget();
    });

    widgetClose.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeWidget();
    });
    
    mainCircle.addEventListener('click', () => {
      if (isWidgetOpen && !isListening && !isPlayingAudio) {
        if (isConnected) {
          startListening();
        } else if (connectionFailedPermanently) {
          showConnectionError("Соединение отсутствует. Нажмите 'Повторить'.");
        } else {
          connectWebSocket();
        }
      }
    });
    
    if (retryButton) {
      retryButton.addEventListener('click', resetConnection);
    }
    
    // Автоматическое подключение при загрузке
    connectWebSocket();
    
    // Проверка состояния через 2 секунды
    setTimeout(() => {
      widgetLog('=== Widget Status Check ===');
      widgetLog(`Connected: ${isConnected}`);
      widgetLog(`Listening: ${isListening}`);
      widgetLog(`Widget Open: ${isWidgetOpen}`);
      widgetLog(`Audio Initialized: ${window.audioInitialized}`);
      if (conversationId) {
        widgetLog(`Conversation ID: ${conversationId}`);
      }
    }, 2000);
  }

  // ============= INITIALIZATION =============
  
  function initializeWidget() {
    widgetLog('=== ElevenLabs Widget Initialization ===');
    widgetLog(`Device: ${isIOS ? 'iOS' : (isAndroid ? 'Android' : (isMobile ? 'Mobile' : 'Desktop'))}`);
    widgetLog(`Agent ID: ${CONFIG.agentId}`);
    widgetLog(`Requires Auth: ${CONFIG.requiresAuth}`);
    
    loadFontAwesome();
    createStyles();
    createWidgetHTML();
    initWidget();
    
    widgetLog('✅ Widget initialization complete');
  }
  
  // Запуск
  if (!document.getElementById('elevenlabs-widget-container')) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initializeWidget);
    } else {
      initializeWidget();
    }
  } else {
    widgetLog('Widget already exists, skipping initialization');
  }
})();
