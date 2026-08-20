/* ============================================================ */
/* Voicyfy AI - Audio System                                    */
/* Voice LLM Interface - Voicyfy                                */
/* Version: 4.3.2 - IIFE wrapped                                */
/* ============================================================ */

'use strict';

(function() {

// Get config reference
const AudioConfig = window.VoicyfyConfig;

// ============================================================================
// GLOBAL AUDIO STATE
// ============================================================================

window.audioInitialized = false;
window.globalAudioContext = null;
window.globalMicStream = null;
window.silentAudioBuffer = null;

// ============================================================================
// AUDIO PLAYBACK STATE
// ============================================================================

let playbackAudioContext = null;
let audioBufferQueue = [];
let isSchedulingAudio = false;
let nextPlayTime = 0;
let scheduledSources = [];
let schedulerInterval = null;

// ============================================================================
// RECORDING STATE
// ============================================================================

let audioProcessor = null;
let isListening = false;
let isPlayingAudio = false;
let hasAudioData = false;
let audioDataStartTime = 0;

// ============================================================================
// MUTE STATE
// ============================================================================

let isMuted = false;

// ============================================================================
// INTERRUPTION STATE
// ============================================================================

const interruptionState = {
    is_assistant_speaking: false,
    is_user_speaking: false,
    last_speech_start: 0,
    last_speech_stop: 0,
    interruption_count: 0,
    last_interruption_time: 0
};

// ============================================================================
// MUTE FUNCTIONS
// ============================================================================

function toggleMute() {
    isMuted = !isMuted;
    AudioConfig.log(`🎤 Mute toggled: ${isMuted ? 'ON (muted)' : 'OFF (active)'}`);
    
    updateMuteUI();
    
    if (isMuted) {
        // Stop listening when muted
        stopListening();
        
        // Clear audio buffer when muting
        if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
            window.websocket.send(JSON.stringify({
                type: "input_audio_buffer.clear",
                event_id: `clear_mute_${Date.now()}`
            }));
        }
        
        // Reset audio data tracking
        hasAudioData = false;
        audioDataStartTime = 0;
    } else {
        // Resume listening when unmuted (if connected and not playing)
        if (window.isConnected && !isPlayingAudio && !window.isStreamingLLM && !window.isReconnecting) {
            startListening();
        }
    }
    
    return isMuted;
}

function updateMuteUI() {
    const muteButton = document.getElementById('muteButton');
    const muteIcon = document.getElementById('muteIcon');
    const muteStatus = document.getElementById('muteStatus');
    const voicyfySphere = document.getElementById('voicyfySphere');
    const voiceStatus = document.getElementById('voiceStatus');
    const statusText = document.getElementById('statusText');
    
    if (muteButton) {
        muteButton.classList.toggle('muted', isMuted);
        muteButton.title = isMuted ? 'Включить микрофон' : 'Отключить микрофон';
    }
    
    if (muteIcon) {
        muteIcon.className = isMuted ? 'fas fa-microphone-slash' : 'fas fa-microphone';
    }
    
    if (muteStatus) {
        muteStatus.classList.toggle('visible', isMuted);
    }
    
    if (voicyfySphere) {
        if (isMuted) {
            voicyfySphere.classList.remove('listening');
            voicyfySphere.classList.add('muted');
        } else {
            voicyfySphere.classList.remove('muted');
            // Restore listening state if connected
            if (window.isConnected && !isPlayingAudio && !window.isStreamingLLM) {
                voicyfySphere.classList.add('listening');
            }
        }
    }
    
    // Update status indicator
    if (isMuted && voiceStatus && statusText) {
        voiceStatus.className = 'voice-status muted';
        statusText.textContent = 'Микрофон отключён';
    } else if (!isMuted && window.isConnected) {
        if (typeof window.updateStatus === 'function') {
            window.updateStatus('connected', 'Готов', 'connected');
        }
    }
}

function getMuteState() {
    return isMuted;
}

// ============================================================================
// AUDIO INITIALIZATION
// ============================================================================

async function initializeAudio() {
    AudioConfig.log('🎤 Initializing audio system...');
    
    try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error("Browser doesn't support microphone access");
        }

        // Create AudioContext
        if (!window.globalAudioContext) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            window.globalAudioContext = new AudioContextClass({
                sampleRate: 24000,
                latencyHint: 'interactive'
            });
            AudioConfig.log(`   AudioContext created: ${window.globalAudioContext.sampleRate} Hz`);
        }

        // Resume if suspended
        if (window.globalAudioContext.state === 'suspended') {
            await window.globalAudioContext.resume();
            AudioConfig.log('   AudioContext resumed');
        }

        // Get microphone stream
        if (!window.globalMicStream) {
            window.globalMicStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 24000,
                    channelCount: 1
                }
            });
            AudioConfig.log('   Microphone stream acquired');
        }

        // iOS silent buffer workaround
        if (AudioConfig.isIOS && !window.silentAudioBuffer) {
            try {
                window.silentAudioBuffer = window.globalAudioContext.createBuffer(
                    1, 1, window.globalAudioContext.sampleRate
                );
                const silentSource = window.globalAudioContext.createBufferSource();
                silentSource.buffer = window.silentAudioBuffer;
                silentSource.connect(window.globalAudioContext.destination);
                silentSource.start(0);
                AudioConfig.log('   iOS: Silent buffer played');
            } catch (e) {
                AudioConfig.log('   iOS: Silent buffer failed (non-critical)', 'warn');
            }
        }

        window.audioInitialized = true;
        AudioConfig.log('✅ Audio system initialized');
        return true;

    } catch (error) {
        AudioConfig.log(`❌ Audio initialization failed: ${error.message}`, 'error');
        return false;
    }
}

