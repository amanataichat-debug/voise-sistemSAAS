const API_URL = 'https://realtime-saas.onrender.com/api';

// Проверяем авторизацию при открытии
document.addEventListener('DOMContentLoaded', async () => {
  const token = await getToken();
  
  if (token) {
    showMainView();
    await loadAssistants(token);
  } else {
    showLoginForm();
  }
});

// Логин
document.getElementById('loginBtn').addEventListener('click', async () => {
  const email = document.getElementById('email').value;
  const password = document.getElementById('password').value;
  const errorDiv = document.getElementById('error');
  
  try {
    const response = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    
    if (!response.ok) {
      throw new Error('Неверный email или пароль');
    }
    
    const data = await response.json();
    
    // Сохраняем токен
    await chrome.storage.local.set({ token: data.token });
    
    showMainView();
    await loadAssistants(data.token);
    
  } catch (error) {
    errorDiv.textContent = error.message;
  }
});

// Загрузить ассистентов
async function loadAssistants(token) {
  try {
    const response = await fetch(`${API_URL}/assistants/`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    const assistants = await response.json();
    
    const select = document.getElementById('assistantSelect');
    select.innerHTML = assistants.map(a => 
      `<option value="${a.id}">${a.name}</option>`
    ).join('');
    
    // Восстанавливаем выбор
    const saved = await chrome.storage.local.get(['selectedAssistant']);
    if (saved.selectedAssistant) {
      select.value = saved.selectedAssistant;
    }
    
  } catch (error) {
    console.error('Ошибка загрузки ассистентов:', error);
  }
}

// Открыть боковую панель
document.getElementById('openSidePanel').addEventListener('click', async () => {
  const assistantId = document.getElementById('assistantSelect').value;
  
  if (!assistantId) {
    alert('Выберите ассистента');
    return;
  }
  
  // Сохраняем выбор
  await chrome.storage.local.set({ selectedAssistant: assistantId });
  
  // Открываем side panel
  await chrome.sidePanel.open({ windowId: (await chrome.windows.getCurrent()).id });
  
  // Закрываем popup
  window.close();
});

// Выйти
document.getElementById('logoutBtn').addEventListener('click', async () => {
  await chrome.storage.local.clear();
  showLoginForm();
});

// Вспомогательные функции
async function getToken() {
  const data = await chrome.storage.local.get(['token']);
  return data.token;
}

function showLoginForm() {
  document.getElementById('loginForm').style.display = 'block';
  document.getElementById('mainView').style.display = 'none';
}

function showMainView() {
  document.getElementById('loginForm').style.display = 'none';
  document.getElementById('mainView').style.display = 'block';
}
