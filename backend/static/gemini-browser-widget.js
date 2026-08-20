/**
 * 🚀 Gemini Browser Widget v1.1 - UNIVERSAL
 * Работает на ЛЮБОМ сайте без привязки к классам
 * 
 * ✅ visibleText — агент видит текст страницы как человек
 * ✅ Универсальный скан — все кликабельные элементы
 * ✅ Элементы с текстом — карточки, цены, названия
 * 
 * @version 1.1.0
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
        browserAgent: true,
        
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
        
        domController: {
            highlightDuration: 600,
            typingSpeed: 40,
            actionDelay: 300,
            maxElements: 100,
            maxVisibleTextLength: 3000  // 🆕 Лимит текста
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
        
        browserAgent: {
            isExecuting: false,
            currentTaskId: null,
            highlightedElement: null
        },
        
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
    // DOM CONTROLLER v1.1 - UNIVERSAL
    // ============================================================================

    class DOMController {
        constructor() {
            this.highlightOverlay = null;
            this.cursorElement = null;
            this.createStyles();
        }
        
        createStyles() {
            if (document.getElementById('browser-agent-styles')) return;
            
            const style = document.createElement('style');
            style.id = 'browser-agent-styles';
            style.textContent = `
                .ba-highlight {
                    position: fixed;
                    pointer-events: none;
                    z-index: 2147483646;
                    border: 3px solid #3b82f6;
                    border-radius: 8px;
                    background: rgba(59, 130, 246, 0.15);
                    box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
                    transition: all 0.3s ease;
                    animation: ba-pulse 0.6s ease-in-out infinite;
                }
                
                @keyframes ba-pulse {
                    0%, 100% { 
                        box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
                        transform: scale(1);
                    }
                    50% { 
                        box-shadow: 0 0 30px rgba(59, 130, 246, 0.6);
                        transform: scale(1.02);
                    }
                }
                
                .ba-cursor {
                    position: fixed;
                    width: 20px;
                    height: 20px;
                    pointer-events: none;
                    z-index: 2147483647;
                    transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
                }
                
                .ba-cursor::before {
                    content: '';
                    position: absolute;
                    width: 0;
                    height: 0;
                    border-left: 8px solid #3b82f6;
                    border-top: 5px solid transparent;
                    border-bottom: 5px solid transparent;
                    transform: rotate(-45deg);
                    filter: drop-shadow(2px 2px 2px rgba(0,0,0,0.3));
                }
                
                .ba-cursor.clicking::after {
                    content: '';
                    position: absolute;
                    top: -10px;
                    left: -10px;
                    width: 40px;
                    height: 40px;
                    border: 2px solid #3b82f6;
                    border-radius: 50%;
                    animation: ba-click-ripple 0.4s ease-out forwards;
                }
                
                @keyframes ba-click-ripple {
                    0% { transform: scale(0.5); opacity: 1; }
                    100% { transform: scale(1.5); opacity: 0; }
                }
                
                .ba-typing-indicator {
                    position: absolute;
                    bottom: -25px;
                    left: 0;
                    font-size: 11px;
                    color: #3b82f6;
                    background: white;
                    padding: 2px 8px;
                    border-radius: 4px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    white-space: nowrap;
                }
                
                .ba-status-toast {
                    position: fixed;
                    top: 20px;
                    left: 50%;
                    transform: translateX(-50%);
                    background: linear-gradient(135deg, #1e3a8a, #3b82f6);
                    color: white;
                    padding: 12px 24px;
                    border-radius: 12px;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    font-size: 14px;
                    font-weight: 500;
                    box-shadow: 0 4px 20px rgba(59, 130, 246, 0.4);
                    z-index: 2147483647;
                    opacity: 0;
                    transition: opacity 0.3s ease;
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }
                
                .ba-status-toast.visible { opacity: 1; }
                
                .ba-status-toast .ba-spinner {
                    width: 16px;
                    height: 16px;
                    border: 2px solid rgba(255,255,255,0.3);
                    border-top-color: white;
                    border-radius: 50%;
                    animation: ba-spin 0.8s linear infinite;
                }
                
                @keyframes ba-spin {
                    to { transform: rotate(360deg); }
                }
            `;
            document.head.appendChild(style);
        }
        
        // ========================================================================
        // 🆕 UNIVERSAL DOM SCANNING
        // ========================================================================
        
        scanDOM() {
            const elements = [];
            const seenSelectors = new Set();
            
            // ================================================================
            // 1. ИНТЕРАКТИВНЫЕ ЭЛЕМЕНТЫ (кнопки, ссылки, инпуты)
            // ================================================================
            const interactiveSelectors = [
                'button',
                'a[href]',
                'input:not([type="hidden"])',
                'select',
                'textarea',
                '[onclick]',
                '[role="button"]',
                '[role="link"]',
                '[role="menuitem"]',
                '[role="tab"]',
                '[type="submit"]',
                '[tabindex]:not([tabindex="-1"])'
            ];
            
            const interactiveElements = document.querySelectorAll(interactiveSelectors.join(','));
            
            interactiveElements.forEach((el) => {
                const elementData = this.extractElementData(el);
                if (elementData && !seenSelectors.has(elementData.selector)) {
                    seenSelectors.add(elementData.selector);
                    elementData.interactive = true;
                    elements.push(elementData);
                }
            });
            
            // ================================================================
            // 2. 🆕 ЭЛЕМЕНТЫ С ТЕКСТОМ В VIEWPORT (карточки, товары, и т.д.)
            // ================================================================
            const allVisibleElements = document.querySelectorAll('*');
            
            allVisibleElements.forEach((el) => {
                // Пропускаем служебные теги
                const tag = el.tagName.toLowerCase();
                if (['script', 'style', 'noscript', 'svg', 'path', 'meta', 'link', 'head', 'html', 'body'].includes(tag)) {
                    return;
                }
                
                // Пропускаем если уже добавили
                const selector = this.getUniqueSelector(el);
                if (seenSelectors.has(selector)) return;
                
                // Проверяем видимость
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;
                if (rect.bottom < 0 || rect.top > window.innerHeight) return;
                if (rect.right < 0 || rect.left > window.innerWidth) return;
                
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return;
                
                // 🆕 Проверяем: есть ли СОБСТВЕННЫЙ текст (не от детей)
                const ownText = this.getOwnText(el);
                if (!ownText || ownText.length < 3) return;
                
                // 🆕 Это похоже на карточку/товар/элемент списка?
                const isClickable = (
                    el.onclick ||
                    style.cursor === 'pointer' ||
                    el.closest('a') ||
                    el.closest('button') ||
                    el.getAttribute('role') === 'button' ||
                    el.classList.toString().match(/card|item|product|goods|result|option|row/i)
                );
                
                // Добавляем только кликабельные элементы с текстом
                if (isClickable && elements.length < CONFIG.domController.maxElements) {
                    seenSelectors.add(selector);
                    elements.push({
                        tag: tag,
                        text: ownText.substring(0, 100),
                        selector: selector,
                        interactive: false,
                        clickable: true,
                        rect: {
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height)
                        }
                    });
                }
            });
            
            // ================================================================
            // 3. 🆕 ВИДИМЫЙ ТЕКСТ СТРАНИЦЫ (как человек читает)
            // ================================================================
            const visibleText = this.getVisibleText();
            
            // ================================================================
            // 4. Формируем результат
            // ================================================================
            return {
                url: window.location.href,
                title: document.title,
                elements: elements.slice(0, CONFIG.domController.maxElements),
                visibleText: visibleText,  // 🆕
                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight,
                    scrollY: window.scrollY
                },
                timestamp: Date.now()
            };
        }
        
        // 🆕 Получить собственный текст элемента (без детей)
        getOwnText(el) {
            let text = '';
            
            for (const node of el.childNodes) {
                if (node.nodeType === Node.TEXT_NODE) {
                    text += node.textContent;
                }
            }
            
            // Также берём alt, title, value, placeholder
            text += ' ' + (el.getAttribute('alt') || '');
            text += ' ' + (el.getAttribute('title') || '');
            text += ' ' + (el.value || '');
            text += ' ' + (el.placeholder || '');
            
            return text.trim().replace(/\s+/g, ' ').substring(0, 150);
        }
        
        // 🆕 Получить видимый текст страницы
        getVisibleText() {
            const viewportHeight = window.innerHeight;
            const scrollY = window.scrollY;
            
            // Собираем текст из видимых элементов
            const textParts = [];
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                {
                    acceptNode: (node) => {
                        const parent = node.parentElement;
                        if (!parent) return NodeFilter.FILTER_REJECT;
                        
                        const tag = parent.tagName.toLowerCase();
                        if (['script', 'style', 'noscript'].includes(tag)) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        
                        const rect = parent.getBoundingClientRect();
                        // В viewport или рядом
                        if (rect.top < viewportHeight + 200 && rect.bottom > -200) {
                            return NodeFilter.FILTER_ACCEPT;
                        }
                        
                        return NodeFilter.FILTER_REJECT;
                    }
                }
            );
            
            let node;
            let totalLength = 0;
            const maxLength = CONFIG.domController.maxVisibleTextLength;
            
            while ((node = walker.nextNode()) && totalLength < maxLength) {
                const text = node.textContent.trim();
                if (text.length > 2) {
                    textParts.push(text);
                    totalLength += text.length;
                }
            }
            
            // Объединяем и чистим
            return textParts
                .join(' ')
                .replace(/\s+/g, ' ')
                .substring(0, maxLength);
        }
        
        extractElementData(el) {
            const rect = el.getBoundingClientRect();
            
            // Пропускаем невидимые
            if (rect.width === 0 || rect.height === 0) return null;
            if (window.getComputedStyle(el).display === 'none') return null;
            if (window.getComputedStyle(el).visibility === 'hidden') return null;
            
            // Пропускаем за пределами viewport
            if (rect.bottom < 0 || rect.top > window.innerHeight) return null;
            if (rect.right < 0 || rect.left > window.innerWidth) return null;
            
            const tag = el.tagName.toLowerCase();
            
            return {
                tag: tag,
                type: el.type || null,
                text: this.getElementText(el),
                placeholder: el.placeholder || null,
                name: el.name || null,
                value: el.value ? el.value.substring(0, 50) : null,
                href: el.href ? el.href.substring(0, 100) : null,
                ariaLabel: el.getAttribute('aria-label') || null,
                selector: this.getUniqueSelector(el),
                rect: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                }
            };
        }
        
        getElementText(el) {
            if (el.tagName === 'INPUT') {
                return el.placeholder || el.getAttribute('aria-label') || el.name || '';
            }
            
            const text = el.innerText || el.textContent || '';
            return text.trim().substring(0, 100).replace(/\s+/g, ' ');
        }
        
        getUniqueSelector(element) {
            // ID
            if (element.id && !element.id.match(/^\d/)) {
                return `#${element.id}`;
            }
            
            // data-testid
            if (element.dataset.testid) {
                return `[data-testid="${element.dataset.testid}"]`;
            }
            
            // data-id
            if (element.dataset.id) {
                return `[data-id="${element.dataset.id}"]`;
            }
            
            // name для форм
            if (element.name) {
                const tag = element.tagName.toLowerCase();
                const selector = `${tag}[name="${element.name}"]`;
                if (document.querySelectorAll(selector).length === 1) {
                    return selector;
                }
            }
            
            // Уникальный класс + тег
            if (element.className && typeof element.className === 'string') {
                const classes = element.className.split(' ').filter(c => c && !c.startsWith('ba-'));
                if (classes.length > 0) {
                    const selector = `${element.tagName.toLowerCase()}.${classes[0]}`;
                    if (document.querySelectorAll(selector).length === 1) {
                        return selector;
                    }
                }
            }
            
            // Путь
            const path = [];
            let current = element;
            
            while (current && current !== document.body && path.length < 5) {
                let selector = current.tagName.toLowerCase();
                
                if (current.className && typeof current.className === 'string') {
                    const firstClass = current.className.split(' ').find(c => c && !c.startsWith('ba-'));
                    if (firstClass) {
                        selector += `.${firstClass}`;
                    }
                }
                
                const parent = current.parentElement;
                if (parent) {
                    const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                    if (siblings.length > 1) {
                        const index = siblings.indexOf(current) + 1;
                        selector += `:nth-child(${index})`;
                    }
                }
                
                path.unshift(selector);
                current = current.parentElement;
            }
            
            return path.join(' > ');
        }
        
        // ========================================================================
        // ACTION EXECUTION
        // ========================================================================
        
        async executeAction(action) {
            const { type, selector, params = {} } = action;
            
            console.log(`[DOM-CONTROLLER] Executing: ${type} on ${selector}`);
            
            try {
                switch (type) {
                    case 'click':
                        return await this.performClick(selector);
                    case 'type':
                        return await this.performType(selector, params.text || '');
                    case 'scroll':
                        return await this.performScroll(selector, params.direction || 'down');
                    case 'wait':
                        return await this.performWait(selector, params.timeout || 3000);
                    case 'extract':
                        return await this.performExtract(selector);
                    default:
                        return { success: false, error: `Unknown action type: ${type}` };
                }
            } catch (error) {
                console.error(`[DOM-CONTROLLER] Action error:`, error);
                return { success: false, error: error.message };
            }
        }
        
        async performClick(selector) {
            const element = document.querySelector(selector);
            if (!element) {
                return { success: false, error: `Element not found: ${selector}` };
            }
            
            await this.highlightElement(element);
            await this.animateCursor(element);
            
            // Скроллим к элементу если нужно
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            await this.sleep(200);
            
            element.click();
            this.removeHighlight();
            
            return { success: true };
        }
        
        async performType(selector, text) {
            const element = document.querySelector(selector);
            if (!element) {
                return { success: false, error: `Element not found: ${selector}` };
            }
            
            await this.highlightElement(element);
            element.focus();
            
            // Очистка
            if (element.value) {
                element.value = '';
            }
            
            // Печатаем
            for (const char of text) {
                element.value += char;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                await this.sleep(CONFIG.domController.typingSpeed);
            }
            
            element.dispatchEvent(new Event('change', { bubbles: true }));
            this.removeHighlight();
            
            return { success: true, typed: text };
        }
        
        async performScroll(selector, direction) {
            let target = document;
            
            if (selector && selector !== 'window') {
                const element = document.querySelector(selector);
                if (element) target = element;
            }
            
            const amount = direction === 'up' ? -400 : 400;
            
            if (target === document) {
                window.scrollBy({ top: amount, behavior: 'smooth' });
            } else {
                target.scrollBy({ top: amount, behavior: 'smooth' });
            }
            
            await this.sleep(500);
            return { success: true, direction };
        }
        
        async performWait(selector, timeout) {
            const startTime = Date.now();
            
            while (Date.now() - startTime < timeout) {
                const element = document.querySelector(selector);
                if (element) return { success: true, found: true };
                await this.sleep(200);
            }
            
            return { success: false, error: `Timeout waiting for: ${selector}` };
        }
        
        async performExtract(selector) {
            const element = document.querySelector(selector);
            if (!element) {
                return { success: false, error: `Element not found: ${selector}` };
            }
            
            return {
                success: true,
                data: {
                    text: element.innerText || element.textContent,
                    value: element.value,
                    href: element.href
                }
            };
        }
        
        // ========================================================================
        // VISUAL EFFECTS
        // ========================================================================
        
        async highlightElement(element) {
            this.removeHighlight();
            
            const rect = element.getBoundingClientRect();
            
            const highlight = document.createElement('div');
            highlight.className = 'ba-highlight';
            highlight.style.cssText = `
                left: ${rect.left - 4}px;
                top: ${rect.top - 4}px;
                width: ${rect.width + 8}px;
                height: ${rect.height + 8}px;
            `;
            
            document.body.appendChild(highlight);
            this.highlightOverlay = highlight;
            
            await this.sleep(CONFIG.domController.highlightDuration);
        }
        
        removeHighlight() {
            if (this.highlightOverlay) {
                this.highlightOverlay.remove();
                this.highlightOverlay = null;
            }
        }
        
        async animateCursor(targetElement) {
            if (!this.cursorElement) {
                this.cursorElement = document.createElement('div');
                this.cursorElement.className = 'ba-cursor';
                document.body.appendChild(this.cursorElement);
            }
            
            const rect = targetElement.getBoundingClientRect();
            const targetX = rect.left + rect.width / 2;
            const targetY = rect.top + rect.height / 2;
            
            this.cursorElement.style.opacity = '1';
            this.cursorElement.style.left = `${targetX}px`;
            this.cursorElement.style.top = `${targetY}px`;
            
            await this.sleep(400);
            
            this.cursorElement.classList.add('clicking');
            await this.sleep(300);
            this.cursorElement.classList.remove('clicking');
            
            await this.sleep(200);
            this.cursorElement.style.opacity = '0';
        }
        
        showStatusToast(message, showSpinner = false) {
            let toast = document.querySelector('.ba-status-toast');
            
            if (!toast) {
                toast = document.createElement('div');
                toast.className = 'ba-status-toast';
                document.body.appendChild(toast);
            }
            
            toast.innerHTML = `
                ${showSpinner ? '<div class="ba-spinner"></div>' : ''}
                <span>${message}</span>
            `;
            
            toast.classList.add('visible');
            
            setTimeout(() => {
                toast.classList.remove('visible');
            }, 3000);
        }
        
        hideStatusToast() {
            const toast = document.querySelector('.ba-status-toast');
            if (toast) toast.classList.remove('visible');
        }
        
        sleep(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }
    }

    // ============================================================================
    // INITIALIZATION
    // ============================================================================

    let domController = null;

    function init() {
        console.log('[BROWSER-WIDGET] 🚀 Initializing v1.1 (Universal)...');
        
        const getScriptTag = () => {
            const scripts = document.querySelectorAll('script');
            for (let i = 0; i < scripts.length; i++) {
                if (scripts[i].hasAttribute('data-assistantId') || scripts[i].dataset.assistantid) {
                    return scripts[i];
                }
            }
            return document.currentScript;
        };

        const scriptTag = getScriptTag();
        
        if (!scriptTag) {
            console.error('[BROWSER-WIDGET] Script tag not found');
            return;
        }

        CONFIG.assistantId = scriptTag.getAttribute('data-assistantId') || 
                            scriptTag.getAttribute('data-assistantid') ||
                            scriptTag.dataset.assistantid;
        CONFIG.serverUrl = scriptTag.getAttribute('data-server') || scriptTag.dataset.server;
        
        const posAttr = scriptTag.getAttribute('data-position') || scriptTag.dataset.position;
        if (posAttr) CONFIG.position = posAttr;

        if (!CONFIG.assistantId || !CONFIG.serverUrl) {
            console.error('[BROWSER-WIDGET] Missing required parameters');
            return;
        }
        
        console.log('[BROWSER-WIDGET] Config:', {
            assistantId: CONFIG.assistantId,
            serverUrl: CONFIG.serverUrl,
            browserAgent: CONFIG.browserAgent
        });
        
        domController = new DOMController();
        
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
        
        console.log('[BROWSER-WIDGET] ✅ Initialization complete');
    }

    async function initAudioContext() {
        if (STATE.audioContext) {
            if (STATE.audioContext.state === 'suspended') {
                await STATE.audioContext.resume();
            }
            return;
        }
        
        console.log('[BROWSER-WIDGET] 🎧 Creating AudioContext...');
        
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
            
            console.log('[BROWSER-WIDGET] ✅ AudioWorklets loaded');
        } catch (error) {
            console.error('[BROWSER-WIDGET] ❌ AudioWorklet load failed:', error);
        }
    }

    // ============================================================================
    // UI CREATION
    // ============================================================================

    function getWidgetPositionStyles() {
        const parts = CONFIG.position.toLowerCase().split('-');
        let vertical = 'bottom';
        let horizontal = 'right';
        
        if (parts.includes('top')) vertical = 'top';
        if (parts.includes('left')) horizontal = 'left';
        
        return `${vertical}: 20px; ${horizontal}: 20px;`;
    }

    function createStyles() {
        const styleEl = document.createElement('style');
        styleEl.id = 'browser-widget-styles';
        styleEl.textContent = `
            .bw-widget-container {
                position: fixed;
                ${getWidgetPositionStyles()}
                z-index: 2147483647;
                transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                font-family: 'Segoe UI', 'Roboto', sans-serif;
            }
            
            .bw-widget-button {
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
            
            .bw-widget-button:hover {
                transform: scale(1.05);
                box-shadow: 0 10px 30px rgba(74, 134, 232, 0.4);
            }
            
            .bw-button-inner {
                position: relative;
                width: 40px;
                height: 40px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .bw-pulse-ring {
                position: absolute;
                width: 100%;
                height: 100%;
                border-radius: 50%;
                animation: bw-pulse-ring 3s ease-out infinite;
                background: radial-gradient(rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0) 70%);
                opacity: 0;
            }
            
            @keyframes bw-pulse-ring {
                0% { transform: scale(0.5); opacity: 0; }
                25% { opacity: 0.4; }
                100% { transform: scale(1.2); opacity: 0; }
            }
            
            .bw-audio-bars-mini {
                display: flex;
                align-items: center;
                height: 26px;
                gap: 4px;
                justify-content: center;
            }
            
            .bw-audio-bar-mini {
                width: 3px;
                height: 12px;
                background-color: #ffffff;
                border-radius: 1.5px;
                animation: bw-eq-animation 1.2s ease-in-out infinite;
                opacity: 0.9;
            }
            
            .bw-audio-bar-mini:nth-child(1) { animation-delay: 0.0s; height: 7px; }
            .bw-audio-bar-mini:nth-child(2) { animation-delay: 0.3s; height: 12px; }
            .bw-audio-bar-mini:nth-child(3) { animation-delay: 0.1s; height: 18px; }
            .bw-audio-bar-mini:nth-child(4) { animation-delay: 0.5s; height: 9px; }
            
            @keyframes bw-eq-animation {
                0% { height: 5px; }
                50% { height: 18px; }
                100% { height: 5px; }
            }
            
            .bw-widget-expanded {
                position: absolute;
                bottom: 0;
                right: 0;
                width: 340px;
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
            
            .bw-widget-container.active .bw-widget-expanded {
                height: 480px;
                opacity: 1;
                pointer-events: all;
            }
            
            .bw-widget-container.active .bw-widget-button {
                transform: scale(0.9);
            }
            
            .bw-widget-header {
                padding: 15px 20px;
                background: linear-gradient(135deg, #1e3a8a, #3b82f6);
                color: white;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-radius: 20px 20px 0 0;
            }
            
            .bw-widget-title {
                font-weight: 600;
                font-size: 15px;
                display: flex;
                align-items: center;
                gap: 8px;
            }
            
            .bw-widget-title .bw-agent-badge {
                font-size: 10px;
                background: rgba(255,255,255,0.2);
                padding: 2px 6px;
                border-radius: 4px;
            }
            
            .bw-widget-close {
                background: none;
                border: none;
                color: white;
                font-size: 18px;
                cursor: pointer;
                opacity: 0.8;
                transition: all 0.2s;
            }
            
            .bw-widget-close:hover { opacity: 1; transform: scale(1.1); }
            
            .bw-widget-content {
                flex: 1;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                background: #f9fafc;
                position: relative;
                padding: 20px;
            }
            
            .bw-main-circle {
                width: 160px;
                height: 160px;
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
            
            .bw-main-circle::before {
                content: '';
                position: absolute;
                width: 140%;
                height: 140%;
                background: linear-gradient(45deg, rgba(255, 255, 255, 0.3), rgba(74, 134, 232, 0.2));
                animation: bw-wave 8s linear infinite;
                border-radius: 40%;
            }
            
            @keyframes bw-wave {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            
            .bw-main-circle.listening {
                background: linear-gradient(135deg, #dbeafe, #eff6ff);
                box-shadow: 0 0 30px rgba(37, 99, 235, 0.5);
            }
            
            .bw-main-circle.speaking {
                background: linear-gradient(135deg, #dcfce7, #ecfdf5);
                box-shadow: 0 0 30px rgba(5, 150, 105, 0.5);
            }
            
            .bw-main-circle.agent-working {
                background: linear-gradient(135deg, #fef3c7, #fef9c3);
                box-shadow: 0 0 30px rgba(245, 158, 11, 0.5);
            }
            
            .bw-main-circle.agent-working::after {
                content: '';
                position: absolute;
                width: 100%;
                height: 100%;
                border: 3px solid transparent;
                border-top-color: #f59e0b;
                border-radius: 50%;
                animation: bw-agent-spin 1s linear infinite;
            }
            
            @keyframes bw-agent-spin {
                to { transform: rotate(360deg); }
            }
            
            .bw-mic-icon {
                color: #3b82f6;
                font-size: 32px;
                z-index: 10;
                transition: color 0.3s ease;
            }
            
            .bw-main-circle.listening .bw-mic-icon { color: #2563eb; }
            .bw-main-circle.speaking .bw-mic-icon { color: #059669; }
            .bw-main-circle.agent-working .bw-mic-icon { color: #d97706; }
            
            .bw-status-bar {
                margin-top: 20px;
                padding: 10px 20px;
                background: white;
                border-radius: 12px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.05);
                text-align: center;
                max-width: 280px;
                min-height: 40px;
            }
            
            .bw-status-text {
                font-size: 13px;
                color: #475569;
                line-height: 1.4;
            }
            
            .bw-status-text.agent {
                color: #d97706;
                font-weight: 500;
            }
            
            .bw-connection-error {
                color: #ef4444;
                background-color: rgba(254, 226, 226, 0.8);
                border: 1px solid #ef4444;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 13px;
                margin-top: 10px;
                display: none;
            }
            
            .bw-connection-error.visible { display: block; }
            
            .bw-loader-modal {
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
            
            .bw-loader-modal.active { opacity: 1; visibility: visible; }
            
            .bw-loader {
                width: 40px;
                height: 40px;
                border: 3px solid rgba(59, 130, 246, 0.2);
                border-radius: 50%;
                border-top-color: #3b82f6;
                animation: bw-spin 1s linear infinite;
            }
            
            @keyframes bw-spin { to { transform: rotate(360deg); } }
            
            .bw-branding {
                position: absolute;
                bottom: 10px;
                left: 50%;
                transform: translateX(-50%);
                opacity: 0.6;
            }
            
            .bw-branding img { height: 22px; }
            
            @keyframes bw-button-pulse {
                0% { box-shadow: 0 0 0 0 rgba(74, 134, 232, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(74, 134, 232, 0); }
                100% { box-shadow: 0 0 0 0 rgba(74, 134, 232, 0); }
            }
            
            .bw-pulse-animation { animation: bw-button-pulse 2s infinite; }
        `;
        document.head.appendChild(styleEl);
    }

    function createWidgetHTML() {
        const container = document.createElement('div');
        container.className = 'bw-widget-container';
        container.id = 'bw-widget-container';

        container.innerHTML = `
            <div class="bw-widget-button" id="bw-widget-button">
                <div class="bw-button-inner">
                    <div class="bw-pulse-ring"></div>
                    <div class="bw-audio-bars-mini">
                        <div class="bw-audio-bar-mini"></div>
                        <div class="bw-audio-bar-mini"></div>
                        <div class="bw-audio-bar-mini"></div>
                        <div class="bw-audio-bar-mini"></div>
                    </div>
                </div>
            </div>
            
            <div class="bw-widget-expanded" id="bw-widget-expanded">
                <div class="bw-widget-header">
                    <div class="bw-widget-title">
                        🤖 Голосовой Ассистент
                        <span class="bw-agent-badge">v1.1</span>
                    </div>
                    <button class="bw-widget-close" id="bw-widget-close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                
                <div class="bw-widget-content">
                    <div class="bw-main-circle" id="bw-main-circle">
                        <i class="fas fa-microphone bw-mic-icon"></i>
                    </div>
                    
                    <div class="bw-status-bar">
                        <div class="bw-status-text" id="bw-status-text">Нажмите для начала</div>
                    </div>
                    
                    <div class="bw-connection-error" id="bw-connection-error">
                        Ошибка соединения
                        <button id="bw-retry-btn" style="margin-left:10px;padding:4px 8px;cursor:pointer;">Повторить</button>
                    </div>
                    
                    <div class="bw-branding">
                        <a href="https://voicyfy.ru/" target="_blank">
                            <img src="https://i.ibb.co/ccw6sjdk/photo-2025-06-03-05-04-02.jpg" alt="Voicyfy">
                        </a>
                    </div>
                </div>
                
                <div class="bw-loader-modal" id="bw-loader-modal">
                    <div class="bw-loader"></div>
                </div>
            </div>
        `;

        document.body.appendChild(container);
    }

    function cacheUIElements() {
        STATE.ui = {
            container: document.getElementById('bw-widget-container'),
            button: document.getElementById('bw-widget-button'),
            expanded: document.getElementById('bw-widget-expanded'),
            closeBtn: document.getElementById('bw-widget-close'),
            mainCircle: document.getElementById('bw-main-circle'),
            statusText: document.getElementById('bw-status-text'),
            loader: document.getElementById('bw-loader-modal'),
            errorMsg: document.getElementById('bw-connection-error'),
            retryBtn: document.getElementById('bw-retry-btn')
        };

        STATE.ui.button.addEventListener('click', handleWidgetOpen);
        STATE.ui.closeBtn.addEventListener('click', handleWidgetClose);
        STATE.ui.retryBtn.addEventListener('click', () => connectWebSocket());
        
        STATE.ui.button.classList.add('bw-pulse-animation');
    }

    // ============================================================================
    // UI STATE UPDATES
    // ============================================================================

    function updateUIState(state, message = '') {
        const ui = STATE.ui;
        if (!ui.mainCircle) return;

        ui.mainCircle.classList.remove('listening', 'speaking', 'agent-working');
        ui.button.classList.remove('bw-pulse-animation');
        ui.statusText.classList.remove('agent');

        if (message) {
            ui.statusText.textContent = message;
        }

        switch (state) {
            case 'connecting':
                ui.loader.classList.add('active');
                ui.statusText.textContent = message || 'Подключение...';
                break;
                
            case 'connected':
                ui.loader.classList.remove('active');
                ui.errorMsg.classList.remove('visible');
                ui.statusText.textContent = message || 'Готов к работе';
                break;
                
            case 'recording':
                ui.mainCircle.classList.add('listening');
                ui.statusText.textContent = message || 'Слушаю...';
                break;
                
            case 'playing':
                ui.mainCircle.classList.add('speaking');
                ui.statusText.textContent = message || 'Говорю...';
                break;
                
            case 'agent-working':
                ui.mainCircle.classList.add('agent-working');
                ui.statusText.classList.add('agent');
                ui.statusText.textContent = message || 'Выполняю действие...';
                break;
                
            case 'error':
                ui.errorMsg.classList.add('visible');
                ui.loader.classList.remove('active');
                ui.statusText.textContent = message || 'Ошибка';
                break;
                
            case 'disconnected':
                ui.statusText.textContent = message || 'Отключено';
                break;
        }
    }

    // ============================================================================
    // WIDGET OPEN/CLOSE
    // ============================================================================

    async function handleWidgetOpen() {
        STATE.ui.container.classList.add('active');
        STATE.isWidgetOpen = true;
        STATE.ui.button.classList.remove('bw-pulse-animation');
        
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
        STATE.ui.button.classList.add('bw-pulse-animation');

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
    // WEBSOCKET CONNECTION
    // ============================================================================

    async function connectWebSocket() {
        updateUIState('connecting', 'Подключение...');
        
        const wsUrl = CONFIG.serverUrl.replace('http://', 'ws://').replace('https://', 'wss://');
        const endpoint = `${wsUrl}/ws/gemini-browser/${CONFIG.assistantId}`;
        
        console.log(`[BROWSER-WIDGET] Connecting to: ${endpoint}`);
        
        try {
            STATE.ws = new WebSocket(endpoint);
            STATE.ws.binaryType = 'arraybuffer';
            
            STATE.ws.onopen = () => {
                console.log('[BROWSER-WIDGET] ✅ WebSocket connected');
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
                console.error('[BROWSER-WIDGET] WS Error:', error);
                updateUIState('error', 'Ошибка сервера');
            };
            
            STATE.ws.onclose = (event) => {
                console.log('[BROWSER-WIDGET] WS Closed:', event.code);
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

    // ============================================================================
    // MESSAGE HANDLING
    // ============================================================================

    function handleWSMessage(event) {
        try {
            if (event.data instanceof ArrayBuffer || event.data instanceof Blob) return;

            const data = JSON.parse(event.data);
            
            // ================================================================
            // 🤖 BROWSER AGENT MESSAGES
            // ================================================================
            
            if (data.type === 'browser.dom_request') {
                console.log('[BROWSER-WIDGET] 📄 DOM request received');
                
                const dom = domController.scanDOM();
                
                STATE.ws.send(JSON.stringify({
                    type: 'browser.dom_response',
                    request_id: data.request_id,
                    dom: dom
                }));
                
                console.log(`[BROWSER-WIDGET] 📤 DOM sent: ${dom.elements.length} elements, ${dom.visibleText.length} chars text`);
                return;
            }
            
            if (data.type === 'browser.action') {
                console.log('[BROWSER-WIDGET] ⚡ Action request:', data.action);
                
                STATE.browserAgent.isExecuting = true;
                updateUIState('agent-working', 'Выполняю действие...');
                
                domController.executeAction(data.action).then(result => {
                    STATE.browserAgent.isExecuting = false;
                    
                    STATE.ws.send(JSON.stringify({
                        type: 'browser.action_result',
                        action_id: data.action.id,
                        result: result
                    }));
                    
                    console.log('[BROWSER-WIDGET] ✅ Action result:', result);
                    
                    if (STATE.isRecording) {
                        updateUIState('recording', 'Слушаю...');
                    } else {
                        updateUIState('connected', 'Готов');
                    }
                });
                return;
            }
            
            if (data.type === 'browser.task_started') {
                console.log('[BROWSER-WIDGET] 🚀 Task started:', data.task_id);
                STATE.browserAgent.currentTaskId = data.task_id;
                domController.showStatusToast(`Выполняю: ${data.goal}`, true);
                return;
            }
            
            if (data.type === 'browser_agent.speak') {
                console.log('[BROWSER-WIDGET] 🗣️ Agent message:', data.message);
                
                const isProgress = data.event === 'progress';
                domController.showStatusToast(data.message, isProgress);
                
                if (data.event === 'completed') {
                    STATE.browserAgent.currentTaskId = null;
                    updateUIState('connected', data.message);
                } else if (data.event === 'failed') {
                    STATE.browserAgent.currentTaskId = null;
                    updateUIState('error', data.message);
                } else {
                    updateUIState('agent-working', data.message);
                }
                return;
            }
            
            // ================================================================
            // 🎤 VOICE AGENT MESSAGES
            // ================================================================
            
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
                    updateUIState('recording', 'Прервано');
                    break;
                    
                case 'input.transcription':
                    if (data.text) {
                        updateUIState('recording', `Вы: "${data.text}"`);
                    }
                    break;
                    
                case 'error':
                    console.error('[BROWSER-WIDGET] Error:', data.error);
                    updateUIState('error', data.error.message || 'Ошибка');
                    break;
            }
        } catch (e) {
            console.error('[BROWSER-WIDGET] Parse error:', e);
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
        console.log('[BROWSER-WIDGET] 🎙️ Starting recording...');
        
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
        
        console.log('[BROWSER-WIDGET] 🛑 Recording stopped');
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

    function arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    // ============================================================================
    // PUBLIC API
    // ============================================================================

    window.BrowserWidget = {
        startTask: function(goal) {
            if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
                STATE.ws.send(JSON.stringify({
                    type: 'browser.start_task',
                    goal: goal,
                    url: window.location.href
                }));
                return true;
            }
            return false;
        },
        
        cancelTask: function() {
            if (STATE.browserAgent.currentTaskId && STATE.ws) {
                STATE.ws.send(JSON.stringify({
                    type: 'browser.cancel_task',
                    task_id: STATE.browserAgent.currentTaskId
                }));
                return true;
            }
            return false;
        },
        
        isConnected: function() { return STATE.isConnected; },
        open: handleWidgetOpen,
        close: handleWidgetClose
    };

    // ============================================================================
    // STARTUP
    // ============================================================================

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
