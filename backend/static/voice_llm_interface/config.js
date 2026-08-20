/* ============================================================ */
/* Voicyfy AI - Configuration                                   */
/* Voice LLM Interface - Voicyfy                                */
/* Version: 4.3.2 - IIFE wrapped                                */
/* ============================================================ */

'use strict';

// IIFE - все переменные остаются внутри, наружу только window.VoicyfyConfig
(function() {
    
    // ============================================================================
    // DEBUG & ENVIRONMENT
    // ============================================================================
    
    const DEBUG_MODE = true;
    
    // ============================================================================
    // CONNECTION SETTINGS
    // ============================================================================
    
    const MAX_RECONNECT_ATTEMPTS = 5;
    const MOBILE_MAX_RECONNECT_ATTEMPTS = 10;
    const PING_INTERVAL = 15000;
    const MOBILE_PING_INTERVAL = 10000;
    
    // ============================================================================
    // STORAGE KEYS
    // ============================================================================
    
    const STORAGE_KEY_ASSISTANT = 'lastGeminiAssistantId';
    const HISTORY_STORAGE_KEY = 'voicyfy_chat_history';
    const MAX_HISTORY_PAIRS = 5;
    
    // ============================================================================
    // SERVER URL
    // ============================================================================
    
    const SERVER_URL = `${window.location.protocol}//${window.location.host}`;
    
    // ============================================================================
    // DEVICE DETECTION
    // ============================================================================
    
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
    const isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
    const isAndroid = /Android/i.test(navigator.userAgent);
    
    // ============================================================================
    // AUDIO INPUT CONFIGURATION
    // ============================================================================
    
    const AUDIO_CONFIG = {
        silenceThreshold: 0.01,
        silenceDuration: 300,
        bufferCheckInterval: 50,
        soundDetectionThreshold: 0.02,
        amplificationFactor: isMobile ? 2.0 : 1.0
    };
    
    // ============================================================================
    // AUDIO PLAYBACK CONFIGURATION
    // ============================================================================
    
    const AUDIO_PLAYBACK_SAMPLE_RATE = 24000;
    const MIN_BUFFER_SAMPLES = 12000;
    const CHUNK_SIZE = 4800;
    const SCHEDULE_AHEAD_TIME = 0.3;
    const SCHEDULER_INTERVAL_MS = 50;
    const CROSSFADE_SAMPLES = 64;
    const MINIMUM_AUDIO_LENGTH = 300;
    
    // ============================================================================
    // LOGGING UTILITY
    // ============================================================================
    
    function log(message, type = 'info') {
        if (DEBUG_MODE || type === 'error') {
            const prefix = '[Voicyfy]';
            const timestamp = new Date().toLocaleTimeString();
            
            if (type === 'error') {
                console.error(`${prefix} [${timestamp}] ERROR:`, message);
            } else if (type === 'warn') {
                console.warn(`${prefix} [${timestamp}] WARNING:`, message);
            } else {
                console.log(`${prefix} [${timestamp}]`, message);
            }
        }
    }
    
    // ============================================================================
    // EXPORT TO GLOBAL SCOPE (только один объект!)
    // ============================================================================
    
    window.VoicyfyConfig = {
        DEBUG_MODE,
        MAX_RECONNECT_ATTEMPTS,
        MOBILE_MAX_RECONNECT_ATTEMPTS,
        PING_INTERVAL,
        MOBILE_PING_INTERVAL,
        STORAGE_KEY_ASSISTANT,
        HISTORY_STORAGE_KEY,
        MAX_HISTORY_PAIRS,
        SERVER_URL,
        isMobile,
        isIOS,
        isAndroid,
        AUDIO_CONFIG,
        AUDIO_PLAYBACK_SAMPLE_RATE,
        MIN_BUFFER_SAMPLES,
        CHUNK_SIZE,
        SCHEDULE_AHEAD_TIME,
        SCHEDULER_INTERVAL_MS,
        CROSSFADE_SAMPLES,
        MINIMUM_AUDIO_LENGTH,
        log
    };
    
    // Log initialization
    log('📦 Config loaded');
    log('   Server: ' + SERVER_URL);
    log('   Device: ' + (isMobile ? (isIOS ? 'iOS' : 'Android') : 'Desktop'));
    
})();
