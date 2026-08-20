// /static/agents/api.js

// ============================================================================
// API MODULE - Работа с бэкендом
// ============================================================================

const api = {
  baseUrl: '/api',
  
  // ============================================================================
  // ТОКЕН И АВТОРИЗАЦИЯ
  // ============================================================================
  
  /**
   * Получение токена авторизации из localStorage
   * @returns {string|null} Токен авторизации или null
   */
  getToken() {
    return localStorage.getItem('auth_token');
  },
  
  /**
   * Проверка авторизации пользователя
   * @returns {boolean} true если пользователь авторизован
   */
  isAuthenticated() {
    return this.getToken() !== null;
  },
  
  /**
   * Проверка авторизации с редиректом
   * @returns {boolean} true если пользователь авторизован
   */
  checkAuth() {
    if (!this.isAuthenticated()) {
      console.log('[AUTH] Пользователь не авторизован, перенаправление на страницу входа');
      window.location.href = '/static/login.html';
      return false;
    }
    console.log('[AUTH] Пользователь авторизован');
    return true;
  },
  
  // ============================================================================
  // БАЗОВАЯ ФУНКЦИЯ ДЛЯ ЗАПРОСОВ
  // ============================================================================
  
  /**
   * Базовая функция для выполнения HTTP запросов к API
   * @param {string} endpoint - Путь к эндпоинту API
   * @param {object} options - Опции для fetch запроса
   * @returns {Promise<any>} Промис с данными ответа
   * @throws {Error} Ошибка при выполнении запроса
   */
  async fetch(endpoint, options = {}) {
    // Добавляем токен авторизации если пользователь авторизован
    if (this.isAuthenticated()) {
      options.headers = {
        ...options.headers,
        'Authorization': `Bearer ${this.getToken()}`
      };
    }
    
    // Обработка JSON body
    if (options.body && typeof options.body !== 'string' && !(options.body instanceof FormData)) {
      options.headers = {
        ...options.headers,
        'Content-Type': 'application/json'
      };
      options.body = JSON.stringify(options.body);
    }
    
    try {
      console.log(`[API] ${options.method || 'GET'} ${endpoint}`);
      const response = await fetch(`${this.baseUrl}${endpoint}`, options);
      
      // Обработка ошибки авторизации
      if (response.status === 401) {
        localStorage.removeItem('auth_token');
        window.location.href = '/static/login.html';
        throw new Error('Требуется авторизация');
      }
      
      // Для файлов без JSON ответа (например, скачивание)
      if (options.method === 'GET' && endpoint.includes('/download/')) {
        return response;
      }
      
      // Парсинг JSON ответа
      const responseText = await response.text();
      let data;
      
      try {
        data = JSON.parse(responseText);
      } catch (e) {
        console.error(`[API] Ошибка парсинга JSON: ${e.message}`);
        console.error(`[API] Текст ответа: ${responseText.substring(0, 200)}...`);
        throw new Error('Некорректный формат ответа от сервера');
      }
      
      // Обработка ошибок HTTP
      if (!response.ok) {
        console.error(`[API] Ошибка ${response.status}: ${data.detail || 'Нет деталей'}`);
        throw new Error(data.detail || 'Произошла ошибка при выполнении запроса');
      }
      
      console.log(`[API] Успешный ответ от ${endpoint}`);
      return data;
      
    } catch (error) {
      console.error(`[API] Ошибка запроса: ${error.message}`);
      throw error;
    }
  },
  
  // ============================================================================
  // HTTP МЕТОДЫ
  // ============================================================================
  
  /**
   * GET запрос
   * @param {string} endpoint - Путь к эндпоинту
   * @returns {Promise<any>} Промис с данными
   */
  get(endpoint) {
    return this.fetch(endpoint, { method: 'GET' });
  },
  
  /**
   * POST запрос
   * @param {string} endpoint - Путь к эндпоинту
   * @param {object|FormData} data - Данные для отправки
   * @returns {Promise<any>} Промис с данными
   */
  post(endpoint, data) {
    return this.fetch(endpoint, { method: 'POST', body: data });
  },
  
  /**
   * PUT запрос
   * @param {string} endpoint - Путь к эндпоинту
   * @param {object|FormData} data - Данные для отправки
   * @returns {Promise<any>} Промис с данными
   */
  put(endpoint, data) {
    return this.fetch(endpoint, { method: 'PUT', body: data });
  },
  
  /**
   * DELETE запрос
   * @param {string} endpoint - Путь к эндпоинту
   * @returns {Promise<any>} Промис с данными
   */
  delete(endpoint) {
    return this.fetch(endpoint, { method: 'DELETE' });
  },
  
  /**
   * PATCH запрос
   * @param {string} endpoint - Путь к эндпоинту
   * @param {object|FormData} data - Данные для отправки
   * @returns {Promise<any>} Промис с данными
   */
  patch(endpoint, data) {
    return this.fetch(endpoint, { method: 'PATCH', body: data });
  },
  
  // ============================================================================
  // ПОЛЬЗОВАТЕЛИ
  // ============================================================================
  
  /**
   * Получение информации о текущем пользователе
   * @returns {Promise<object>} Данные пользователя
   */
  getCurrentUser() {
    console.log('[API] Запрос информации о текущем пользователе');
    return this.get('/users/me');
  },
  
  /**
   * Обновление профиля пользователя
   * @param {object} data - Данные для обновления
   * @returns {Promise<object>} Обновленные данные пользователя
   */
  updateUserProfile(data) {
    console.log('[API] Обновление профиля пользователя');
    return this.put('/users/me', data);
  },
  
  /**
   * Изменение пароля
   * @param {string} oldPassword - Старый пароль
   * @param {string} newPassword - Новый пароль
   * @returns {Promise<object>} Результат операции
   */
  changePassword(oldPassword, newPassword) {
    console.log('[API] Изменение пароля');
    return this.post('/users/change-password', {
      old_password: oldPassword,
      new_password: newPassword
    });
  },
  
  // ============================================================================
  // ПОДПИСКИ (НОВОЕ)
  // ============================================================================
  
  /**
   * Получение информации о подписке текущего пользователя
   * @returns {Promise<object>} Информация о подписке
   */
  getSubscription() {
    console.log('[API] Запрос информации о подписке');
    return this.get('/subscriptions/my-subscription');
  },

  /**
   * Расход лимита ассистентов по всем провайдерам сразу
   * (OpenAI, Gemini, Grok, Cascade, Cartesia, Yandex, Translate)
   */
  getAssistantsUsage() {
    console.log('[API] Запрос расхода лимита ассистентов');
    return this.get('/subscriptions/assistants-usage');
  },
  
  // ============================================================================
  // АГЕНТЫ (АССИСТЕНТЫ) - OpenAI
  // ============================================================================
  
  /**
   * Получение списка всех OpenAI агентов пользователя
   * @returns {Promise<Array>} Массив агентов
   */
  getAssistants() {
    console.log('[API] Запрос списка OpenAI агентов');
    return this.get('/assistants');
  },
  
  /**
   * Получение данных конкретного агента по ID
   * @param {string} id - ID агента
   * @returns {Promise<object>} Данные агента
   */
  getAgent(id) {
    console.log(`[API] Запрос агента по ID: ${id}`);
    return this.get(`/assistants/${id}`);
  },
  
  /**
   * Создание нового агента
   * @param {object} data - Данные нового агента
   * @param {string} data.name - Название агента
   * @param {string} data.description - Описание агента
   * @param {string} data.system_prompt - Системный промпт
   * @param {string} data.voice - Голос агента
   * @param {string} data.greeting_message - Приветственное сообщение
   * @param {string|null} data.google_sheet_id - ID Google таблицы для логирования
   * @param {Array} data.functions - Массив включенных функций
   * @returns {Promise<object>} Созданный агент
   */
  createAssistant(data) {
    console.log('[API] Создание нового агента');
    console.log('[API] Данные агента:', JSON.stringify(data, null, 2));
    return this.post('/assistants', data);
  },
  
  /**
   * Обновление существующего агента
   * @param {string} id - ID агента
   * @param {object} data - Данные для обновления
   * @returns {Promise<object>} Обновленный агент
   */
  updateAgent(id, data) {
    console.log(`[API] Обновление агента ${id}`);
    console.log('[API] Данные для обновления:', JSON.stringify(data, null, 2));
    return this.put(`/assistants/${id}`, data);
  },
  
  /**
   * Удаление агента
   * @param {string} id - ID агента
   * @returns {Promise<object>} Результат удаления
   */
  deleteAgent(id) {
    console.log(`[API] Удаление агента ${id}`);
    return this.delete(`/assistants/${id}`);
  },
  
  /**
   * Получение кода для встраивания виджета
   * @param {string} id - ID агента
   * @returns {Promise<object>} Объект с embed_code
   */
  getEmbedCode(id) {
    console.log(`[API] Получение кода встраивания для агента ${id}`);
    return this.get(`/assistants/${id}/embed-code`);
  },
  
  /**
   * Клонирование агента
   * @param {string} id - ID агента для клонирования
   * @returns {Promise<object>} Новый клонированный агент
   */
  cloneAgent(id) {
    console.log(`[API] Клонирование агента ${id}`);
    return this.post(`/assistants/${id}/clone`, {});
  },
  
  // ============================================================================
  // АГЕНТЫ (АССИСТЕНТЫ) - Gemini (НОВОЕ)
  // ============================================================================
  
  /**
   * Получение списка всех Gemini агентов пользователя
   * @returns {Promise<Array>} Массив Gemini агентов
   */
  getGeminiAssistants() {
    console.log('[API] Запрос списка Gemini агентов');
    return this.get('/gemini-assistants');
  },
  
  /**
   * Получение данных конкретного Gemini агента по ID
   * @param {string} id - ID агента
   * @returns {Promise<object>} Данные агента
   */
  getGeminiAgent(id) {
    console.log(`[API] Запрос Gemini агента по ID: ${id}`);
    return this.get(`/gemini-assistants/${id}`);
  },
  
  /**
   * Создание нового Gemini агента
   * @param {object} data - Данные нового агента
   * @returns {Promise<object>} Созданный агент
   */
  createGeminiAssistant(data) {
    console.log('[API] Создание нового Gemini агента');
    console.log('[API] Данные агента:', JSON.stringify(data, null, 2));
    return this.post('/gemini-assistants', data);
  },
  
  /**
   * Обновление существующего Gemini агента
   * @param {string} id - ID агента
   * @param {object} data - Данные для обновления
   * @returns {Promise<object>} Обновленный агент
   */
  updateGeminiAgent(id, data) {
    console.log(`[API] Обновление Gemini агента ${id}`);
    return this.put(`/gemini-assistants/${id}`, data);
  },
  
  /**
   * Удаление Gemini агента
   * @param {string} id - ID агента
   * @returns {Promise<object>} Результат удаления
   */
  deleteGeminiAgent(id) {
    console.log(`[API] Удаление Gemini агента ${id}`);
    return this.delete(`/gemini-assistants/${id}`);
  },
  
  /**
   * Получение кода для встраивания виджета Gemini
   * @param {string} id - ID агента
   * @returns {Promise<object>} Объект с embed_code
   */
  getGeminiEmbedCode(id) {
    console.log(`[API] Получение кода встраивания для Gemini агента ${id}`);
    return this.get(`/gemini-assistants/${id}/embed-code`);
  },

  // ============================================================================
  // 🆕 v1.0: ПЕРЕВОДЧИК (Translate Assistants)
  // ============================================================================

  getTranslateAssistants() { return this.get('/translate-assistants'); },
  getTranslateAssistant(id) { return this.get(`/translate-assistants/${id}`); },
  createTranslateAssistant(data) { return this.post('/translate-assistants', data); },
  updateTranslateAssistant(id, data) { return this.put(`/translate-assistants/${id}`, data); },
  deleteTranslateAssistant(id) { return this.delete(`/translate-assistants/${id}`); },
  getTranslateEmbedCode(id) { return this.get(`/translate-assistants/${id}/embed-code`); },

  // ============================================================================
  // ФУНКЦИИ
  // ============================================================================
  
  /**
   * Получение списка доступных функций для агентов
   * @returns {Promise<Array>} Массив функций с описанием и параметрами
   */
  getFunctions() {
    console.log('[API] Получение списка доступных функций');
    return this.get('/functions/');
  },
  
  // ============================================================================
  // GOOGLE SHEETS
  // ============================================================================
  
  /**
   * Проверка подключения к Google таблице
   * @param {string} assistantId - ID ассистента
   * @param {string} sheetId - ID Google таблицы
   * @returns {Promise<object>} Результат проверки с полями success и message
   */
  verifyGoogleSheet(assistantId, sheetId) {
    console.log(`[API] Проверка подключения к Google таблице ${sheetId}`);
    return this.post(`/assistants/${assistantId}/verify-sheet`, { 
      sheet_id: sheetId 
    });
  },
  
  /**
   * Получение данных из Google таблицы
   * @param {string} sheetId - ID Google таблицы
   * @param {string} range - Диапазон ячеек (например, 'Sheet1!A1:D10')
   * @returns {Promise<object>} Данные из таблицы
   */
  getSheetData(sheetId, range) {
    console.log(`[API] Получение данных из Google таблицы ${sheetId}, диапазон: ${range}`);
    return this.get(`/sheets/${sheetId}/data?range=${encodeURIComponent(range)}`);
  },
  
  /**
   * Добавление строки в Google таблицу
   * @param {string} sheetId - ID Google таблицы
   * @param {Array} values - Массив значений для новой строки
   * @param {string} sheetName - Название листа (опционально)
   * @returns {Promise<object>} Результат операции
   */
  appendSheetRow(sheetId, values, sheetName = null) {
    console.log(`[API] Добавление строки в Google таблицу ${sheetId}`);
    return this.post(`/sheets/${sheetId}/append`, {
      values: values,
      sheet_name: sheetName
    });
  },
  
  // ============================================================================
  // СТАТИСТИКА И АНАЛИТИКА
  // ============================================================================
  
  /**
   * Получение статистики по агенту
   * @param {string} id - ID агента
   * @param {string} period - Период (day, week, month, all)
   * @returns {Promise<object>} Статистика
   */
  getAgentStats(id, period = 'week') {
    console.log(`[API] Получение статистики для агента ${id}, период: ${period}`);
    return this.get(`/assistants/${id}/stats?period=${period}`);
  },
  
  /**
   * Получение истории диалогов
   * @param {string} id - ID агента
   * @param {number} limit - Лимит записей
   * @param {number} offset - Смещение для пагинации
   * @returns {Promise<object>} История диалогов
   */
  getConversationHistory(id, limit = 50, offset = 0) {
    console.log(`[API] Получение истории диалогов для агента ${id}`);
    return this.get(`/assistants/${id}/conversations?limit=${limit}&offset=${offset}`);
  },
  
  /**
   * Получение общей статистики пользователя
   * @returns {Promise<object>} Общая статистика
   */
  getDashboardStats() {
    console.log('[API] Получение статистики дашборда');
    return this.get('/stats/dashboard');
  },
  
  // ============================================================================
  // БАЗА ЗНАНИЙ
  // ============================================================================
  
  /**
   * Получение списка документов базы знаний
   * @returns {Promise<Array>} Массив документов
   */
  getKnowledgeBase() {
    console.log('[API] Получение списка документов базы знаний');
    return this.get('/knowledge-base');
  },
  
  /**
   * Загрузка документа в базу знаний
   * @param {FormData} formData - Форма с файлом
   * @returns {Promise<object>} Загруженный документ
   */
  uploadDocument(formData) {
    console.log('[API] Загрузка документа в базу знаний');
    return this.post('/knowledge-base/upload', formData);
  },
  
  /**
   * Удаление документа из базы знаний
   * @param {string} id - ID документа
   * @returns {Promise<object>} Результат удаления
   */
  deleteDocument(id) {
    console.log(`[API] Удаление документа ${id} из базы знаний`);
    return this.delete(`/knowledge-base/${id}`);
  },
  
  /**
   * Поиск в базе знаний
   * @param {string} query - Поисковый запрос
   * @param {number} limit - Лимит результатов
   * @returns {Promise<object>} Результаты поиска
   */
  searchKnowledgeBase(query, limit = 10) {
    console.log(`[API] Поиск в базе знаний: ${query}`);
    return this.post('/knowledge-base/search', {
      query: query,
      limit: limit
    });
  },
  
  // ============================================================================
  // WEBHOOKS И ИНТЕГРАЦИИ
  // ============================================================================
  
  /**
   * Получение списка вебхуков
   * @param {string} assistantId - ID ассистента
   * @returns {Promise<Array>} Массив вебхуков
   */
  getWebhooks(assistantId) {
    console.log(`[API] Получение списка вебхуков для агента ${assistantId}`);
    return this.get(`/assistants/${assistantId}/webhooks`);
  },
  
  /**
   * Создание нового вебхука
   * @param {string} assistantId - ID ассистента
   * @param {object} data - Данные вебхука
   * @returns {Promise<object>} Созданный вебхук
   */
  createWebhook(assistantId, data) {
    console.log(`[API] Создание вебхука для агента ${assistantId}`);
    return this.post(`/assistants/${assistantId}/webhooks`, data);
  },
  
  /**
   * Удаление вебхука
   * @param {string} assistantId - ID ассистента
   * @param {string} webhookId - ID вебхука
   * @returns {Promise<object>} Результат удаления
   */
  deleteWebhook(assistantId, webhookId) {
    console.log(`[API] Удаление вебхука ${webhookId}`);
    return this.delete(`/assistants/${assistantId}/webhooks/${webhookId}`);
  },
  
  /**
   * Тестирование вебхука
   * @param {string} assistantId - ID ассистента
   * @param {string} webhookId - ID вебхука
   * @returns {Promise<object>} Результат теста
   */
  testWebhook(assistantId, webhookId) {
    console.log(`[API] Тестирование вебхука ${webhookId}`);
    return this.post(`/assistants/${assistantId}/webhooks/${webhookId}/test`, {});
  },
  
  // ============================================================================
  // PINECONE
  // ============================================================================
  
  /**
   * Получение списка namespace'ов в Pinecone
   * @returns {Promise<Array>} Массив namespace'ов
   */
  getPineconeNamespaces() {
    console.log('[API] Получение списка Pinecone namespace\'ов');
    return this.get('/pinecone/namespaces');
  },
  
  /**
   * Поиск в Pinecone
   * @param {string} namespace - Namespace для поиска
   * @param {string} query - Поисковый запрос
   * @param {number} topK - Количество результатов
   * @returns {Promise<object>} Результаты поиска
   */
  searchPinecone(namespace, query, topK = 5) {
    console.log(`[API] Поиск в Pinecone namespace: ${namespace}`);
    return this.post('/pinecone/search', {
      namespace: namespace,
      query: query,
      top_k: topK
    });
  },
  
  // ============================================================================
  // АДМИНИСТРИРОВАНИЕ (только для well96well@gmail.com)
  // ============================================================================
  
  /**
   * Получение списка всех пользователей (admin)
   * @returns {Promise<Array>} Массив пользователей
   */
  getAllUsers() {
    console.log('[API] Получение списка всех пользователей (admin)');
    return this.get('/admin/users');
  },
  
  /**
   * Получение пользователя по ID (admin)
   * @param {string} userId - ID пользователя
   * @returns {Promise<object>} Данные пользователя
   */
  getUserById(userId) {
    console.log(`[API] Получение пользователя ${userId} (admin)`);
    return this.get(`/admin/users/${userId}`);
  },
  
  /**
   * Обновление пользователя (admin)
   * @param {string} userId - ID пользователя
   * @param {object} data - Данные для обновления
   * @returns {Promise<object>} Обновленный пользователь
   */
  updateUser(userId, data) {
    console.log(`[API] Обновление пользователя ${userId} (admin)`);
    return this.put(`/admin/users/${userId}`, data);
  },
  
  /**
   * Удаление пользователя (admin)
   * @param {string} userId - ID пользователя
   * @returns {Promise<object>} Результат удаления
   */
  deleteUser(userId) {
    console.log(`[API] Удаление пользователя ${userId} (admin)`);
    return this.delete(`/admin/users/${userId}`);
  },
  
  /**
   * Получение логов системы (admin)
   * @param {number} limit - Лимит записей
   * @param {string} level - Уровень логов (info, warning, error)
   * @returns {Promise<Array>} Массив логов
   */
  getSystemLogs(limit = 100, level = 'all') {
    console.log(`[API] Получение системных логов (admin), лимит: ${limit}, уровень: ${level}`);
    return this.get(`/admin/logs?limit=${limit}&level=${level}`);
  },
  
  /**
   * Получение общей статистики платформы (admin)
   * @returns {Promise<object>} Статистика платформы
   */
  getPlatformStats() {
    console.log('[API] Получение статистики платформы (admin)');
    return this.get('/admin/stats');
  },
  
  // ============================================================================
  // БИЛЛИНГ И ПОДПИСКИ
  // ============================================================================
  
  /**
   * Получение истории платежей
   * @returns {Promise<Array>} История платежей
   */
  getPaymentHistory() {
    console.log('[API] Получение истории платежей');
    return this.get('/billing/payments');
  },
  
  /**
   * Обновление платежного метода
   * @param {object} data - Данные платежного метода
   * @returns {Promise<object>} Результат обновления
   */
  updatePaymentMethod(data) {
    console.log('[API] Обновление платежного метода');
    return this.post('/billing/payment-method', data);
  },
  
  /**
   * Отмена подписки
   * @returns {Promise<object>} Результат отмены
   */
  cancelSubscription() {
    console.log('[API] Отмена подписки');
    return this.post('/billing/subscription/cancel', {});
  },
  
  // ============================================================================
  // НАСТРОЙКИ
  // ============================================================================
  
  /**
   * Получение настроек приложения
   * @returns {Promise<object>} Настройки
   */
  getSettings() {
    console.log('[API] Получение настроек');
    return this.get('/settings');
  },
  
  /**
   * Обновление настроек
   * @param {object} data - Новые настройки
   * @returns {Promise<object>} Обновленные настройки
   */
  updateSettings(data) {
    console.log('[API] Обновление настроек');
    return this.put('/settings', data);
  },
  
  // ============================================================================
  // ЭКСПОРТ ДАННЫХ
  // ============================================================================
  
  /**
   * Экспорт данных агента
   * @param {string} id - ID агента
   * @param {string} format - Формат экспорта (json, csv, xlsx)
   * @returns {Promise<Blob>} Файл с данными
   */
  async exportAgentData(id, format = 'json') {
    console.log(`[API] Экспорт данных агента ${id} в формате ${format}`);
    const response = await fetch(`${this.baseUrl}/assistants/${id}/export?format=${format}`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${this.getToken()}`
      }
    });
    
    if (!response.ok) {
      throw new Error('Ошибка при экспорте данных');
    }
    
    return await response.blob();
  },
  
  /**
   * Экспорт всех данных пользователя
   * @returns {Promise<Blob>} Архив с данными
   */
  async exportAllData() {
    console.log('[API] Экспорт всех данных пользователя');
    const response = await fetch(`${this.baseUrl}/users/me/export`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${this.getToken()}`
      }
    });
    
    if (!response.ok) {
      throw new Error('Ошибка при экспорте данных');
    }
    
    return await response.blob();
  }
};

// ============================================================================
// ЭКСПОРТ
// ============================================================================

export { api };
export default api;