// ============================================================================
// PLAYBACK AUDIO CONTEXT
// ============================================================================

function initPlaybackAudioContext() {
    if (!playbackAudioContext) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        playbackAudioContext = new AudioContextClass({
            sampleRate: AudioConfig.AUDIO_PLAYBACK_SAMPLE_RATE,
            latencyHint: 'playback'
        });
        AudioConfig.log('🔊 Playback AudioContext created');
    }
    
    if (playbackAudioContext.state === 'suspended') {
        playbackAudioContext.resume().then(() => {
            AudioConfig.log('🔊 Playback AudioContext resumed');
        });
    }
    
    return playbackAudioContext;
}

// ============================================================================
// AUDIO DATA CONVERSION
// ============================================================================

function base64ToInt16Array(base64) {
    try {
        const binaryString = atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return new Int16Array(bytes.buffer);
    } catch (e) {
        AudioConfig.log(`Base64 decode error: ${e.message}`, 'error');
        return new Int16Array(0);
    }
}

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

// ============================================================================
// AUDIO PLAYBACK SYSTEM
// ============================================================================

function addAudioChunkToBuffer(base64Data) {
    if (!base64Data || typeof base64Data !== 'string') return;
    
    const int16Samples = base64ToInt16Array(base64Data);
    if (int16Samples.length === 0) return;
    
    for (let i = 0; i < int16Samples.length; i++) {
        audioBufferQueue.push(int16Samples[i]);
    }
    
    if (!isSchedulingAudio && audioBufferQueue.length >= AudioConfig.MIN_BUFFER_SAMPLES) {
        startAudioScheduler();
    }
}

function startAudioScheduler() {
    if (isSchedulingAudio) return;
    
    isSchedulingAudio = true;
    isPlayingAudio = true;
    
    const ctx = initPlaybackAudioContext();
    if (ctx.state === 'suspended') ctx.resume();
    
    nextPlayTime = ctx.currentTime + 0.15;
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.add('speaking');
        voicyfySphere.classList.remove('listening', 'processing', 'streaming', 'muted');
    }
    
    if (typeof window.updateStatus === 'function') {
        window.updateStatus('speaking', 'Говорит...', 'speaking');
    }
    
    interruptionState.is_assistant_speaking = true;
    
    schedulerInterval = setInterval(scheduleNextChunk, AudioConfig.SCHEDULER_INTERVAL_MS);
    scheduleNextChunk();
}

