// Здесь копируем логику из widget.js
// - WebSocket подключение
// - Аудио обработка
// - Screen capture

const WS_URL = 'wss://realtime-saas.onrender.com/ws';

let ws = null;
let assistantId = null;

// Инициализация
(async function init() {
  const data = await chrome.storage.local.get(['selectedAssistant']);
  assistantId = data.selectedAssistant;
  
  if (!assistantId) {
    document.getElementById('status').textContent = 'Ошибка: ассистент не выбран';
    return;
  }
  
  connectWebSocket();
})();

function connectWebSocket() {
  ws = new WebSocket(`${WS_URL}/${assistantId}`);
  
  ws.onopen = () => {
    document.getElementById('status').textContent = '✅ Подключено';
    console.log('WebSocket connected');
  };
  
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Message:', data);
    
    // Обработка сообщений (как в widget.js)
    if (data.type === 'response.text.delta') {
      showMessage(data.delta);
    }
  };
  
  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
    document.getElementById('status').textContent = '❌ Ошибка соединения';
  };
  
  ws.onclose = () => {
    document.getElementById('status').textContent = '⚠️ Отключено';
  };
}

function showMessage(text) {
  const messagesDiv = document.getElementById('messages');
  const msgDiv = document.createElement('div');
  msgDiv.textContent = text;
  messagesDiv.appendChild(msgDiv);
}

// TODO: Добавить аудио запись (из widget.js)
