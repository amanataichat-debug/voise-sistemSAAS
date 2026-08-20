// ============= ✅ ENHANCED ELEVENLABS CONVERSATION MANAGER =============

class EnhancedElevenLabsConversationManager {
  constructor() {
    // WebSocket соединение
    this.websocket = null;
    this.mediaStream = null;
    this.audioContext = null;
    this.audioProcessor = null;
    this.isActive = false;
    this.conversationId = null;
    
    // ✅ Правильная аудио очередь с jitter buffer
    this.audioQueue = [];
    this.jitterBuffer = [];
    this.isPlayingAudio = false;
    this.currentAudioElement = null;
    this.expectedSequence = 0;
    
    // ✅ ЛОКАЛЬНЫЙ VAD для отображения (когда сервер молчит)
    this.localVADProcessor = null;
    this.localVADThreshold = 0.01; // Низкий порог для визуализации
    this.localVADHistory = [];
    this.localVADValue = 0;
    
    // ✅ Серверный VAD
    this.serverVADValue = 0;
    this.serverVADReceived = false;
    
    // ✅ ДИАГНОСТИКА
    this.diagnosticIssues = [];
    this.diagnosticCheckInterval = null;
    
    // ✅ АДАПТИВНАЯ буферизация
    this.bufferSize = 2048;
    this.minBufferSize = 1024;
    this.maxBufferSize = 8192;
    this.averageRTT = 0;
    this.rttHistory = [];
    
    this.inputVolumeLevel = 0;
    
    // ✅ УЛУЧШЕННЫЕ метрики
    this.metrics = {
      connectionStartTime: null,
      connectionTime: 0,
      audioChunks: 0,
      interruptions: 0,
      rtt: 0,
      sessionDuration: 0,
      bufferHealth: 100,
      vadEvents: 0,
      transcriptEvents: 0
    };
    
    // Ping-Pong monitoring
    this.lastPingTime = null;
    this.keepAliveInterval = null;
    
    console.log('🎯 Enhanced ElevenLabs Conversation Manager с диагностикой инициализирован');
  }
  
  // ============= ДИАГНОСТИЧЕСКИЕ МЕТОДЫ =============
  
  addDiagnosticIssue(type, message) {
    const issue = {
      type: type, // 'error', 'warning', 'success'
      message: message,
      timestamp: Date.now()
    };
    
    this.diagnosticIssues.push(issue);
    this.updateDiagnosticDisplay();
    
    // Лог в консоль
    const logMethod = type === 'error' ? 'error' : type === 'warning' ? 'warn' : 'log';
    console[logMethod](`🔍 ДИАГНОСТИКА [${type.toUpperCase()}]: ${message}`);
  }
  
  clearDiagnosticIssues() {
    this.diagnosticIssues = [];
    this.updateDiagnosticDisplay();
  }
  
  updateDiagnosticDisplay() {
    const diagnosticPanel = document.getElementById('diagnostic-panel');
    const diagnosticItems = document.getElementById('diagnostic-items');
    
    if (!diagnosticPanel || !diagnosticItems) return;
    
    if (this.diagnosticIssues.length === 0) {
      diagnosticPanel.style.display = 'none';
      return;
    }
    
    diagnosticPanel.style.display = 'block';
    diagnosticItems.innerHTML = '';
    
    this.diagnosticIssues.forEach(issue => {
      const issueEl = document.createElement('div');
      issueEl.className = `diagnostic-item ${issue.type}`;
      issueEl.innerHTML = `
        <span>${new Date(issue.timestamp).toLocaleTimeString()}</span> - 
        <span>${issue.message}</span>
      `;
      diagnosticItems.appendChild(issueEl);
    });
  }
  
  startDiagnosticMonitoring() {
    // Проверяем проблемы каждые 5 секунд
    this.diagnosticCheckInterval = setInterval(() => {
      this.performDiagnosticChecks();
    }, 5000);
  }
  