function scheduleNextChunk() {
    if (!isSchedulingAudio) {
        if (schedulerInterval) {
            clearInterval(schedulerInterval);
            schedulerInterval = null;
        }
        return;
    }
    
    const ctx = playbackAudioContext;
    if (!ctx) return;
    
    if (nextPlayTime < ctx.currentTime) {
        nextPlayTime = ctx.currentTime + 0.05;
    }
    
    while (audioBufferQueue.length >= AudioConfig.CHUNK_SIZE && nextPlayTime < ctx.currentTime + AudioConfig.SCHEDULE_AHEAD_TIME) {
        const chunkSize = Math.min(AudioConfig.CHUNK_SIZE, audioBufferQueue.length);
        const chunk = audioBufferQueue.splice(0, chunkSize);
        
        const audioBuffer = ctx.createBuffer(1, chunk.length, AudioConfig.AUDIO_PLAYBACK_SAMPLE_RATE);
        const channelData = audioBuffer.getChannelData(0);
        
        for (let i = 0; i < chunk.length; i++) {
            let sample = chunk[i] / 32768.0;
            
            // Crossfade
            if (i < AudioConfig.CROSSFADE_SAMPLES) {
                sample *= (i / AudioConfig.CROSSFADE_SAMPLES);
            } else if (i >= chunk.length - AudioConfig.CROSSFADE_SAMPLES) {
                const fadePos = chunk.length - i;
                sample *= (fadePos / AudioConfig.CROSSFADE_SAMPLES);
            }
            
            channelData[i] = sample;
        }
        
        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(ctx.destination);
        source.start(nextPlayTime);
        nextPlayTime += audioBuffer.duration;
        
        scheduledSources.push(source);
        
        source.onended = () => {
            const idx = scheduledSources.indexOf(source);
            if (idx > -1) scheduledSources.splice(idx, 1);
            
            if (scheduledSources.length === 0 && audioBufferQueue.length < AudioConfig.CHUNK_SIZE && isSchedulingAudio) {
                setTimeout(() => {
                    if (scheduledSources.length === 0 && audioBufferQueue.length < AudioConfig.CHUNK_SIZE) {
                        finishAudioPlayback();
                    }
                }, 200);
            }
        };
    }
}

function finishAudioPlayback() {
    AudioConfig.log('🔇 Audio playback finished');
    
    if (schedulerInterval) {
        clearInterval(schedulerInterval);
        schedulerInterval = null;
    }
    
    isSchedulingAudio = false;
    isPlayingAudio = false;
    interruptionState.is_assistant_speaking = false;
    audioBufferQueue = [];
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('speaking');
        
        // Restore muted state if muted
        if (isMuted) {
            voicyfySphere.classList.add('muted');
        }
    }
    
    // FIX: Проверяем window.isStreamingLLM
    if (!window.isStreamingLLM) {
        if (typeof window.updateStatus === 'function') {
            if (isMuted) {
                window.updateStatus('muted', 'Микрофон отключён', 'muted');
            } else {
                window.updateStatus('connected', 'Готов', 'connected');
            }
        }
        
        setTimeout(() => {
            // FIX: Проверяем все условия включая window.isStreamingLLM
            if (!isPlayingAudio && !window.isStreamingLLM && !window.isReconnecting && !isMuted) {
                startListening();
            }
        }, 400);
    }
}

function stopAllAudioPlayback() {
    if (schedulerInterval) {
        clearInterval(schedulerInterval);
        schedulerInterval = null;
    }
    
    isSchedulingAudio = false;
    isPlayingAudio = false;
    interruptionState.is_assistant_speaking = false;
    
    scheduledSources.forEach(source => {
        try { source.stop(); } catch (e) {}
    });
    scheduledSources = [];
    audioBufferQueue = [];
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('speaking');
    }
}

// ============================================================================
// AUDIO RECORDING / LISTENING
// ============================================================================

