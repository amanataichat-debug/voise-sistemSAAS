/**
 * 🚀 Grok Voice Widget v1.1 - PRODUCTION (xAI Grok + VOICYFY UI)
 * xAI Grok Voice Agent API Integration with Voicyfy Branding
 * 
 * ✅ UI: 100% Match with OpenAI/Gemini Widget (Blue/Clean/Voicyfy)
 * ✅ Logic: Pure Grok VAD (Server-side decision making)
 * ✅ Continuous audio streaming via AudioWorklet
 * ✅ Instant interruptions & Zero-latency playback
 * ✅ 5 Voices: Ara, Rex, Sal, Eve, Leo
 * 
 * CHANGELOG v1.1:
 * 🔧 FIXED: Audio buffer race condition - first chunks were lost
 * 🔧 FIXED: AudioWorklet now pre-initialized before audio arrives
 * 🔧 FIXED: Added pendingAudioQueue for buffering early chunks
 * 
 * @version 1.1
 * @author Voicyfy Team
 * @license MIT
 */

(function() {
    'use strict';

    // ============================================================================
    // CONFIGURATION
    // ============================================================================

    const CONFIG = {
        assistantId: null,
        serverUrl: null,
        position: 'bottom-right',
        
        audio: {
            inputSampleRate: 16000,
            outputSampleRate: 24000,
            playbackSampleRate: 24000,
            actualSampleRate: null,
            channelCount: 1,
            bitsPerSample: 16,
            chunkDuration: 100,
            needsResampling: false
        },
        
        vad: {
            enabled: true,
            speechThreshold: -38,
            visualSilenceThreshold: -45
        },
        
        ws: {
            reconnectDelay: 2000,
            maxReconnectAttempts: 5,
            pingInterval: 30000
        },
        
        setup: {
            waitAfterSetup: 800,
            maxSetupWait: 10000
        }
    };

    // ============================================================================
    // STATE MANAGEMENT
    // ============================================================================

    const STATE = {
        ws: null,
        isConnected: false,
        isSetupComplete: false,
        readyToRecord: false,
        isRecording: false,
        isPlaying: false,
        isSpeaking: false,
        audioContext: null,
        mediaStream: null,
        audioWorkletNode: null,
        audioStreamNode: null,
        pingInterval: null,
        reconnectAttempts: 0,
        lastSpeechTime: 0,
        sessionConfig: null,
        errorState: null,
        setupTimeout: null,
        isWidgetOpen: false,
        audioChunksProcessed: 0,
        audioWorkletReady: false,
        streamWorkletReady: false,
        playbackAnimationId: null,
        
        // v1.1: Audio buffering for race condition fix
        pendingAudioQueue: [],
        audioStreamInitialized: false,
        
        ui: {} 
    };

    // ============================================================================
    // AUDIOWORKLET PROCESSOR CODE
    // ============================================================================

    const RECORDER_WORKLET_CODE = `
class RecorderWorkletProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.bufferSize = 4096;
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || !input[0]) return true;

        const inputData = input[0];

        for (let i = 0; i < inputData.length; i++) {
            this.buffer[this.bufferIndex++] = inputData[i];

            if (this.bufferIndex >= this.bufferSize) {
                this.port.postMessage({
                    type: 'audioData',
                    data: this.buffer.slice(0, this.bufferIndex)
                });
                this.bufferIndex = 0;
            }
        }

        return true;
    }
}
registerProcessor('recorder-worklet', RecorderWorkletProcessor);
`;

    const STREAM_WORKLET_CODE = `
class AudioStreamProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.audioQueue = [];
        this.currentBuffer = null;
        this.bufferIndex = 0;
        this.samplesProcessed = 0;
        this.isActive = false;
        
        this.port.onmessage = (e) => {
            if (e.data.type === 'audioData') {
                this.audioQueue.push(e.data.buffer);
                if (!this.isActive) {
                    this.isActive = true;
                    this.port.postMessage({ type: 'started' });
                }
            } else if (e.data.type === 'clear') {
                this.audioQueue = [];
                this.currentBuffer = null;
                this.bufferIndex = 0;
                this.isActive = false;
                this.port.postMessage({ type: 'cleared' });
            } else if (e.data.type === 'stop') {
                this.isActive = false;
                this.port.postMessage({ type: 'stopped' });
            }
        };
    }
    
    process(inputs, outputs, parameters) {
        const output = outputs[0];
        if (!output || !output[0]) return true;
        
        const outputChannel = output[0];
        
        for (let i = 0; i < outputChannel.length; i++) {
            if (!this.currentBuffer || this.bufferIndex >= this.currentBuffer.length) {
                if (this.audioQueue.length > 0) {
                    this.currentBuffer = this.audioQueue.shift();
                    this.bufferIndex = 0;
                } else {
                    outputChannel[i] = 0;
                    continue;
                }
            }
            
            outputChannel[i] = this.currentBuffer[this.bufferIndex++];
            this.samplesProcessed++;
        }
        
        if (this.samplesProcessed % 4800 === 0) {
            this.port.postMessage({
                type: 'stats',
                queueLength: this.audioQueue.length,
                samplesProcessed: this.samplesProcessed
            });
        }
        
        return true;
    }
}
registerProcessor('audio-stream-processor', AudioStreamProcessor);
`;

    // ============================================================================
    // INITIALIZATION
    // ============================================================================

    function init() {
        console.log('[GROK-WIDGET] 🚀 Initializing v1.1 (Voicyfy UI + Audio Fix)...');
        
        const getScriptTag = () => {
            const scripts = document.querySelectorAll('script');
            for (let i = 0; i < scripts.length; i++) {
                if (scripts[i].hasAttribute('data-assistantId') || scripts[i].dataset.assistantId) return scripts[i];
            }
            return document.currentScript;
        };

        const scriptTag = getScriptTag();
        
        if (!scriptTag) {
            console.error('[GROK-WIDGET] Script tag with data-assistantId not found');
            return;
        }

        CONFIG.assistantId = scriptTag.getAttribute('data-assistantId') || scriptTag.dataset.assistantId;
        CONFIG.serverUrl = scriptTag.getAttribute('data-server') || scriptTag.dataset.server;
        
        const posAttr = scriptTag.getAttribute('data-position') || scriptTag.dataset.position;
        if (posAttr) CONFIG.position = posAttr;

        if (!CONFIG.assistantId || !CONFIG.serverUrl) {
            console.error('[GROK-WIDGET] Missing required parameters (assistantId or server)');
            return;
        }
        
        if (!document.getElementById('font-awesome-css')) {
            const link = document.createElement('link');
            link.id = 'font-awesome-css';
            link.rel = 'stylesheet';
            link.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css';
            document.head.appendChild(link);
        }

        createStyles();
        createWidgetHTML();
        cacheUIElements();
        
        document.addEventListener('click', initAudioContext, { once: true });
        document.addEventListener('touchstart', initAudioContext, { once: true });
        
        console.log('[GROK-WIDGET] ✅ Initialization complete');
    }

    async function initAudioContext() {
        if (STATE.audioContext) {
            if (STATE.audioContext.state === 'suspended') {
                await STATE.audioContext.resume();
            }
            return;
        }
        
        console.log('[GROK-WIDGET] 🎧 Creating AudioContext...');
        
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        STATE.audioContext = new AudioContextClass({
            sampleRate: CONFIG.audio.playbackSampleRate,
            latencyHint: 'interactive'
        });
        
        const actualRate = STATE.audioContext.sampleRate;
        CONFIG.audio.actualSampleRate = actualRate;
        
        if (actualRate !== CONFIG.audio.outputSampleRate) {
            CONFIG.audio.needsResampling = true;
        }
        
        await loadAudioWorklets();
        
        // v1.1: Pre-initialize audio stream node
        await initAudioStreamNode();
    }

    async function loadAudioWorklets() {
        try {
            const recorderBlob = new Blob([RECORDER_WORKLET_CODE], { type: 'application/javascript' });
            const recorderUrl = URL.createObjectURL(recorderBlob);
            await STATE.audioContext.audioWorklet.addModule(recorderUrl);
            STATE.audioWorkletReady = true;
            URL.revokeObjectURL(recorderUrl);
            
            const streamBlob = new Blob([STREAM_WORKLET_CODE], { type: 'application/javascript' });
            const streamUrl = URL.createObjectURL(streamBlob);
            await STATE.audioContext.audioWorklet.addModule(streamUrl);
            STATE.streamWorkletReady = true;
            URL.revokeObjectURL(streamUrl);
            
            console.log('[GROK-WIDGET] ✅ AudioWorklets loaded');
        } catch (error) {
            console.error('[GROK-WIDGET] ❌ AudioWorklet load failed:', error);
        }
    }
    
    // v1.1: Pre-initialize audio stream node to avoid race condition
    async function initAudioStreamNode() {
        if (STATE.audioStreamInitialized || !STATE.streamWorkletReady) {
            return;
        }
        
        try {
            console.log('[GROK-WIDGET] 🔊 Pre-initializing AudioStreamNode...');
            
            STATE.audioStreamNode = new AudioWorkletNode(STATE.audioContext, 'audio-stream-processor');
            STATE.audioStreamNode.connect(STATE.audioContext.destination);
            
            STATE.audioStreamNode.port.onmessage = (event) => {
                if (event.data.type === 'started') {
                    console.log('[GROK-WIDGET] 🔊 AudioStream started playing');
                } else if (event.data.type === 'stats') {
                    // Optional: log stats for debugging
                }
            };
            
            STATE.audioStreamInitialized = true;
            console.log('[GROK-WIDGET] ✅ AudioStreamNode pre-initialized');
            
        } catch (error) {
            console.error('[GROK-WIDGET] ❌ Failed to pre-initialize AudioStreamNode:', error);
        }
    }

    // ============================================================================
    // UI CREATION - VOICYFY DESIGN
    // ============================================================================

    function getWidgetPositionStyles() {
        const parts = CONFIG.position.toLowerCase().split('-');
        let vertical = 'bottom';
        let horizontal = 'right';
        
        if (parts.includes('top')) vertical = 'top';
        if (parts.includes('left')) horizontal = 'left';
        
        return `
            ${vertical}: 20px;
            ${horizontal}: 20px;
        `;
    }

    function createStyles() {
        const styleEl = document.createElement('style');
        styleEl.id = 'grok-widget-styles';
        styleEl.textContent = `
            .grok-widget-container {
                position: fixed;
                ${getWidgetPositionStyles()}
                z-index: 2147483647;
                transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                font-family: 'Segoe UI', 'Roboto', sans-serif;
            }
            
            .grok-widget-button {
                width: 60px;
                height: 60px;
                border-radius: 50%;
                background: linear-gradient(135deg, #2563eb, #1e40af);
                box-shadow: 0 8px 32px rgba(37, 99, 235, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.1);
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                transition: all 0.3s ease;
                position: relative;
                overflow: hidden;
                z-index: 2147483647;
                border: none;
                outline: none;
            }
            
            .grok-widget-button:hover {
                transform: scale(1.05);
                box-shadow: 0 10px 30px rgba(37, 99, 235, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.15);
            }
            
            .grok-button-inner {
                position: relative;
                width: 40px;
                height: 40px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .grok-pulse-ring {
                position: absolute;
                width: 100%;
                height: 100%;
                border-radius: 50%;
                animation: grok-pulse-ring 3s ease-out infinite;
                background: radial-gradient(rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0) 70%);
                opacity: 0;
            }
            
            @keyframes grok-pulse-ring {
                0% { transform: scale(0.5); opacity: 0; }
                25% { opacity: 0.4; }
                100% { transform: scale(1.2); opacity: 0; }
            }
            
            .grok-audio-bars-mini {
                display: flex;
                align-items: center;
                height: 26px;
                gap: 4px;
                justify-content: center;
            }
            
            .grok-audio-bar-mini {
                width: 3px;
                height: 12px;
                background-color: #ffffff;
                border-radius: 1.5px;
                animation: grok-eq-animation 1.2s ease-in-out infinite;
                opacity: 0.9;
            }
            
            .grok-audio-bar-mini:nth-child(1) { animation-delay: 0.0s; height: 7px; }
            .grok-audio-bar-mini:nth-child(2) { animation-delay: 0.3s; height: 12px; }
            .grok-audio-bar-mini:nth-child(3) { animation-delay: 0.1s; height: 18px; }
            .grok-audio-bar-mini:nth-child(4) { animation-delay: 0.5s; height: 9px; }
            
            @keyframes grok-eq-animation {
                0% { height: 5px; }
                50% { height: 18px; }
                100% { height: 5px; }
            }
            
            .grok-widget-expanded {
                position: absolute;
                bottom: 0;
                right: 0;
                width: 320px;
                height: 0;
                opacity: 0;
                pointer-events: none;
                background: rgba(255, 255, 255, 0.95);
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
                border-radius: 20px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.05);
                overflow: hidden;
                transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                display: flex;
                flex-direction: column;
                z-index: 2147483646;
            }
            
            .grok-widget-container.position-top-left .grok-widget-expanded,
            .grok-widget-container.position-top-right .grok-widget-expanded {
                top: 0;
                bottom: auto;
            }
            
            .grok-widget-container.active .grok-widget-expanded {
                height: 460px;
                opacity: 1;
                pointer-events: all;
            }
            
            .grok-widget-container.active .grok-widget-button {
                transform: scale(0.9);
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
            }
            
            .grok-widget-header {
                padding: 15px 20px;
                background: linear-gradient(135deg, #1e3a8a, #3b82f6);
                color: white;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-radius: 20px 20px 0 0;
            }
            
            .grok-widget-title {
                font-weight: 600;
                font-size: 16px;
                letter-spacing: 0.3px;
            }
            
            .grok-widget-close {
                background: none;
                border: none;
                color: white;
                font-size: 18px;
                cursor: pointer;
                opacity: 0.8;
                transition: all 0.2s;
            }
            
            .grok-widget-close:hover {
                opacity: 1;
                transform: scale(1.1);
            }
            
            .grok-widget-content {
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
            
            .grok-main-circle {
                width: 180px;
                height: 180px;
                border-radius: 50%;
                background: linear-gradient(135deg, #f3f4f6, #e5e7eb);
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1), inset 0 2px 5px rgba(255, 255, 255, 0.5);
                position: relative;
                overflow: hidden;
                transition: all 0.3s ease;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .grok-main-circle::before {
                content: '';
                position: absolute;
                width: 140%;
                height: 140%;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.3), rgba(37, 99, 235, 0.2));
                animation: grok-wave 8s linear infinite;
                border-radius: 40%;
            }
            
            @keyframes grok-wave {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            
            .grok-main-circle.listening {
                background: linear-gradient(135deg, #dbeafe, #eff6ff);
                box-shadow: 0 0 30px rgba(37, 99, 235, 0.5), inset 0 2px 5px rgba(255, 255, 255, 0.5);
            }
            
            .grok-main-circle.listening::before {
                animation: grok-wave 4s linear infinite;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.5), rgba(37, 99, 235, 0.3));
            }
            
            .grok-main-circle.listening::after {
                content: '';
                position: absolute;
                width: 100%;
                height: 100%;
                border-radius: 50%;
                border: 3px solid rgba(37, 99, 235, 0.5);
                animation: grok-pulse 1.5s ease-out infinite;
            }
            
            @keyframes grok-pulse {
                0% { transform: scale(0.95); opacity: 0.7; }
                50% { transform: scale(1.05); opacity: 0.3; }
                100% { transform: scale(0.95); opacity: 0.7; }
            }
            
            .grok-main-circle.speaking {
                background: linear-gradient(135deg, #dcfce7, #ecfdf5);
                box-shadow: 0 0 30px rgba(5, 150, 105, 0.5), inset 0 2px 5px rgba(255, 255, 255, 0.5);
            }
            
            .grok-main-circle.speaking::before {
                animation: grok-wave 3s linear infinite;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.5), rgba(5, 150, 105, 0.3));
            }
            
            .grok-main-circle.speaking::after {
                content: '';
                position: absolute;
                width: 100%;
                height: 100%;
                background: radial-gradient(circle, transparent 50%, rgba(5, 150, 105, 0.1) 100%);
                border-radius: 50%;
                animation: grok-ripple 2s ease-out infinite;
            }
            
            @keyframes grok-ripple {
                0% { transform: scale(0.8); opacity: 0; }
                50% { opacity: 0.5; }
                100% { transform: scale(1.2); opacity: 0; }
            }
            
            .grok-main-circle.interrupted {
                background: linear-gradient(135deg, #fef3c7, #fffbeb);
                box-shadow: 0 0 30px rgba(217, 119, 6, 0.5), inset 0 2px 5px rgba(255, 255, 255, 0.5);
            }
            
            .grok-mic-icon {
                color: #3b82f6;
                font-size: 32px;
                z-index: 10;
                transition: color 0.3s ease;
            }
            
            .grok-main-circle.listening .grok-mic-icon { color: #2563eb; }
            .grok-main-circle.speaking .grok-mic-icon { color: #059669; }
            .grok-main-circle.interrupted .grok-mic-icon { color: #d97706; }
            
            .grok-audio-visualization {
                position: absolute;
                width: 100%;
                max-width: 160px;
                height: 30px;
                bottom: -5px;
                opacity: 0.8;
                pointer-events: none;
            }
            
            .grok-audio-bars {
                display: flex;
                align-items: flex-end;
                height: 30px;
                gap: 2px;
                width: 100%;
                justify-content: center;
            }
            
            .grok-audio-bar {
                width: 3px;
                height: 2px;
                background-color: #3b82f6;
                border-radius: 1px;
                transition: height 0.1s ease;
            }
            
            .grok-loader-modal {
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background-color: rgba(255, 255, 255, 0.85);
                backdrop-filter: blur(5px);
                -webkit-backdrop-filter: blur(5px);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 2147483646;
                opacity: 0;
                visibility: hidden;
                transition: all 0.3s;
                border-radius: 20px;
            }
            
            .grok-loader-modal.active {
                opacity: 1;
                visibility: visible;
            }
            
            .grok-loader {
                width: 40px;
                height: 40px;
                border: 3px solid rgba(59, 130, 246, 0.2);
                border-radius: 50%;
                border-top-color: #3b82f6;
                animation: grok-spin 1s linear infinite;
            }
            
            @keyframes grok-spin {
                to { transform: rotate(360deg); }
            }
            
            .grok-message-display {
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
                display: none;
            }
            
            .grok-connection-error {
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
                z-index: 20;
                position: relative;
            }
            
            .grok-connection-error.visible { display: block; }
            
            .grok-retry-button {
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
            
            .grok-status-indicator {
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
            
            .grok-status-indicator.show { opacity: 0.8; }
            
            .grok-status-dot {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background-color: #10b981;
            }
            
            .grok-status-dot.disconnected { background-color: #ef4444; }
            .grok-status-dot.connecting { background-color: #f59e0b; }
            .grok-status-dot.interrupted { background-color: #d97706; }
            
            .grok-voicyfy-container {
                position: absolute;
                bottom: 10px;
                left: 50%;
                transform: translateX(-50%);
                text-align: center;
                padding: 8px;
                opacity: 0.8;
                transition: opacity 0.2s ease;
            }
            
            .grok-voicyfy-container:hover { opacity: 1; }
            
            .grok-voicyfy-link {
                display: inline-block;
                text-decoration: none;
                transition: transform 0.2s ease;
            }
            
            .grok-voicyfy-link:hover { transform: translateY(-2px); }
            
            .grok-voicyfy-link img {
                height: 25px;
                width: auto;
                display: block;
            }
            
            @keyframes grok-button-pulse {
                0% { box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(37, 99, 235, 0); }
                100% { box-shadow: 0 0 0 0 rgba(37, 99, 235, 0); }
            }
            
            .grok-pulse-animation { animation: grok-button-pulse 2s infinite; }
        `;
        document.head.appendChild(styleEl);
    }

    function createWidgetHTML() {
        const widgetContainer = document.createElement('div');
        widgetContainer.className = 'grok-widget-container';
        widgetContainer.id = 'grok-widget-container';
        
        if (CONFIG.position) {
            const parts = CONFIG.position.toLowerCase().split('-');
            if (parts.includes('top')) widgetContainer.classList.add('position-top');
            if (parts.includes('left')) widgetContainer.classList.add('position-left');
            if (parts.includes('right')) widgetContainer.classList.add('position-right');
        }

        const widgetHTML = `
            <!-- Button (Minimized) -->
            <div class="grok-widget-button" id="grok-widget-button">
                <div class="grok-button-inner">
                    <div class="grok-pulse-ring"></div>
                    <div class="grok-audio-bars-mini">
                        <div class="grok-audio-bar-mini"></div>
                        <div class="grok-audio-bar-mini"></div>
                        <div class="grok-audio-bar-mini"></div>
                        <div class="grok-audio-bar-mini"></div>
                    </div>
                </div>
            </div>
            
            <!-- Expanded Widget -->
            <div class="grok-widget-expanded" id="grok-widget-expanded">
                <div class="grok-widget-header">
                    <div class="grok-widget-title">Голосовой Ассистент</div>
                    <button class="grok-widget-close" id="grok-widget-close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="grok-widget-content">
                    <!-- Main Circle -->
                    <div class="grok-main-circle" id="grok-main-circle">
                        <i class="fas fa-microphone grok-mic-icon"></i>
                        
                        <!-- Audio Visualization -->
                        <div class="grok-audio-visualization" id="grok-audio-visualization">
                            <div class="grok-audio-bars" id="grok-audio-bars"></div>
                        </div>
                    </div>
                    
                    <!-- Connection Error -->
                    <div class="grok-connection-error" id="grok-connection-error">
                        Ошибка соединения
                        <button class="grok-retry-button" id="grok-retry-button">Повторить</button>
                    </div>
                    
                    <!-- Status Indicator -->
                    <div class="grok-status-indicator" id="grok-status-indicator">
                        <div class="grok-status-dot" id="grok-status-dot"></div>
                        <span id="grok-status-text">Подключение...</span>
                    </div>
                    
                    <!-- Voicyfy Branding -->
                    <div class="grok-voicyfy-container">
                        <a href="https://voicyfy.ru/" target="_blank" rel="noopener noreferrer" class="grok-voicyfy-link">
                            <img src="https://i.ibb.co/ccw6sjdk/photo-2025-06-03-05-04-02.jpg" alt="Voicyfy - powered by AI">
                        </a>
                    </div>
                </div>
            </div>
            
            <!-- Loader Modal -->
            <div id="grok-loader-modal" class="grok-loader-modal active">
                <div class="grok-loader"></div>
            </div>
        `;

        widgetContainer.innerHTML = widgetHTML;
        document.body.appendChild(widgetContainer);
        
        const audioBars = document.getElementById('grok-audio-bars');
        if (audioBars) {
            for (let i = 0; i < 20; i++) {
                const bar = document.createElement('div');
                bar.className = 'grok-audio-bar';
                audioBars.appendChild(bar);
            }
        }
    }

    function cacheUIElements() {
        STATE.ui = {
            container: document.getElementById('grok-widget-container'),
            button: document.getElementById('grok-widget-button'),
            expanded: document.getElementById('grok-widget-expanded'),
            closeBtn: document.getElementById('grok-widget-close'),
            mainCircle: document.getElementById('grok-main-circle'),
            audioBars: document.querySelectorAll('.grok-audio-bar'),
            loader: document.getElementById('grok-loader-modal'),
            statusDot: document.getElementById('grok-status-dot'),
            statusText: document.getElementById('grok-status-text'),
            statusIndicator: document.getElementById('grok-status-indicator'),
            errorMsg: document.getElementById('grok-connection-error'),
            retryBtn: document.getElementById('grok-retry-button')
        };

        STATE.ui.button.addEventListener('click', handleWidgetOpen);
        STATE.ui.closeBtn.addEventListener('click', handleWidgetClose);
        STATE.ui.retryBtn.addEventListener('click', () => connectWebSocket());
        
        STATE.ui.button.style.opacity = '1';
        STATE.ui.button.style.visibility = 'visible';
        STATE.ui.button.classList.add('grok-pulse-animation');
    }

    // ============================================================================
    // UI STATE UPDATES
    // ============================================================================

    function updateUIState(state, message = '') {
        const ui = STATE.ui;
        if (!ui.mainCircle) return;

        ui.mainCircle.classList.remove('listening', 'speaking', 'interrupted');
        ui.statusDot.classList.remove('connected', 'disconnected', 'connecting', 'interrupted');
        ui.button.classList.remove('grok-pulse-animation');

        if (message) {
            ui.statusText.textContent = message;
            ui.statusIndicator.classList.add('show');
            setTimeout(() => ui.statusIndicator.classList.remove('show'), 3000);
        }

        switch (state) {
            case 'connecting':
                ui.statusDot.classList.add('connecting');
                ui.loader.classList.add('active');
                break;
                
            case 'connected':
                ui.statusDot.classList.add('connected');
                ui.loader.classList.remove('active');
                ui.errorMsg.classList.remove('visible');
                break;
                
            case 'recording':
                ui.mainCircle.classList.add('listening');
                ui.statusDot.classList.add('connected');
                break;
                
            case 'playing':
                ui.mainCircle.classList.add('speaking');
                ui.statusDot.classList.add('connected');
                break;
                
            case 'interrupted':
                ui.mainCircle.classList.add('interrupted');
                ui.statusDot.classList.add('interrupted');
                break;
                
            case 'error':
                ui.statusDot.classList.add('disconnected');
                ui.errorMsg.classList.add('visible');
                ui.loader.classList.remove('active');
                break;
                
            case 'disconnected':
                ui.statusDot.classList.add('disconnected');
                break;
        }
    }

    function updateAudioVisualization(audioData) {
        if (!STATE.ui.audioBars || !STATE.ui.audioBars.length) return;
        
        const bars = STATE.ui.audioBars;
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
            const height = 2 + Math.min(28, Math.floor(average * 150));
            bars[i].style.height = `${height}px`;
        }
    }

    function resetAudioVisualization() {
        if (!STATE.ui.audioBars) return;
        STATE.ui.audioBars.forEach(bar => {
            bar.style.height = '2px';
        });
    }

    // ============================================================================
    // WIDGET LOGIC
    // ============================================================================

    async function handleWidgetOpen() {
        STATE.ui.container.classList.add('active');
        STATE.isWidgetOpen = true;
        STATE.ui.button.classList.remove('grok-pulse-animation');
        
        if (!STATE.audioContext) {
            await initAudioContext();
        }

        if (!STATE.isConnected && !STATE.ws) {
            connectWebSocket();
        } else if (STATE.isConnected && !STATE.isRecording && STATE.readyToRecord) {
            startRecording();
        }
    }

    async function handleWidgetClose() {
        STATE.ui.container.classList.remove('active');
        STATE.isWidgetOpen = false;
        STATE.ui.button.classList.add('grok-pulse-animation');

        if (STATE.isRecording) await stopRecording();
        if (STATE.isPlaying) stopPlayback();
        
        if (STATE.ws) {
            STATE.ws.close();
            STATE.ws = null;
        }
        
        STATE.isConnected = false;
        STATE.readyToRecord = false;
        
        if (STATE.pingInterval) {
            clearInterval(STATE.pingInterval);
            STATE.pingInterval = null;
        }
    }

    // ============================================================================
    // WEBSOCKET & GROK LOGIC
    // ============================================================================

    async function connectWebSocket() {
        updateUIState('connecting', 'Подключение...');
        
        const wsUrl = CONFIG.serverUrl.replace('http://', 'ws://').replace('https://', 'wss://');
        const endpoint = `${wsUrl}/ws/grok/${CONFIG.assistantId}`;
        
        try {
            STATE.ws = new WebSocket(endpoint);
            STATE.ws.binaryType = 'arraybuffer';
            
            STATE.ws.onopen = () => {
                console.log('[GROK-WIDGET] ✅ WebSocket connected');
                STATE.isConnected = true;
                STATE.reconnectAttempts = 0;
                
                updateUIState('connected', 'Соединение установлено');
                
                STATE.pingInterval = setInterval(() => {
                    if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
                        STATE.ws.send(JSON.stringify({ type: 'ping' }));
                    }
                }, CONFIG.ws.pingInterval);
                
                STATE.setupTimeout = setTimeout(() => {
                    if (!STATE.isSetupComplete) {
                        STATE.isSetupComplete = true;
                        STATE.readyToRecord = true;
                        if (STATE.isWidgetOpen) startRecording();
                    }
                }, CONFIG.setup.maxSetupWait);
            };
            
            STATE.ws.onmessage = handleWSMessage;
            
            STATE.ws.onerror = (error) => {
                console.error('[GROK-WIDGET] WS Error:', error);
                updateUIState('error', 'Ошибка сервера');
            };
            
            STATE.ws.onclose = (event) => {
                console.log('[GROK-WIDGET] WS Closed:', event.code);
                STATE.isConnected = false;
                STATE.readyToRecord = false;
                stopPlayback();
                if (STATE.isRecording) stopRecording();
                
                updateUIState('disconnected', 'Отключено');
                
                if (STATE.isWidgetOpen && STATE.reconnectAttempts < CONFIG.ws.maxReconnectAttempts) {
                    STATE.reconnectAttempts++;
                    setTimeout(connectWebSocket, CONFIG.ws.reconnectDelay);
                }
            };
            
        } catch (e) {
            console.error(e);
            updateUIState('error', 'Ошибка сети');
        }
    }

    function handleWSMessage(event) {
        try {
            if (event.data instanceof ArrayBuffer || event.data instanceof Blob) return;

            const data = JSON.parse(event.data);
            
            switch (data.type) {
                case 'grok.setup.complete':
                case 'session.updated':
                    STATE.isSetupComplete = true;
                    clearTimeout(STATE.setupTimeout);
                    updateUIState('connected', 'Настройка завершена');
                    setTimeout(() => {
                        STATE.readyToRecord = true;
                        if (STATE.isWidgetOpen) startRecording();
                    }, CONFIG.setup.waitAfterSetup);
                    break;
                    
                case 'response.audio.delta':
                    handleAudioDelta(data);
                    break;
                    
                case 'assistant.speech.started':
                    STATE.isSpeaking = true;
                    updateUIState('playing', 'Ассистент говорит');
                    break;
                    
                case 'assistant.speech.ended':
                    STATE.isSpeaking = false;
                    // Don't stop playback immediately - let queue finish
                    if (STATE.isRecording) updateUIState('recording', 'Слушаю...');
                    break;
                    
                case 'conversation.interrupted':
                    STATE.isSpeaking = false;
                    stopPlayback();
                    updateUIState('interrupted', 'Прервано');
                    setTimeout(() => {
                        if (STATE.isRecording) updateUIState('recording', 'Слушаю...');
                    }, 800);
                    break;
                    
                case 'error':
                    console.error('[GROK] Error:', data.error);
                    updateUIState('error', data.error.message || 'Ошибка');
                    break;
            }
        } catch (e) {
            console.error('[GROK-WIDGET] Parse error:', e);
        }
    }

    // ============================================================================
    // AUDIO HANDLING - v1.1 FIXED
    // ============================================================================

    function handleAudioDelta(data) {
        if (!data.delta) return;
        
        try {
            const binaryString = atob(data.delta);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            
            const pcm16 = new Int16Array(bytes.buffer);
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++) {
                float32[i] = pcm16[i] / 32768.0;
            }
            
            let audioData = float32;
            if (CONFIG.audio.needsResampling) {
                audioData = resampleAudio(float32, CONFIG.audio.outputSampleRate, CONFIG.audio.actualSampleRate);
            }
            
            // v1.1 FIX: Check if AudioStreamNode is ready
            if (STATE.audioStreamNode && STATE.audioStreamInitialized) {
                // Send directly to worklet
                STATE.audioStreamNode.port.postMessage({ type: 'audioData', buffer: audioData });
                
                if (!STATE.isPlaying) {
                    STATE.isPlaying = true;
                    updateUIState('playing', 'Ассистент говорит');
                }
            } else {
                // v1.1 FIX: Buffer audio until stream is ready
                console.log('[GROK-WIDGET] ⏳ Buffering audio chunk (stream not ready yet)');
                STATE.pendingAudioQueue.push(audioData);
                
                // Try to initialize stream and flush buffer
                initAudioStreamNode().then(() => {
                    flushPendingAudioQueue();
                });
            }
            
        } catch (error) {
            console.error('[GROK-WIDGET] Audio decode error:', error);
        }
    }
    
    // v1.1: Flush pending audio queue after stream is initialized
    function flushPendingAudioQueue() {
        if (!STATE.audioStreamNode || !STATE.audioStreamInitialized) {
            return;
        }
        
        if (STATE.pendingAudioQueue.length > 0) {
            console.log(`[GROK-WIDGET] 🔊 Flushing ${STATE.pendingAudioQueue.length} buffered audio chunks`);
            
            while (STATE.pendingAudioQueue.length > 0) {
                const audioData = STATE.pendingAudioQueue.shift();
                STATE.audioStreamNode.port.postMessage({ type: 'audioData', buffer: audioData });
            }
            
            if (!STATE.isPlaying) {
                STATE.isPlaying = true;
                updateUIState('playing', 'Ассистент говорит');
            }
        }
    }

    async function startRecording() {
        if (STATE.isRecording) return;
        console.log('[GROK-WIDGET] 🎙️ Starting recording...');
        
        try {
            STATE.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: CONFIG.audio.inputSampleRate,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            
            const source = STATE.audioContext.createMediaStreamSource(STATE.mediaStream);
            
            const workletNode = new AudioWorkletNode(STATE.audioContext, 'recorder-worklet');
            
            workletNode.port.onmessage = (event) => {
                if (!STATE.isRecording) return;
                
                const audioData = event.data.data;
                const pcmData = float32ToPCM16(audioData);
                
                updateAudioVisualization(audioData);
                
                const rms = calculateRMS(audioData);
                const db = 20 * Math.log10(rms);
                
                if (db > CONFIG.vad.speechThreshold) {
                    if (!STATE.isSpeaking) {
                        STATE.ui.mainCircle.classList.add('listening'); 
                    }
                }
                
                if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
                    const base64Audio = arrayBufferToBase64(pcmData.buffer);
                    STATE.ws.send(JSON.stringify({
                        type: 'input_audio_buffer.append',
                        audio: base64Audio
                    }));
                }
            };
            
            source.connect(workletNode);
            workletNode.connect(STATE.audioContext.destination);
            
            STATE.audioWorkletNode = { source, workletNode };
            STATE.isRecording = true;
            
            updateUIState('recording', 'Слушаю...');
            
        } catch (error) {
            console.error('Mic Error:', error);
            updateUIState('error', 'Нет доступа к микрофону');
        }
    }

    async function stopRecording() {
        if (!STATE.isRecording) return;
        STATE.isRecording = false;
        
        if (STATE.mediaStream) {
            STATE.mediaStream.getTracks().forEach(track => track.stop());
            STATE.mediaStream = null;
        }
        
        if (STATE.audioWorkletNode) {
            STATE.audioWorkletNode.source.disconnect();
            STATE.audioWorkletNode.workletNode.disconnect();
            STATE.audioWorkletNode = null;
        }
        
        resetAudioVisualization();
        console.log('[GROK-WIDGET] 🛑 Recording stopped');
    }

    function stopPlayback() {
        if (!STATE.isPlaying) return;
        
        if (STATE.audioStreamNode) {
            STATE.audioStreamNode.port.postMessage({ type: 'clear' });
        }
        
        // v1.1: Clear pending queue too
        STATE.pendingAudioQueue = [];
        
        STATE.isPlaying = false;
        resetAudioVisualization();
    }

    // ============================================================================
    // UTILS
    // ============================================================================

    function resampleAudio(inputBuffer, inputSampleRate, outputSampleRate) {
        if (inputSampleRate === outputSampleRate) return inputBuffer;
        const ratio = inputSampleRate / outputSampleRate;
        const outputLength = Math.round(inputBuffer.length / ratio);
        const outputBuffer = new Float32Array(outputLength);
        for (let i = 0; i < outputLength; i++) {
            const srcIndex = i * ratio;
            const srcIndexFloor = Math.floor(srcIndex);
            const srcIndexCeil = Math.min(srcIndexFloor + 1, inputBuffer.length - 1);
            const t = srcIndex - srcIndexFloor;
            outputBuffer[i] = inputBuffer[srcIndexFloor] * (1 - t) + inputBuffer[srcIndexCeil] * t;
        }
        return outputBuffer;
    }

    function float32ToPCM16(float32Array) {
        const pcm16 = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return pcm16;
    }

    function calculateRMS(float32Array) {
        let sum = 0;
        for (let i = 0; i < float32Array.length; i++) {
            sum += float32Array[i] * float32Array[i];
        }
        return Math.sqrt(sum / float32Array.length);
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
    // STARTUP
    // ============================================================================

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
