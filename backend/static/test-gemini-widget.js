/**
 * 🚀 Gemini Voice Widget v2.9.0-TEST - WITH SCREEN CAPTURE
 * Google Gemini Live API Integration with Automatic Screenshots
 * 
 * ✅ UI: 100% Match with OpenAI Widget (Blue/Clean/Voicyfy)
 * ✅ Logic: Pure Gemini VAD (Server-side decision making)
 * ✅ Continuous audio streaming via AudioWorklet
 * ✅ Instant interruptions & Zero-latency playback
 * ✅ NEW: Automatic screen capture every 3 seconds
 * 
 * @version 2.9.0-TEST
 * @author WellcomeAI Team
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
        },
        
        // ✅ NEW: Screen capture settings
        screenCapture: {
            enabled: false,
            intervalMs: 3000,        // Every 3 seconds
            quality: 0.7,            // JPEG quality (0.1 - 1.0)
            maxWidth: 1280,          // Max screenshot width
            maxHeight: 720,          // Max screenshot height
            silent: true             // Don't trigger immediate response
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
        
        // ✅ NEW: Screen capture state
        screenCaptureStream: null,
        screenCaptureInterval: null,
        isScreenSharing: false,
        screenshotCount: 0,
        lastScreenshotTime: 0,
        
        // UI Elements references
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
        console.log('[GEMINI-WIDGET] 🚀 Initializing v2.9.0-TEST (Screen Capture Edition)...');
        
        const getScriptTag = () => {
            const scripts = document.querySelectorAll('script');
            for (let i = 0; i < scripts.length; i++) {
                if (scripts[i].hasAttribute('data-assistantId') || scripts[i].dataset.assistantId) return scripts[i];
            }
            return document.currentScript;
        };

        const scriptTag = getScriptTag();
        
        if (!scriptTag) {
            console.error('[GEMINI-WIDGET] Script tag with data-assistantId not found');
            return;
        }

        CONFIG.assistantId = scriptTag.getAttribute('data-assistantId') || scriptTag.dataset.assistantId;
        CONFIG.serverUrl = scriptTag.getAttribute('data-server') || scriptTag.dataset.server;
        
        const posAttr = scriptTag.getAttribute('data-position') || scriptTag.dataset.position;
        if (posAttr) CONFIG.position = posAttr;

        if (!CONFIG.assistantId || !CONFIG.serverUrl) {
            console.error('[GEMINI-WIDGET] Missing required parameters (assistantId or server)');
            return;
        }
        
        // Load FontAwesome
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
        
        console.log('[GEMINI-WIDGET] ✅ Initialization complete');
        console.log('[GEMINI-WIDGET] 📸 Screen capture feature: AVAILABLE');
    }

    async function initAudioContext() {
        if (STATE.audioContext) {
            if (STATE.audioContext.state === 'suspended') {
                await STATE.audioContext.resume();
            }
            return;
        }
        
        console.log('[GEMINI-WIDGET] 🎧 Creating AudioContext...');
        
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
            
            console.log('[GEMINI-WIDGET] ✅ AudioWorklets loaded');
        } catch (error) {
            console.error('[GEMINI-WIDGET] ❌ AudioWorklet load failed:', error);
        }
    }

    // ============================================================================
    // UI CREATION - VOICYFY DESIGN + SCREEN CAPTURE BUTTON
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
        styleEl.id = 'wellcomeai-widget-styles';
        styleEl.textContent = `
            .wellcomeai-widget-container {
                position: fixed;
                ${getWidgetPositionStyles()}
                z-index: 2147483647;
                transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                font-family: 'Segoe UI', 'Roboto', sans-serif;
            }
            
            .wellcomeai-widget-button {
                width: 60px;
                height: 60px;
                border-radius: 50%;
                background: linear-gradient(135deg, #4a86e8, #2b59c3);
                box-shadow: 0 8px 32px rgba(74, 134, 232, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.1);
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
            
            .wellcomeai-widget-button:hover {
                transform: scale(1.05);
                box-shadow: 0 10px 30px rgba(74, 134, 232, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.15);
            }
            
            .wellcomeai-button-inner {
                position: relative;
                width: 40px;
                height: 40px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .wellcomeai-pulse-ring {
                position: absolute;
                width: 100%;
                height: 100%;
                border-radius: 50%;
                animation: wellcomeai-pulse-ring 3s ease-out infinite;
                background: radial-gradient(rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0) 70%);
                opacity: 0;
            }
            
            @keyframes wellcomeai-pulse-ring {
                0% { transform: scale(0.5); opacity: 0; }
                25% { opacity: 0.4; }
                100% { transform: scale(1.2); opacity: 0; }
            }
            
            .wellcomeai-audio-bars-mini {
                display: flex;
                align-items: center;
                height: 26px;
                gap: 4px;
                justify-content: center;
            }
            
            .wellcomeai-audio-bar-mini {
                width: 3px;
                height: 12px;
                background-color: #ffffff;
                border-radius: 1.5px;
                animation: wellcomeai-eq-animation 1.2s ease-in-out infinite;
                opacity: 0.9;
            }
            
            .wellcomeai-audio-bar-mini:nth-child(1) { animation-delay: 0.0s; height: 7px; }
            .wellcomeai-audio-bar-mini:nth-child(2) { animation-delay: 0.3s; height: 12px; }
            .wellcomeai-audio-bar-mini:nth-child(3) { animation-delay: 0.1s; height: 18px; }
            .wellcomeai-audio-bar-mini:nth-child(4) { animation-delay: 0.5s; height: 9px; }
            
            @keyframes wellcomeai-eq-animation {
                0% { height: 5px; }
                50% { height: 18px; }
                100% { height: 5px; }
            }
            
            .wellcomeai-widget-expanded {
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
            
            .wellcomeai-widget-container.position-top-left .wellcomeai-widget-expanded,
            .wellcomeai-widget-container.position-top-right .wellcomeai-widget-expanded {
                top: 0;
                bottom: auto;
            }
            
            .wellcomeai-widget-container.active .wellcomeai-widget-expanded {
                height: 500px;
                opacity: 1;
                pointer-events: all;
            }
            
            .wellcomeai-widget-container.active .wellcomeai-widget-button {
                transform: scale(0.9);
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
            }
            
            .wellcomeai-widget-header {
                padding: 15px 20px;
                background: linear-gradient(135deg, #1e3a8a, #3b82f6);
                color: white;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-radius: 20px 20px 0 0;
            }
            
            .wellcomeai-widget-title {
                font-weight: 600;
                font-size: 16px;
                letter-spacing: 0.3px;
            }
            
            .wellcomeai-widget-close {
                background: none;
                border: none;
                color: white;
                font-size: 18px;
                cursor: pointer;
                opacity: 0.8;
                transition: all 0.2s;
            }
            
            .wellcomeai-widget-close:hover {
                opacity: 1;
                transform: scale(1.1);
            }
            
            .wellcomeai-widget-content {
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
            
            .wellcomeai-main-circle {
                width: 160px;
                height: 160px;
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
            
            .wellcomeai-main-circle::before {
                content: '';
                position: absolute;
                width: 140%;
                height: 140%;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.3), rgba(74, 134, 232, 0.2));
                animation: wellcomeai-wave 8s linear infinite;
                border-radius: 40%;
            }
            
            @keyframes wellcomeai-wave {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            
            .wellcomeai-main-circle.listening {
                background: linear-gradient(135deg, #dbeafe, #eff6ff);
                box-shadow: 0 0 30px rgba(37, 99, 235, 0.5), inset 0 2px 5px rgba(255, 255, 255, 0.5);
            }
            
            .wellcomeai-main-circle.listening::before {
                animation: wellcomeai-wave 4s linear infinite;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.5), rgba(37, 99, 235, 0.3));
            }
            
            .wellcomeai-main-circle.listening::after {
                content: '';
                position: absolute;
                width: 100%;
                height: 100%;
                border-radius: 50%;
                border: 3px solid rgba(37, 99, 235, 0.5);
                animation: wellcomeai-pulse 1.5s ease-out infinite;
            }
            
            @keyframes wellcomeai-pulse {
                0% { transform: scale(0.95); opacity: 0.7; }
                50% { transform: scale(1.05); opacity: 0.3; }
                100% { transform: scale(0.95); opacity: 0.7; }
            }
            
            .wellcomeai-main-circle.speaking {
                background: linear-gradient(135deg, #dcfce7, #ecfdf5);
                box-shadow: 0 0 30px rgba(5, 150, 105, 0.5), inset 0 2px 5px rgba(255, 255, 255, 0.5);
            }
            
            .wellcomeai-main-circle.speaking::before {
                animation: wellcomeai-wave 3s linear infinite;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.5), rgba(5, 150, 105, 0.3));
            }
            
            .wellcomeai-main-circle.speaking::after {
                content: '';
                position: absolute;
                width: 100%;
                height: 100%;
                background: radial-gradient(circle, transparent 50%, rgba(5, 150, 105, 0.1) 100%);
                border-radius: 50%;
                animation: wellcomeai-ripple 2s ease-out infinite;
            }
            
            @keyframes wellcomeai-ripple {
                0% { transform: scale(0.8); opacity: 0; }
                50% { opacity: 0.5; }
                100% { transform: scale(1.2); opacity: 0; }
            }
            
            .wellcomeai-main-circle.interrupted {
                background: linear-gradient(135deg, #fef3c7, #fffbeb);
                box-shadow: 0 0 30px rgba(217, 119, 6, 0.5), inset 0 2px 5px rgba(255, 255, 255, 0.5);
            }
            
            .wellcomeai-mic-icon {
                color: #3b82f6;
                font-size: 32px;
                z-index: 10;
                transition: color 0.3s ease;
            }
            
            .wellcomeai-main-circle.listening .wellcomeai-mic-icon { color: #2563eb; }
            .wellcomeai-main-circle.speaking .wellcomeai-mic-icon { color: #059669; }
            .wellcomeai-main-circle.interrupted .wellcomeai-mic-icon { color: #d97706; }
            
            .wellcomeai-audio-visualization {
                position: absolute;
                width: 100%;
                max-width: 160px;
                height: 30px;
                bottom: -5px;
                opacity: 0.8;
                pointer-events: none;
            }
            
            .wellcomeai-audio-bars {
                display: flex;
                align-items: flex-end;
                height: 30px;
                gap: 2px;
                width: 100%;
                justify-content: center;
            }
            
            .wellcomeai-audio-bar {
                width: 3px;
                height: 2px;
                background-color: #3b82f6;
                border-radius: 1px;
                transition: height 0.1s ease;
            }
            
            /* ✅ NEW: Screen Capture Button Styles */
            .wellcomeai-screen-capture-section {
                width: 100%;
                margin-top: 15px;
                padding: 10px 0;
                border-top: 1px solid rgba(0, 0, 0, 0.08);
            }
            
            .wellcomeai-screen-btn {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                width: 100%;
                padding: 12px 20px;
                border: 2px solid #e5e7eb;
                border-radius: 12px;
                background: white;
                color: #374151;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.3s ease;
            }
            
            .wellcomeai-screen-btn:hover {
                border-color: #3b82f6;
                color: #3b82f6;
                background: #eff6ff;
            }
            
            .wellcomeai-screen-btn.active {
                border-color: #10b981;
                background: linear-gradient(135deg, #d1fae5, #ecfdf5);
                color: #059669;
            }
            
            .wellcomeai-screen-btn.active:hover {
                border-color: #ef4444;
                background: #fef2f2;
                color: #ef4444;
            }
            
            .wellcomeai-screen-btn i {
                font-size: 16px;
            }
            
            .wellcomeai-screen-status {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                margin-top: 8px;
                font-size: 11px;
                color: #6b7280;
                opacity: 0;
                transition: opacity 0.3s;
            }
            
            .wellcomeai-screen-status.visible {
                opacity: 1;
            }
            
            .wellcomeai-screen-status-dot {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: #10b981;
                animation: wellcomeai-blink 1s infinite;
            }
            
            @keyframes wellcomeai-blink {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.3; }
            }
            
            .wellcomeai-screen-preview {
                width: 100%;
                height: 60px;
                margin-top: 10px;
                border-radius: 8px;
                overflow: hidden;
                background: #1f2937;
                display: none;
                position: relative;
            }
            
            .wellcomeai-screen-preview.visible {
                display: block;
            }
            
            .wellcomeai-screen-preview img {
                width: 100%;
                height: 100%;
                object-fit: contain;
            }
            
            .wellcomeai-screen-preview-label {
                position: absolute;
                bottom: 4px;
                right: 6px;
                font-size: 9px;
                color: white;
                background: rgba(0, 0, 0, 0.6);
                padding: 2px 6px;
                border-radius: 4px;
            }
            
            .wellcomeai-loader-modal {
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
            
            .wellcomeai-loader-modal.active {
                opacity: 1;
                visibility: visible;
            }
            
            .wellcomeai-loader {
                width: 40px;
                height: 40px;
                border: 3px solid rgba(59, 130, 246, 0.2);
                border-radius: 50%;
                border-top-color: #3b82f6;
                animation: wellcomeai-spin 1s linear infinite;
            }
            
            @keyframes wellcomeai-spin {
                to { transform: rotate(360deg); }
            }
            
            .wellcomeai-connection-error {
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
            
            .wellcomeai-connection-error.visible { display: block; }
            
            .wellcomeai-retry-button {
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
            
            .wellcomeai-status-indicator {
                position: absolute;
                bottom: 140px;
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
            
            .wellcomeai-status-indicator.show { opacity: 0.8; }
            
            .wellcomeai-status-dot {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background-color: #10b981;
            }
            
            .wellcomeai-status-dot.disconnected { background-color: #ef4444; }
            .wellcomeai-status-dot.connecting { background-color: #f59e0b; }
            .wellcomeai-status-dot.interrupted { background-color: #d97706; }
            
            .wellcomeai-voicyfy-container {
                position: absolute;
                bottom: 10px;
                left: 50%;
                transform: translateX(-50%);
                text-align: center;
                padding: 8px;
                opacity: 0.8;
                transition: opacity 0.2s ease;
            }
            
            .wellcomeai-voicyfy-container:hover { opacity: 1; }
            
            .wellcomeai-voicyfy-link {
                display: inline-block;
                text-decoration: none;
                transition: transform 0.2s ease;
            }
            
            .wellcomeai-voicyfy-link:hover { transform: translateY(-2px); }
            
            .wellcomeai-voicyfy-link img {
                height: 25px;
                width: auto;
                display: block;
            }
            
            @keyframes wellcomeai-button-pulse {
                0% { box-shadow: 0 0 0 0 rgba(74, 134, 232, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(74, 134, 232, 0); }
                100% { box-shadow: 0 0 0 0 rgba(74, 134, 232, 0); }
            }
            
            .wellcomeai-pulse-animation { animation: wellcomeai-button-pulse 2s infinite; }
        `;
        document.head.appendChild(styleEl);
    }

    function createWidgetHTML() {
        const widgetContainer = document.createElement('div');
        widgetContainer.className = 'wellcomeai-widget-container';
        widgetContainer.id = 'wellcomeai-widget-container';
        
        if (CONFIG.position) {
            const parts = CONFIG.position.toLowerCase().split('-');
            if (parts.includes('top')) widgetContainer.classList.add('position-top');
            if (parts.includes('left')) widgetContainer.classList.add('position-left');
            if (parts.includes('right')) widgetContainer.classList.add('position-right');
        }

        const widgetHTML = `
            <!-- Button (Minimized) -->
            <div class="wellcomeai-widget-button" id="wellcomeai-widget-button">
                <div class="wellcomeai-button-inner">
                    <div class="wellcomeai-pulse-ring"></div>
                    <div class="wellcomeai-audio-bars-mini">
                        <div class="wellcomeai-audio-bar-mini"></div>
                        <div class="wellcomeai-audio-bar-mini"></div>
                        <div class="wellcomeai-audio-bar-mini"></div>
                        <div class="wellcomeai-audio-bar-mini"></div>
                    </div>
                </div>
            </div>
            
            <!-- Expanded Widget -->
            <div class="wellcomeai-widget-expanded" id="wellcomeai-widget-expanded">
                <div class="wellcomeai-widget-header">
                    <div class="wellcomeai-widget-title">🧪 Голосовой Ассистент (TEST)</div>
                    <button class="wellcomeai-widget-close" id="wellcomeai-widget-close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="wellcomeai-widget-content">
                    <!-- Main Circle -->
                    <div class="wellcomeai-main-circle" id="wellcomeai-main-circle">
                        <i class="fas fa-microphone wellcomeai-mic-icon"></i>
                        
                        <!-- Audio Visualization -->
                        <div class="wellcomeai-audio-visualization" id="wellcomeai-audio-visualization">
                            <div class="wellcomeai-audio-bars" id="wellcomeai-audio-bars"></div>
                        </div>
                    </div>
                    
                    <!-- ✅ NEW: Screen Capture Section -->
                    <div class="wellcomeai-screen-capture-section">
                        <button class="wellcomeai-screen-btn" id="wellcomeai-screen-btn">
                            <i class="fas fa-desktop"></i>
                            <span id="wellcomeai-screen-btn-text">Поделиться экраном</span>
                        </button>
                        
                        <div class="wellcomeai-screen-status" id="wellcomeai-screen-status">
                            <div class="wellcomeai-screen-status-dot"></div>
                            <span id="wellcomeai-screen-status-text">Скриншоты: каждые 3 сек</span>
                        </div>
                        
                        <div class="wellcomeai-screen-preview" id="wellcomeai-screen-preview">
                            <img id="wellcomeai-screen-preview-img" src="" alt="Preview">
                            <div class="wellcomeai-screen-preview-label" id="wellcomeai-screen-preview-label">0 снимков</div>
                        </div>
                    </div>
                    
                    <!-- Connection Error -->
                    <div class="wellcomeai-connection-error" id="wellcomeai-connection-error">
                        Ошибка соединения
                        <button class="wellcomeai-retry-button" id="wellcomeai-retry-button">Повторить</button>
                    </div>
                    
                    <!-- Status Indicator -->
                    <div class="wellcomeai-status-indicator" id="wellcomeai-status-indicator">
                        <div class="wellcomeai-status-dot" id="wellcomeai-status-dot"></div>
                        <span id="wellcomeai-status-text">Подключение...</span>
                    </div>
                    
                    <!-- Voicyfy Branding -->
                    <div class="wellcomeai-voicyfy-container">
                        <a href="https://voicyfy.ru/" target="_blank" rel="noopener noreferrer" class="wellcomeai-voicyfy-link">
                            <img src="https://i.ibb.co/ccw6sjdk/photo-2025-06-03-05-04-02.jpg" alt="Voicyfy - powered by AI">
                        </a>
                    </div>
                </div>
            </div>
            
            <!-- Loader Modal -->
            <div id="wellcomeai-loader-modal" class="wellcomeai-loader-modal active">
                <div class="wellcomeai-loader"></div>
            </div>
        `;

        widgetContainer.innerHTML = widgetHTML;
        document.body.appendChild(widgetContainer);
        
        // Create audio bars
        const audioBars = document.getElementById('wellcomeai-audio-bars');
        if (audioBars) {
            for (let i = 0; i < 20; i++) {
                const bar = document.createElement('div');
                bar.className = 'wellcomeai-audio-bar';
                audioBars.appendChild(bar);
            }
        }
    }

    function cacheUIElements() {
        STATE.ui = {
            container: document.getElementById('wellcomeai-widget-container'),
            button: document.getElementById('wellcomeai-widget-button'),
            expanded: document.getElementById('wellcomeai-widget-expanded'),
            closeBtn: document.getElementById('wellcomeai-widget-close'),
            mainCircle: document.getElementById('wellcomeai-main-circle'),
            audioBars: document.querySelectorAll('.wellcomeai-audio-bar'),
            loader: document.getElementById('wellcomeai-loader-modal'),
            statusDot: document.getElementById('wellcomeai-status-dot'),
            statusText: document.getElementById('wellcomeai-status-text'),
            statusIndicator: document.getElementById('wellcomeai-status-indicator'),
            errorMsg: document.getElementById('wellcomeai-connection-error'),
            retryBtn: document.getElementById('wellcomeai-retry-button'),
            
            // ✅ NEW: Screen capture UI elements
            screenBtn: document.getElementById('wellcomeai-screen-btn'),
            screenBtnText: document.getElementById('wellcomeai-screen-btn-text'),
            screenStatus: document.getElementById('wellcomeai-screen-status'),
            screenStatusText: document.getElementById('wellcomeai-screen-status-text'),
            screenPreview: document.getElementById('wellcomeai-screen-preview'),
            screenPreviewImg: document.getElementById('wellcomeai-screen-preview-img'),
            screenPreviewLabel: document.getElementById('wellcomeai-screen-preview-label')
        };

        // Bind Events
        STATE.ui.button.addEventListener('click', handleWidgetOpen);
        STATE.ui.closeBtn.addEventListener('click', handleWidgetClose);
        STATE.ui.retryBtn.addEventListener('click', () => connectWebSocket());
        
        // ✅ NEW: Screen capture button event
        STATE.ui.screenBtn.addEventListener('click', handleScreenCaptureToggle);
        
        // Initial State
        STATE.ui.button.style.opacity = '1';
        STATE.ui.button.style.visibility = 'visible';
        STATE.ui.button.classList.add('wellcomeai-pulse-animation');
    }

    // ============================================================================
    // ✅ NEW: SCREEN CAPTURE FUNCTIONALITY
    // ============================================================================

    async function handleScreenCaptureToggle() {
        if (STATE.isScreenSharing) {
            await stopScreenCapture();
        } else {
            await startScreenCapture();
        }
    }

    async function startScreenCapture() {
        console.log('[GEMINI-WIDGET] 📸 Starting screen capture...');
        
        try {
            // Request screen sharing permission
            STATE.screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    cursor: 'always',
                    displaySurface: 'monitor'
                },
                audio: false
            });
            
            // Handle user stopping screen share via browser UI
            STATE.screenCaptureStream.getVideoTracks()[0].onended = () => {
                console.log('[GEMINI-WIDGET] 📸 Screen sharing stopped by user');
                stopScreenCapture();
            };
            
            STATE.isScreenSharing = true;
            STATE.screenshotCount = 0;
            
            // Update UI
            STATE.ui.screenBtn.classList.add('active');
            STATE.ui.screenBtnText.textContent = 'Остановить трансляцию';
            STATE.ui.screenStatus.classList.add('visible');
            STATE.ui.screenPreview.classList.add('visible');
            
            console.log('[GEMINI-WIDGET] ✅ Screen capture started');
            console.log(`[GEMINI-WIDGET] 📸 Will capture every ${CONFIG.screenCapture.intervalMs}ms`);
            
            // Take first screenshot immediately
            await captureAndSendScreenshot();
            
            // Start interval for automatic screenshots
            STATE.screenCaptureInterval = setInterval(async () => {
                if (STATE.isScreenSharing && STATE.isConnected) {
                    await captureAndSendScreenshot();
                }
            }, CONFIG.screenCapture.intervalMs);
            
        } catch (error) {
            console.error('[GEMINI-WIDGET] ❌ Screen capture error:', error);
            
            if (error.name === 'NotAllowedError') {
                updateUIState('error', 'Доступ к экрану запрещён');
            } else {
                updateUIState('error', 'Ошибка захвата экрана');
            }
        }
    }

    async function stopScreenCapture() {
        console.log('[GEMINI-WIDGET] 📸 Stopping screen capture...');
        
        // Stop interval
        if (STATE.screenCaptureInterval) {
            clearInterval(STATE.screenCaptureInterval);
            STATE.screenCaptureInterval = null;
        }
        
        // Stop stream tracks
        if (STATE.screenCaptureStream) {
            STATE.screenCaptureStream.getTracks().forEach(track => track.stop());
            STATE.screenCaptureStream = null;
        }
        
        STATE.isScreenSharing = false;
        
        // Update UI
        STATE.ui.screenBtn.classList.remove('active');
        STATE.ui.screenBtnText.textContent = 'Поделиться экраном';
        STATE.ui.screenStatus.classList.remove('visible');
        STATE.ui.screenPreview.classList.remove('visible');
        
        console.log(`[GEMINI-WIDGET] ✅ Screen capture stopped. Total screenshots: ${STATE.screenshotCount}`);
    }

    async function captureAndSendScreenshot() {
        if (!STATE.screenCaptureStream || !STATE.isScreenSharing) {
            console.log('[GEMINI-WIDGET] ⚠️ No active screen stream');
            return;
        }
        
        if (!STATE.isConnected || !STATE.ws || STATE.ws.readyState !== WebSocket.OPEN) {
            console.log('[GEMINI-WIDGET] ⚠️ WebSocket not connected, skipping screenshot');
            return;
        }
        
        try {
            const track = STATE.screenCaptureStream.getVideoTracks()[0];
            if (!track || track.readyState !== 'live') {
                console.log('[GEMINI-WIDGET] ⚠️ Video track not active');
                return;
            }
            
            // Create video element to capture frame
            const video = document.createElement('video');
            video.srcObject = STATE.screenCaptureStream;
            video.muted = true;
            
            await new Promise((resolve) => {
                video.onloadedmetadata = () => {
                    video.play();
                    resolve();
                };
            });
            
            // Wait a bit for video to render
            await new Promise(r => setTimeout(r, 100));
            
            // Calculate dimensions (maintain aspect ratio)
            let width = video.videoWidth;
            let height = video.videoHeight;
            
            if (width > CONFIG.screenCapture.maxWidth) {
                const ratio = CONFIG.screenCapture.maxWidth / width;
                width = CONFIG.screenCapture.maxWidth;
                height = Math.round(height * ratio);
            }
            
            if (height > CONFIG.screenCapture.maxHeight) {
                const ratio = CONFIG.screenCapture.maxHeight / height;
                height = CONFIG.screenCapture.maxHeight;
                width = Math.round(width * ratio);
            }
            
            // Create canvas and draw frame
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, width, height);
            
            // Stop video
            video.pause();
            video.srcObject = null;
            
            // Convert to base64 JPEG
            const base64Image = canvas.toDataURL('image/jpeg', CONFIG.screenCapture.quality);
            
            // Update preview
            STATE.ui.screenPreviewImg.src = base64Image;
            STATE.screenshotCount++;
            STATE.lastScreenshotTime = Date.now();
            STATE.ui.screenPreviewLabel.textContent = `${STATE.screenshotCount} снимков`;
            
            // Calculate size
            const sizeKB = Math.round((base64Image.length * 0.75) / 1024);
            console.log(`[GEMINI-WIDGET] 📸 Screenshot #${STATE.screenshotCount}: ${width}x${height}, ${sizeKB}KB`);
            
            // Send to server
            STATE.ws.send(JSON.stringify({
                type: 'screen.context',
                image: base64Image,
                silent: CONFIG.screenCapture.silent
            }));
            
            console.log(`[GEMINI-WIDGET] ✅ Screenshot #${STATE.screenshotCount} sent to Gemini (silent: ${CONFIG.screenCapture.silent})`);
            
        } catch (error) {
            console.error('[GEMINI-WIDGET] ❌ Screenshot capture error:', error);
        }
    }

    // ============================================================================
    // UI STATE UPDATES
    // ============================================================================

    function updateUIState(state, message = '') {
        const ui = STATE.ui;
        if (!ui.mainCircle) return;

        ui.mainCircle.classList.remove('listening', 'speaking', 'interrupted');
        ui.statusDot.classList.remove('connected', 'disconnected', 'connecting', 'interrupted');
        ui.button.classList.remove('wellcomeai-pulse-animation');

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
        STATE.ui.button.classList.remove('wellcomeai-pulse-animation');
        
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
        STATE.ui.button.classList.add('wellcomeai-pulse-animation');

        // Stop screen capture
        if (STATE.isScreenSharing) {
            await stopScreenCapture();
        }

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
    // WEBSOCKET & GEMINI LOGIC
    // ============================================================================

    async function connectWebSocket() {
        updateUIState('connecting', 'Подключение...');
        
        const wsUrl = CONFIG.serverUrl.replace('http://', 'ws://').replace('https://', 'wss://');
        const endpoint = `${wsUrl}/ws/gemini/${CONFIG.assistantId}`;
        
        try {
            STATE.ws = new WebSocket(endpoint);
            STATE.ws.binaryType = 'arraybuffer';
            
            STATE.ws.onopen = () => {
                console.log('[GEMINI-WIDGET] ✅ WebSocket connected');
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
                console.error('[GEMINI-WIDGET] WS Error:', error);
                updateUIState('error', 'Ошибка сервера');
            };
            
            STATE.ws.onclose = async (event) => {
                console.log('[GEMINI-WIDGET] WS Closed:', event.code);
                STATE.isConnected = false;
                STATE.readyToRecord = false;
                stopPlayback();
                if (STATE.isRecording) stopRecording();
                
                // Stop screen capture on disconnect
                if (STATE.isScreenSharing) {
                    await stopScreenCapture();
                }
                
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
                case 'gemini.setup.complete':
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
                    if (!STATE.isPlaying) startAudioStream();
                    break;
                    
                case 'assistant.speech.ended':
                    STATE.isSpeaking = false;
                    stopPlayback();
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
                    console.error('[GEMINI] Error:', data.error);
                    updateUIState('error', data.error.message || 'Ошибка');
                    break;
            }
        } catch (e) {
            console.error('[GEMINI-WIDGET] Parse error:', e);
        }
    }

    // ============================================================================
    // AUDIO HANDLING
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
            
            if (STATE.audioStreamNode) {
                STATE.audioStreamNode.port.postMessage({ type: 'audioData', buffer: audioData });
            }
            
            if (!STATE.isPlaying) startAudioStream();
            
        } catch (error) {
            console.error(error);
        }
    }

    async function startRecording() {
        if (STATE.isRecording) return;
        console.log('[GEMINI-WIDGET] 🎙️ Starting recording...');
        
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
        console.log('[GEMINI-WIDGET] 🛑 Recording stopped');
    }

    async function startAudioStream() {
        if (STATE.isPlaying) return;
        
        try {
            if (!STATE.audioStreamNode && STATE.streamWorkletReady) {
                STATE.audioStreamNode = new AudioWorkletNode(STATE.audioContext, 'audio-stream-processor');
                STATE.audioStreamNode.connect(STATE.audioContext.destination);
            }
            STATE.isPlaying = true;
        } catch (error) {
            console.error('Stream Error:', error);
        }
    }

    function stopPlayback() {
        if (!STATE.isPlaying) return;
        
        if (STATE.audioStreamNode) {
            STATE.audioStreamNode.port.postMessage({ type: 'clear' });
        }
        
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