  stopDiagnosticMonitoring() {
    if (this.diagnosticCheckInterval) {
      clearInterval(this.diagnosticCheckInterval);
      this.diagnosticCheckInterval = null;
    }
  }
  
  performDiagnosticChecks() {
    if (!this.isActive) return;
    
    // Проверка 1: VAD события не приходят от сервера
    if (this.metrics.sessionDuration > 10 && this.metrics.vadEvents === 0) {
      if (!this.serverVADReceived) {
        this.addDiagnosticIssue('warning', 'VAD события не приходят от сервера. Проверьте настройки агента в ElevenLabs.');
        this.serverVADReceived = true; // Чтобы не спамить
      }
    }
    
    // Проверка 2: Высокий RTT
    if (this.averageRTT > 500) {
      this.addDiagnosticIssue('warning', `Высокий RTT: ${Math.round(this.averageRTT)}ms. Проблемы с сетью могут влиять на качество.`);
    }
    
    // Проверка 3: Нет транскрипций речи пользователя
    if (this.metrics.sessionDuration > 15 && this.metrics.transcriptEvents === 0) {
      this.addDiagnosticIssue('error', 'Речь пользователя не распознается. Проверьте микрофон и язык агента.');
    }
    
    // Проверка 4: Слишком много ping событий подряд
    if (this.averageRTT > 1000) {
      this.addDiagnosticIssue('error', 'Критически высокий RTT. Соединение нестабильно.');
    }
  }
  
  // ============= ЛОКАЛЬНЫЙ VAD ДЛЯ ВИЗУАЛИЗАЦИИ =============
  