async function startListening() {
    // Check conditions - FIX: используем window. для глобальных переменных
    if (!window.isConnected || isPlayingAudio || window.isReconnecting || isListening || window.isStreamingLLM) {
        return;
    }
    
    // Don't start listening if muted
    if (isMuted) {
        AudioConfig.log('🎤 Listening blocked: microphone is muted');
        return;
    }
    
    // FIX: Проверяем userActivated
    if (!window.userActivated) {
        AudioConfig.log('🎤 Listening blocked: user not activated');
        return;
    }
    
    if (!window.audioInitialized) {
        const success = await initializeAudio();
        if (!success) return;
    }
    
    isListening = true;
    AudioConfig.log('🎤 Started listening');
    
    // Clear buffer on server
    if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
        window.websocket.send(JSON.stringify({
            type: "input_audio_buffer.clear",
            event_id: `clear_${Date.now()}`
        }));
    }
    
    // Resume AudioContext if needed
    if (window.globalAudioContext.state === 'suspended') {
        await window.globalAudioContext.resume();
    }
    
    // Create audio processor if not exists
    if (!audioProcessor) {
        const bufferSize = 2048;
        audioProcessor = window.globalAudioContext.createScriptProcessor(bufferSize, 1, 1);
        
        let isSilent = true;
        let silenceStartTime = Date.now();
        let lastCommitTime = 0;
        let hasSentAudioInCurrentSegment = false;
        
        audioProcessor.onaudioprocess = function(e) {
            // Skip if muted
            if (isMuted) {
                // Update visualizer to show muted state
                const voicyfySphere = document.getElementById('voicyfySphere');
                if (voicyfySphere && !voicyfySphere.classList.contains('muted')) {
                    voicyfySphere.classList.add('muted');
                    voicyfySphere.classList.remove('listening');
                }
                return;
            }
            
            if (isListening && window.websocket && window.websocket.readyState === WebSocket.OPEN && !window.isReconnecting) {
                let inputData = e.inputBuffer.getChannelData(0);
                if (inputData.length === 0) return;
                
                // Calculate amplitude
                let maxAmplitude = 0;
                for (let i = 0; i < inputData.length; i++) {
                    maxAmplitude = Math.max(maxAmplitude, Math.abs(inputData[i]));
                }
                
                // Mobile amplification
                if (AudioConfig.isMobile && AudioConfig.AUDIO_CONFIG.amplificationFactor > 1.0) {
                    const amplifiedData = new Float32Array(inputData.length);
                    for (let i = 0; i < inputData.length; i++) {
                        amplifiedData[i] = Math.max(-1.0, Math.min(1.0, inputData[i] * AudioConfig.AUDIO_CONFIG.amplificationFactor));
                    }
                    inputData = amplifiedData;
                    maxAmplitude = 0;
                    for (let i = 0; i < inputData.length; i++) {
                        maxAmplitude = Math.max(maxAmplitude, Math.abs(inputData[i]));
                    }
                }
                
                const hasSound = maxAmplitude > AudioConfig.AUDIO_CONFIG.soundDetectionThreshold;
                
                // Update visualizer
                updateCircularVisualization(inputData);
                
                // Convert to PCM16
                const pcm16Data = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    pcm16Data[i] = Math.max(-32768, Math.min(32767, Math.floor(inputData[i] * 32767)));
                }
                
                // Send to server
                try {
                    window.websocket.send(JSON.stringify({
                        type: "input_audio_buffer.append",
                        event_id: `audio_${Date.now()}`,
                        audio: arrayBufferToBase64(pcm16Data.buffer)
                    }));
                    hasSentAudioInCurrentSegment = true;
                    
                    if (!hasAudioData && hasSound) {
                        hasAudioData = true;
                        audioDataStartTime = Date.now();
                    }
                } catch (error) {
                    AudioConfig.log(`Audio send error: ${error.message}`, "error");
                }
                
                const now = Date.now();
                
                // Handle silence detection
                if (hasSound) {
                    isSilent = false;
                    silenceStartTime = now;
                    
                    const voicyfySphere = document.getElementById('voicyfySphere');
                    if (voicyfySphere && 
                        !voicyfySphere.classList.contains('listening') && 
                        !voicyfySphere.classList.contains('speaking') &&
                        !voicyfySphere.classList.contains('processing') &&
                        !voicyfySphere.classList.contains('streaming') &&
                        !voicyfySphere.classList.contains('muted')) {
                        voicyfySphere.classList.add('listening');
                    }
                } else if (!isSilent) {
                    const silenceDuration = now - silenceStartTime;
                    
                    if (silenceDuration > AudioConfig.AUDIO_CONFIG.silenceDuration) {
                        isSilent = true;
                        
                        if (now - lastCommitTime > 1000 && hasSentAudioInCurrentSegment) {
                            setTimeout(() => {
                                if (isSilent && isListening && !window.isReconnecting && !isMuted) {
                                    commitAudioBuffer();
                                    lastCommitTime = Date.now();
                                    hasSentAudioInCurrentSegment = false;
                                }
                            }, 100);
                        }
                    }
                }
            }
        };
        
        // Connect processor
        const streamSource = window.globalAudioContext.createMediaStreamSource(window.globalMicStream);
        streamSource.connect(audioProcessor);
        
        const gainNode = window.globalAudioContext.createGain();
        gainNode.gain.value = 0;
        audioProcessor.connect(gainNode);
        gainNode.connect(window.globalAudioContext.destination);
    }
    
    // Reset tracking
    hasAudioData = false;
    audioDataStartTime = 0;
    
    // Update UI
    const voicyfySphere = document.getElementById('voicyfySphere');
    // FIX: Проверяем window.isStreamingLLM
    if (voicyfySphere && !isPlayingAudio && !window.isStreamingLLM && !isMuted) {
        voicyfySphere.classList.add('listening');
        voicyfySphere.classList.remove('speaking', 'processing', 'streaming', 'muted');
    }
    
    AudioConfig.log("✅ Listening started");
}