  setupLocalVAD() {
    if (!this.audioContext || !this.mediaStream) return;
    
    try {
      const source = this.audioContext.createMediaStreamSource(this.mediaStream);
      
      // ✅ Создаем отдельный анализатор для локального VAD
      const analyser = this.audioContext.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.3;
      
      source.connect(analyser);
      
      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      
      // Функция для вычисления VAD на основе энергии сигнала
      const updateLocalVAD = () => {
        if (!this.isActive) return;
        
        analyser.getByteFrequencyData(dataArray);
        
        // Вычисляем RMS энергию
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          const value = dataArray[i] / 255.0;
          sum += value * value;
        }
        const rms = Math.sqrt(sum / bufferLength);
        
        // Сглаживаем с историей
        this.localVADHistory.push(rms);
        if (this.localVADHistory.length > 10) {
          this.localVADHistory.shift();
        }
        
        // Средний локальный VAD
        const avgLocalVAD = this.localVADHistory.reduce((a, b) => a + b, 0) / this.localVADHistory.length;
        this.localVADValue = Math.min(avgLocalVAD * 5, 1.0); // Усиливаем для визуализации
        
        // Обновляем отображение
        this.updateVADDisplay();
        
        // Продолжаем анализ
        requestAnimationFrame(updateLocalVAD);
      };
      
      updateLocalVAD();
      this.log('✅ Локальный VAD для визуализации настроен', 'success');
      
    } catch (error) {
      this.log(`❌ Ошибка настройки локального VAD: ${error.message}`, 'error');
      this.addDiagnosticIssue('warning', 'Локальный VAD не удалось настроить');
    }
  }
  
  // ============= ПРАВИЛЬНАЯ ИНИЦИАЛИЗАЦИЯ =============
  
  async startConversation(agentId) {
    try {
      this.updateStatus('connecting', 'Инициализация...');
      this.log('🔌 Запуск улучшенного подключения к ElevenLabs Conversational AI...', 'info');
      this.metrics.connectionStartTime = Date.now();
      this.clearDiagnosticIssues();
      
      // Получаем доступ к микрофону
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });
      this.log('🎤 Микрофон подключен', 'success');
      this.addDiagnosticIssue('success', 'Микрофон успешно подключен');
      
      this.updateStatus('connecting', 'Получение signed URL...');
      
      // ✅ Получение signed URL - используем глобальный api объект
      const urlData = await window.api.get(`/${agentId}/signed-url`);
      const wsUrl = urlData.signed_url || urlData.fallback_url;
      
      if (!wsUrl) {
        throw new Error('Не получен URL для подключения к ElevenLabs');
      }
      
      this.log(`🔗 Signed URL получен`, 'success');
      this.updateStatus('connecting', 'Установка WebSocket соединения...');
      
      // Создаем WebSocket соединение
      this.websocket = new WebSocket(wsUrl);
      
      // Настраиваем обработчики
      this.websocket.onopen = () => this.onWebSocketOpen();
      this.websocket.onmessage = (event) => this.onWebSocketMessage(event);
      this.websocket.onclose = (event) => this.onWebSocketClose(event);
      this.websocket.onerror = (error) => this.onWebSocketError(error);
      
      return true;
      
    } catch (error) {
      this.log(`❌ Ошибка подключения: ${error.message}`, 'error');
      this.updateStatus('disconnected', 'Ошибка подключения');
      this.addDiagnosticIssue('error', `Ошибка подключения: ${error.message}`);
      window.ui.showNotification(`Ошибка подключения: ${error.message}`, 'error');
      return false;
    }
  }
  
  onWebSocketOpen() {
    this.log('✅ WebSocket подключен к ElevenLabs', 'success');
    this.updateStatus('connected', 'Подключен');
    this.isActive = true;
    
    this.metrics.connectionTime = Date.now() - this.metrics.connectionStartTime;
    this.addDiagnosticIssue('success', `Подключение установлено за ${this.metrics.connectionTime}ms`);
    
    // ✅ ПРАВИЛЬНАЯ инициализация с динамическими переменными
    const dynamicVariablesObj = {};
    window.dynamicVariables.forEach(variable => {
      if (variable.name && variable.value) {
        dynamicVariablesObj[variable.name] = variable.value;
      }
    });
    
    const initMessage = {
      "type": "conversation_initiation_client_data",
      "conversation_config_override": {
        "agent": {
          "language": window.currentAgentData?.language || "en"
        }
      },
      "dynamic_variables": dynamicVariablesObj
    };
    
    this.websocket.send(JSON.stringify(initMessage));
    this.log('📤 Отправлена правильная инициализация с динамическими переменными', 'success');
    
    // Настраиваем аудио и VAD с задержкой
    setTimeout(() => {
      this.setupAdaptiveAudio();
      this.setupLocalVAD();
      this.startDiagnosticMonitoring();
    }, 1000);
    
    // Запускаем ping-pong мониторинг
    this.startProperPingPongMonitoring();
    
    // Обновляем UI
    this.updateTestingUI(true);
    window.ui.showNotification('Подключение установлено! Говорите с ассистентом', 'success');
  }
  
  // ============= ПРАВИЛЬНАЯ ОБРАБОТКА СООБЩЕНИЙ =============
  
  onWebSocketMessage(event) {
    try {
      const data = JSON.parse(event.data);
      this.log(`📥 Получено: ${data.type}`, 'info');
      
      switch (data.type) {
        case 'conversation_initiation_metadata':
          this.handleInitiationMetadata(data);
          break;
          
        case 'audio':
          this.handleAudioResponse(data);
          break;
          
        case 'user_transcript':
          this.handleUserTranscript(data);
          break;
          
        case 'agent_response':
          this.handleAgentResponse(data);
          break;
          
        case 'vad_score':
          // ✅ Обрабатываем серверный VAD
          this.handleServerVadScore(data);
          break;
          
        case 'interruption':
          // ✅ Серверные прерывания
          this.handleServerInterruption(data);
          break;
          
        case 'ping':
          this.handlePing(data);
          break;
          
        case 'error':
          this.handleError(data);
          break;
          
        case 'agent_response_correction':
          this.handleAgentResponseCorrection(data);
          break;
          
        default:
          this.log(`❓ Неизвестный тип: ${data.type}`, 'warning');
      }
      
      this.updateMetricsDisplay();
      
    } catch (error) {
      this.log(`❌ Ошибка обработки сообщения: ${error.message}`, 'error');
      this.addDiagnosticIssue('error', `Ошибка парсинга сообщения: ${error.message}`);
    }
  }
  
  // ✅ ОБРАБОТКА СЕРВЕРНОГО VAD
  handleServerVadScore(data) {
    this.serverVADValue = data.vad_score_event?.vad_score || 0;
    this.metrics.vadEvents++;
    this.serverVADReceived = true;
    
    // Отмечаем что серверный VAD работает
    if (this.metrics.vadEvents === 1) {
      this.addDiagnosticIssue('success', 'Серверный VAD работает корректно');
    }
    
    this.updateVADDisplay();
  }
  
  // ✅ ОБЪЕДИНЕННОЕ отображение VAD
  updateVADDisplay() {
    // Серверный VAD
    const serverVadBar = document.getElementById('server-vad-bar');
    const serverVadScore = document.getElementById('server-vad-score');
    
    if (serverVadBar && serverVadScore) {
      const serverPercentage = Math.min(this.serverVADValue * 100, 100);
      serverVadBar.style.width = serverPercentage + '%';
      serverVadScore.textContent = this.serverVADValue.toFixed(2);
      
      if (this.serverVADValue > 0.6) {
        serverVadBar.classList.add('active');
      } else {
        serverVadBar.classList.remove('active');
      }
    }
    
    // Локальный VAD
    const localVadBar = document.getElementById('local-vad-bar');
    const localVadScore = document.getElementById('local-vad-score');
    
    if (localVadBar && localVadScore) {
      const localPercentage = Math.min(this.localVADValue * 100, 100);
      localVadBar.style.width = localPercentage + '%';
      localVadScore.textContent = this.localVADValue.toFixed(2);
      
      if (this.localVADValue > 0.3) {
        localVadBar.classList.add('active');
      } else {
        localVadBar.classList.remove('active');
      }
    }
    
    // Индикатор активности пользователя
    const userSpeakingIndicator = document.getElementById('user-speaking-indicator');
    const userSpeakingText = document.getElementById('user-speaking-text');
    
    if (userSpeakingIndicator && userSpeakingText) {
      // Используем серверный VAD если доступен, иначе локальный
      const vadValue = this.serverVADReceived ? this.serverVADValue : this.localVADValue;
      const threshold = this.serverVADReceived ? 0.5 : 0.3;
      
      if (vadValue > threshold) {
        userSpeakingIndicator.classList.add('active');
        userSpeakingText.textContent = this.serverVADReceived ? '🗣️ Говорит (сервер)' : '🗣️ Говорит (локально)';
      } else {
        userSpeakingIndicator.classList.remove('active');
        userSpeakingText.textContent = 'Молчание';
      }
    }
  }
  
  handleUserTranscript(data) {
    const transcript = data.user_transcription_event?.user_transcript;
    if (transcript) {
      this.metrics.transcriptEvents++;
      this.log(`📝 Вы: "${transcript}"`, 'info');
      this.updateStatus('connected', `Вы: "${transcript}"`);
      
      if (this.metrics.transcriptEvents === 1) {
        this.addDiagnosticIssue('success', 'Распознавание речи работает корректно');
      }
    }
  }
  
  handleServerInterruption(data) {
    this.log('⚡ СЕРВЕРНОЕ прерывание получено от ElevenLabs', 'warning');
    this.metrics.interruptions++;
    
    this.stopCurrentAudio();
    this.clearAudioQueue();
    
    this.updateStatus('connected', '⚡ Прервано сервером - продолжайте говорить');
    this.addDiagnosticIssue('success', 'Прерывание обработано корректно');
    window.ui.showNotification('Агент прерван сервером! Продолжайте говорить.', 'info');
  }
  
  // ============= АУДИО ОБРАБОТКА =============
  
  handleAudioResponse(data) {
    const audioData = data.audio_event;
    if (audioData?.audio_base_64) {
      this.log('🔊 Получен аудио чанк от агента', 'success');
      this.metrics.audioChunks++;
      
      const startTime = Date.now();
      this.addToJitterBuffer(audioData.audio_base_64, audioData.event_id, startTime);
    }
  }
  
  // ✅ JITTER BUFFER остается прежним
  addToJitterBuffer(audioBase64, eventId, startTime) {
    const audioChunk = {
      audioBase64,
      eventId,
      startTime,
      sequence: this.expectedSequence++
    };
    
    this.jitterBuffer.push(audioChunk);
    this.jitterBuffer.sort((a, b) => a.sequence - b.sequence);
    this.processJitterBuffer();
  }
  
  processJitterBuffer() {
    if (this.jitterBuffer.length < 2) {
      setTimeout(() => {
        if (this.jitterBuffer.length > 0) {
          this.flushJitterBuffer();
        }
      }, 50);
      return;
    }
    
    this.flushJitterBuffer();
  }
  
  flushJitterBuffer() {
    while (this.jitterBuffer.length > 0) {
      const chunk = this.jitterBuffer.shift();
      this.queueAudioChunk(chunk.audioBase64, chunk.eventId, chunk.startTime);
    }
  }
  
  queueAudioChunk(audioBase64, eventId, startTime) {
    try {
      const audioBlob = this.createAudioBlobFromBase64(audioBase64);
      const audioUrl = URL.createObjectURL(audioBlob);
      const audioElement = new Audio(audioUrl);
      
      audioElement.volume = 0.8;
      audioElement.preload = 'auto';
      
      this.audioQueue.push({
        audio: audioElement,
        url: audioUrl,
        eventId: eventId,
        startTime: startTime
      });
      
      if (!this.isPlayingAudio) {
        this.playNextAudioChunk();
      }
      
    } catch (error) {
      this.log(`❌ Ошибка добавления аудио в очередь: ${error.message}`, 'error');
    }
  }
  
  playNextAudioChunk() {
    if (this.audioQueue.length === 0) {
      this.isPlayingAudio = false;
      this.updateStatus('connected', 'Готов к разговору');
      this.updateAgentStatus('connected', 'Готов');
      this.log('✅ Воспроизведение завершено', 'success');
      return;
    }
    
    const chunk = this.audioQueue.shift();
    const { audio, url, eventId, startTime } = chunk;
    
    this.isPlayingAudio = true;
    this.currentAudioElement = audio;
    this.updateStatus('connected', 'Агент говорит...');
    this.updateAgentStatus('speaking', 'Говорит');
    
    audio.onended = () => {
      URL.revokeObjectURL(url);
      this.currentAudioElement = null;
      setTimeout(() => this.playNextAudioChunk(), 10);
    };
    
    audio.onerror = (error) => {
      this.log(`❌ Ошибка воспроизведения: ${error}`, 'error');
      URL.revokeObjectURL(url);
      this.currentAudioElement = null;
      setTimeout(() => this.playNextAudioChunk(), 10);
    };
    
    audio.play().catch(error => {
      this.log(`❌ Ошибка запуска: ${error.message}`, 'error');
      if (error.name === 'NotAllowedError') {
        window.ui.showNotification('Разрешите автовоспроизведение в браузере', 'warning');
      }
    });
  }
  
  stopCurrentAudio() {
    if (this.currentAudioElement) {
      this.currentAudioElement.pause();
      this.currentAudioElement.currentTime = 0;
      this.currentAudioElement = null;
    }
    this.isPlayingAudio = false;
  }
  
  clearAudioQueue() {
    this.log(`🗑️ Очистка аудио очереди (${this.audioQueue.length} элементов)`, 'info');
    
    this.audioQueue.forEach(item => {
      if (item.url) {
        URL.revokeObjectURL(item.url);
      }
    });
    
    this.audioQueue = [];
    this.jitterBuffer = [];
  }
  
  // ✅ АДАПТИВНАЯ настройка аудио
  setupAdaptiveAudio() {
    try {
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 16000
      });
      
      const source = this.audioContext.createMediaStreamSource(this.mediaStream);
      
      this.adjustBufferSize();
      this.audioProcessor = this.audioContext.createScriptProcessor(this.bufferSize, 1, 1);
      
      this.log(`🎤 Адаптивное аудио настроено: ${this.bufferSize} samples`, 'success');
      
      this.audioProcessor.onaudioprocess = (event) => {
        if (this.websocket?.readyState === WebSocket.OPEN && this.isActive) {
          const inputBuffer = event.inputBuffer;
          const channelData = inputBuffer.getChannelData(0);
          
          // Обновляем уровень входного сигнала для UI
          this.updateInputVolumeDisplay(channelData);
          
          // Отправляем аудио
          const pcmData = this.convertToPCM16(channelData);
          const base64Audio = this.arrayBufferToBase64(pcmData);
          
          const audioMessage = {
            user_audio_chunk: base64Audio
          };
          
          this.websocket.send(JSON.stringify(audioMessage));
        }
      };
      
      source.connect(this.audioProcessor);
      this.audioProcessor.connect(this.audioContext.destination);
      
      this.log('✅ Адаптивная аудио запись настроена', 'success');
      
    } catch (error) {
      this.log(`❌ Ошибка настройки аудио: ${error.message}`, 'error');
      this.addDiagnosticIssue('error', `Ошибка настройки аудио: ${error.message}`);
    }
  }
  
  adjustBufferSize() {
    if (this.averageRTT > 200) {
      this.bufferSize = Math.min(this.bufferSize * 2, this.maxBufferSize);
    } else if (this.averageRTT < 50) {
      this.bufferSize = Math.max(this.bufferSize / 2, this.minBufferSize);
    }
    
    this.log(`📊 Буфер адаптирован: ${this.bufferSize} samples (RTT: ${this.averageRTT}ms)`, 'info');
  }
  
  updateInputVolumeDisplay(channelData) {
    let sum = 0;
    for (let i = 0; i < channelData.length; i++) {
      sum += channelData[i] * channelData[i];
    }
    const rms = Math.sqrt(sum / channelData.length);
    this.inputVolumeLevel = Math.min(rms * 100, 100);
    
    const inputVolumeBar = document.getElementById('input-volume-bar');
    const inputVolumeEl = document.getElementById('input-volume');
    
    if (inputVolumeBar && inputVolumeEl) {
      inputVolumeBar.style.width = this.inputVolumeLevel + '%';
      inputVolumeEl.textContent = Math.round(this.inputVolumeLevel) + '%';
      
      if (this.inputVolumeLevel > 10) {
        inputVolumeBar.classList.add('active');
      } else {
        inputVolumeBar.classList.remove('active');
      }
    }
  }
  
  // ============= PING-PONG И RTT =============
  
  startProperPingPongMonitoring() {
    this.keepAliveInterval = setInterval(() => {
      if (this.websocket?.readyState === WebSocket.OPEN) {
        this.lastPingTime = Date.now();
        const pingMessage = { 
          type: "ping",
          timestamp: this.lastPingTime
        };
        this.websocket.send(JSON.stringify(pingMessage));
      }
    }, 10000);
  }
  
  handlePing(data) {
    const pongMessage = {
      "type": "pong",
      "event_id": data.ping_event?.event_id
    };
    
    this.websocket.send(JSON.stringify(pongMessage));
    
    if (this.lastPingTime) {
      const rtt = Date.now() - this.lastPingTime;
      this.updateRTT(rtt);
      this.lastPingTime = null;
    }
  }
  
  updateRTT(rtt) {
    this.metrics.rtt = rtt;
    this.rttHistory.push(rtt);
    
    if (this.rttHistory.length > 10) {
      this.rttHistory.shift();
    }
    
    this.averageRTT = this.rttHistory.reduce((a, b) => a + b, 0) / this.rttHistory.length;
    this.adjustBufferSize();
    this.updateBufferHealth();
  }
  
  updateBufferHealth() {
    const rttVariance = this.calculateRTTVariance();
    const health = Math.max(0, Math.min(100, 100 - (rttVariance / 50) * 100));
    this.metrics.bufferHealth = Math.round(health);
  }
  
  calculateRTTVariance() {
    if (this.rttHistory.length < 2) return 0;
    
    const mean = this.averageRTT;
    const variance = this.rttHistory.reduce((sum, rtt) => {
      return sum + Math.pow(rtt - mean, 2);
    }, 0) / this.rttHistory.length;
    
    return Math.sqrt(variance);
  }
  
  // ============= ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ =============
  
  handleInitiationMetadata(data) {
    const metadata = data.conversation_initiation_metadata_event;
    if (metadata) {
      this.conversationId = metadata.conversation_id;
      this.log(`🎯 Разговор инициализирован: ${this.conversationId}`, 'success');
      this.addDiagnosticIssue('success', `Разговор инициализирован: ${this.conversationId}`);
    }
  }
  
  handleAgentResponse(data) {
    const agentResponse = data.agent_response_event?.agent_response;
    if (agentResponse) {
      this.log(`🤖 Агент: "${agentResponse}"`, 'info');
      this.updateStatus('connected', `Агент: "${agentResponse}"`);
    }
  }
  
  handleAgentResponseCorrection(data) {
    this.log('✏️ Агент исправил свой ответ', 'info');
  }
  
  handleError(data) {
    const error = data.error_event || data.error;
    const message = error?.message || 'Неизвестная ошибка';
    this.log(`❌ Ошибка от сервера: ${message}`, 'error');
    this.addDiagnosticIssue('error', `Серверная ошибка: ${message}`);
    window.ui.showNotification(`Ошибка: ${message}`, 'error');
  }
  
  // ============= ЗАВЕРШЕНИЕ И ОЧИСТКА =============
  
  async stopConversation() {
    this.log('🛑 Остановка разговора...', 'info');
    this.isActive = false;
    
    // Останавливаем мониторинг
    this.stopDiagnosticMonitoring();
    
    if (this.keepAliveInterval) {
      clearInterval(this.keepAliveInterval);
      this.keepAliveInterval = null;
    }
    
    // Очищаем аудио
    this.stopCurrentAudio();
    this.clearAudioQueue();
    
    // Закрываем WebSocket
    if (this.websocket) {
      this.websocket.close();
      this.websocket = null;
    }
    
    // Останавливаем медиа
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }
    
    // Отключаем аудио контекст
    if (this.audioContext) {
      await this.audioContext.close();
      this.audioContext = null;
    }
    
    this.audioProcessor = null;
    this.localVADProcessor = null;
    
    this.updateStatus('disconnected', 'Отключен');
    this.updateAgentStatus('disconnected', 'Остановлен');
    this.updateTestingUI(false);
    
    this.log('✅ Разговор завершен', 'success');
    window.ui.showNotification('Разговор завершен', 'info');
  }
  
  // ============= ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ =============
  
  onWebSocketClose(event) {
    this.log(`🔌 WebSocket закрыт: код ${event.code}`, 'warning');
    this.updateStatus('disconnected', 'Соединение закрыто');
    this.isActive = false;
    
    if (event.code !== 1000) {
      this.addDiagnosticIssue('warning', `Соединение закрыто с кодом ${event.code}`);
    }
  }
  
  onWebSocketError(error) {
    this.log(`❌ WebSocket ошибка: ${error}`, 'error');
    this.updateStatus('disconnected', 'Ошибка соединения');
    this.addDiagnosticIssue('error', `WebSocket ошибка: ${error}`);
  }
  
  createAudioBlobFromBase64(base64Audio) {
    const binaryString = atob(base64Audio);
    const bytes = new Uint8Array(binaryString.length);
    
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    return new Blob([bytes], { type: 'audio/mp3' });
  }
  
  convertToPCM16(float32Array) {
    const buffer = new ArrayBuffer(float32Array.length * 2);
    const view = new DataView(buffer);
    let offset = 0;
    
    for (let i = 0; i < float32Array.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    
    return buffer;
  }
  
  arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }
  
  // ============= UI UPDATES =============
  
  updateStatus(status, message) {
    const connectionStatus = document.getElementById('connection-status');
    if (connectionStatus) {
      const indicator = connectionStatus.querySelector('.status-indicator');
      const text = connectionStatus.querySelector('span');
      
      indicator.className = `status-indicator ${status}`;
      text.textContent = `WebSocket: ${message}`;
    }
  }
  
  updateAgentStatus(status, message) {
    const agentStatus = document.getElementById('agent-status');
    if (agentStatus) {
      const indicator = agentStatus.querySelector('.status-indicator');
      const text = agentStatus.querySelector('span');
      
      indicator.className = `status-indicator ${status}`;
      text.textContent = `Агент: ${message}`;
    }
  }
  
  updateTestingUI(isConnected) {
    const startTestBtn = document.getElementById('start-test-btn');
    const stopTestBtn = document.getElementById('stop-test-btn');
    const retryConnectionBtn = document.getElementById('retry-connection-btn');
    const audioIndicators = document.getElementById('audio-indicators');
    const performanceMetrics = document.getElementById('performance-metrics');
    
    if (startTestBtn) startTestBtn.style.display = isConnected ? 'none' : 'inline-flex';
    if (stopTestBtn) stopTestBtn.style.display = isConnected ? 'inline-flex' : 'none';
    if (retryConnectionBtn) retryConnectionBtn.style.display = isConnected ? 'none' : 'inline-flex';
    if (audioIndicators) audioIndicators.style.display = isConnected ? 'flex' : 'none';
    if (performanceMetrics) performanceMetrics.style.display = isConnected ? 'block' : 'none';
  }
  
  updateMetricsDisplay() {
    if (!this.isActive) return;
    
    this.metrics.sessionDuration = Math.round((Date.now() - this.metrics.connectionStartTime) / 1000);
    
    const updates = {
      'connection-time': `${this.metrics.connectionTime}мс`,
      'interruptions-count': this.metrics.interruptions,
      'audio-chunks-count': this.metrics.audioChunks,
      'rtt-value': `${Math.round(this.averageRTT)}мс`,
      'session-duration': `${this.metrics.sessionDuration}с`,
      'vad-events-count': this.metrics.vadEvents,
      'transcript-events-count': this.metrics.transcriptEvents,
      'buffer-health': `${this.metrics.bufferHealth}%`
    };
    
    Object.entries(updates).forEach(([id, value]) => {
      const element = document.getElementById(id);
      if (element) {
        element.textContent = value;
      }
    });
  }
  
  log(message, type = 'info') {
    const timestamp = new Date().toLocaleTimeString();
    const prefix = type === 'error' ? '❌' : type === 'warning' ? '⚠️' : type === 'success' ? '✅' : 'ℹ️';
    console.log(`${prefix} [${timestamp}] ${message}`);
  }
}

// Экспортируем класс в глобальную область видимости
window.EnhancedElevenLabsConversationManager = EnhancedElevenLabsConversationManager;