function stopListening() {
    isListening = false;
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('listening');
    }
    
    AudioConfig.log("⏹️ Listening stopped");
}

function commitAudioBuffer() {
    if (!isListening || !window.websocket || window.websocket.readyState !== WebSocket.OPEN || window.isReconnecting) {
        return;
    }
    
    if (!hasAudioData) return;
    
    // Don't commit if muted
    if (isMuted) {
        hasAudioData = false;
        audioDataStartTime = 0;
        return;
    }
    
    const audioLength = Date.now() - audioDataStartTime;
    if (audioLength < AudioConfig.MINIMUM_AUDIO_LENGTH) {
        setTimeout(() => {
            if (isListening && hasAudioData && !window.isReconnecting && !isMuted) {
                sendCommitBuffer();
            }
        }, AudioConfig.MINIMUM_AUDIO_LENGTH - audioLength + 50);
        return;
    }
    
    sendCommitBuffer();
}

function sendCommitBuffer() {
    const audioLength = Date.now() - audioDataStartTime;
    if (audioLength < 100) {
        hasAudioData = false;
        audioDataStartTime = 0;
        return;
    }
    
    const voicyfySphere = document.getElementById('voicyfySphere');
    if (voicyfySphere) {
        voicyfySphere.classList.remove('listening');
    }
    
    window.websocket.send(JSON.stringify({
        type: "input_audio_buffer.commit",
        event_id: `commit_${Date.now()}`
    }));
    
    AudioConfig.log('📤 Audio buffer committed');
    
    hasAudioData = false;
    audioDataStartTime = 0;
}

// ============================================================================
// CIRCULAR VISUALIZER
// ============================================================================

function createCircularVisualizer() {
    const circularViz = document.getElementById('circularViz');
    if (!circularViz) return;
    
    const barCount = AudioConfig.isMobile ? 40 : 60;
    const angleStep = 360 / barCount;
    
    for (let i = 0; i < barCount; i++) {
        const bar = document.createElement('div');
        bar.className = 'viz-bar';
        bar.style.transform = `rotate(${i * angleStep}deg) translateY(-140px)`;
        bar.style.height = '20px';
        circularViz.appendChild(bar);
    }
    
    AudioConfig.log(`🎨 Visualizer created: ${barCount} bars`);
}

function updateCircularVisualization(audioData) {
    const circularViz = document.getElementById('circularViz');
    if (!circularViz) return;
    
    const bars = circularViz.querySelectorAll('.viz-bar');
    if (bars.length === 0) return;
    
    const step = Math.floor(audioData.length / bars.length);
    
    for (let i = 0; i < bars.length; i++) {
        let sum = 0;
        for (let j = 0; j < step; j++) {
            const index = i * step + j;
            if (index < audioData.length) sum += Math.abs(audioData[index]);
        }
        const average = sum / step;
        const multiplier = AudioConfig.isMobile ? 300 : 200;
        const height = 20 + Math.min(80, Math.floor(average * multiplier));
        bars[i].style.height = `${height}px`;
    }
}

// ============================================================================
// GETTERS FOR STATE
// ============================================================================

function getIsListening() {
    return isListening;
}

function getIsPlayingAudio() {
    return isPlayingAudio;
}

function getInterruptionState() {
    return interruptionState;
}

// ============================================================================
// EXPORT TO GLOBAL SCOPE
// ============================================================================

window.VoicyfyAudio = {
    // Initialization
    initializeAudio,
    initPlaybackAudioContext,
    
    // Mute
    toggleMute,
    updateMuteUI,
    getMuteState,
    
    // Playback
    addAudioChunkToBuffer,
    stopAllAudioPlayback,
    
    // Recording
    startListening,
    stopListening,
    commitAudioBuffer,
    
    // Visualizer
    createCircularVisualizer,
    updateCircularVisualization,
    
    // State getters
    getIsListening,
    getIsPlayingAudio,
    getInterruptionState
};

AudioConfig.log('🎵 Audio module loaded');

})();
