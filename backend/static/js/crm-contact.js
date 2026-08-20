// backend/static/js/crm-contact.js
/**
 * Contact Detail Page для Voicyfy CRM
 * Детальный просмотр контакта с историей диалогов, заметками и задачами
 * Version: 5.0 - CALL COST & RECORDING SUPPORT
 * ✅ v4.0: Исправлено раскрытие диалогов (session_id вместо id)
 * ✅ v4.0: Добавлено удаление задач для всех статусов
 * ✅ v4.0: Компактный аватар с инициалами
 * ✅ v5.0: Отображение стоимости звонка (call_cost)
 * ✅ v5.0: Аудиоплеер для записи звонка (record_url)
 * ✅ OpenAI + Gemini assistants support
 * ✅ Tasks with auto-calls
 * ✅ Notes feed
 * ✅ Conversations history with call direction (INBOUND/OUTBOUND)
 * ✅ Custom greeting support
 * ✅ Moscow timezone (МСК) support
 */

document.addEventListener('DOMContentLoaded', function() {
  // ==================== Timezone: Moscow (UTC+3) ====================
  const MSK_OFFSET_MS = 3 * 60 * 60 * 1000; // 3 часа в миллисекундах

  /**
   * МСК → UTC: вычитаем 3 часа
   * @param {string} dateString - "2025-12-06T15:00" из datetime-local input
   * @returns {string} ISO строка UTC
   */
  function mskToUtc(dateString) {
    const [datePart, timePart] = dateString.split('T');
    const [year, month, day] = datePart.split('-').map(Number);
    const [hours, minutes] = timePart.split(':').map(Number);
    
    const inputAsUtcMs = Date.UTC(year, month - 1, day, hours, minutes, 0, 0);
    const realUtcMs = inputAsUtcMs - MSK_OFFSET_MS;
    
    return new Date(realUtcMs).toISOString();
  }

  /**
   * UTC → МСК: добавляем 3 часа
   * @param {string} isoString - ISO строка UTC
   * @returns {Date} Date объект с МСК временем
   */
  function utcToMsk(isoString) {
    if (!isoString) return null;
    const utcMs = new Date(isoString).getTime();
    return new Date(utcMs + MSK_OFFSET_MS);
  }

  /**
   * Текущее время в МСК
   * @returns {Date} Date объект с текущим московским временем
   */
  function getMskNow() {
    return new Date(Date.now() + MSK_OFFSET_MS);
  }

  /**
   * Форматирование UTC даты в строку для datetime-local input (показываем МСК)
   * @param {string} isoString - ISO строка в UTC
   * @returns {string} "2025-12-06T15:00" для input
   */
  function formatDatetimeLocalMsk(isoString) {
    const mskDate = utcToMsk(isoString);
    if (!mskDate) return '';
    
    const year = mskDate.getUTCFullYear();
    const month = String(mskDate.getUTCMonth() + 1).padStart(2, '0');
    const day = String(mskDate.getUTCDate()).padStart(2, '0');
    const hours = String(mskDate.getUTCHours()).padStart(2, '0');
    const minutes = String(mskDate.getUTCMinutes()).padStart(2, '0');
    
    return `${year}-${month}-${day}T${hours}:${minutes}`;
  }

  // ==================== Элементы ====================
  const contactAvatar = document.getElementById('contact-avatar');
  const nameInput = document.getElementById('name-input');
  const saveNameBtn = document.getElementById('save-name-btn');
  const contactPhone = document.getElementById('contact-phone');
  const statusDropdown = document.getElementById('status-dropdown');
  
  // Notes
  const notesFeed = document.getElementById('notes-feed');
  const notesCount = document.getElementById('notes-count');
  const noteInput = document.getElementById('note-input');
  const addNoteBtn = document.getElementById('add-note-btn');
  
  // Timeline (unified)
  const timelineContainer = document.getElementById('timeline-container');
  const createTaskBtn = document.getElementById('create-task-btn');
  
  // Task Create Modal
  const taskCreateModal = document.getElementById('task-create-modal');
  const taskCreateForm = document.getElementById('task-create-form');
  const taskCreateAssistant = document.getElementById('task-create-assistant');
  const taskCreateDatetime = document.getElementById('task-create-datetime');
  const taskCreateTitle = document.getElementById('task-create-title');
  const taskCreateDescription = document.getElementById('task-create-description');
  const taskCreateGreeting = document.getElementById('task-create-greeting');
  const taskCreateCancelBtn = document.getElementById('task-create-cancel-btn');
  
  // Task View/Edit Modal
  const taskViewModal = document.getElementById('task-view-modal');
  const taskViewMode = document.getElementById('task-view-mode');
  const taskEditForm = document.getElementById('task-edit-form');
  const taskViewCloseBtn = document.getElementById('task-view-close-btn');
  const taskViewEditBtn = document.getElementById('task-view-edit-btn');
  const taskViewDeleteBtn = document.getElementById('task-view-delete-btn');
  const taskEditCancelBtn = document.getElementById('task-edit-cancel-btn');
  const taskModalTitle = document.getElementById('task-modal-title');
  
  // Task View Mode Elements
  const taskViewStatus = document.getElementById('task-view-status');
  const taskViewTitle = document.getElementById('task-view-title');
  const taskViewDatetime = document.getElementById('task-view-datetime');
  const taskViewAssistant = document.getElementById('task-view-assistant');
  const taskViewDescription = document.getElementById('task-view-description');
  const taskViewGreeting = document.getElementById('task-view-greeting');
  const taskViewDescriptionSection = document.getElementById('task-view-description-section');
  const taskViewGreetingSection = document.getElementById('task-view-greeting-section');
  
  // Task Edit Mode Elements
  const taskEditAssistant = document.getElementById('task-edit-assistant');
  const taskEditDatetime = document.getElementById('task-edit-datetime');
  const taskEditTitle = document.getElementById('task-edit-title');
  const taskEditDescription = document.getElementById('task-edit-description');
  const taskEditGreeting = document.getElementById('task-edit-greeting');

  // Caller ID selects
  const taskCreateCallerId = document.getElementById('task-create-caller-id');
  const taskEditCallerId = document.getElementById('task-edit-caller-id');

  // Delete Task Modal
  const deleteTaskModal = document.getElementById('delete-task-modal');
  const deleteTaskText = document.getElementById('delete-task-text');
  const cancelDeleteTaskBtn = document.getElementById('cancel-delete-task-btn');
  const confirmDeleteTaskBtn = document.getElementById('confirm-delete-task-btn');
  
  // Delete Contact
  const deleteContactBtn = document.getElementById('delete-contact-btn');
  const deleteModal = document.getElementById('delete-modal');
  const cancelDeleteBtn = document.getElementById('cancel-delete-btn');
  const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
  const deleteConversationsCount = document.getElementById('delete-conversations-count');
  
  // Notification
  const notification = document.getElementById('notification');
  const notificationMessage = document.getElementById('notification-message');
  const notificationClose = document.getElementById('notification-close');
  const loadingOverlay = document.getElementById('loading-overlay');
  
  // ==================== Состояние ====================
  let currentContact = null;
  let contactId = null;
  let notes = [];
  let timelineItems = []; // Unified: tasks + conversations
  let assistants = [];
  let phoneNumbers = []; // Доступные номера для caller_id
  let currentTaskId = null;
  let currentTaskData = null;
  let taskToDeleteId = null;
  
  // ==================== API ====================
  const api = {
    baseUrl: '/api',
    
    getToken() {
      return localStorage.getItem('auth_token');
    },
    
    isAuthenticated() {
      return this.getToken() !== null;
    },
    
    async fetch(endpoint, options = {}) {
      if (this.isAuthenticated()) {
        options.headers = {
          ...options.headers,
          'Authorization': `Bearer ${this.getToken()}`
        };
      }
      
      if (options.body && typeof options.body !== 'string') {
        options.headers = {
          ...options.headers,
          'Content-Type': 'application/json'
        };
        options.body = JSON.stringify(options.body);
      }
      
      const response = await fetch(`${this.baseUrl}${endpoint}`, options);
      
      if (response.status === 401) {
        localStorage.removeItem('auth_token');
        window.location.href = '/static/login.html';
        throw new Error('Требуется авторизация');
      }
      
      const data = await response.json();
      
      if (!response.ok) {
        throw new Error(data.detail || 'Произошла ошибка');
      }
      
      return data;
    },
    
    get(endpoint) {
      return this.fetch(endpoint, { method: 'GET' });
    },
    
    post(endpoint, body) {
      return this.fetch(endpoint, { method: 'POST', body });
    },
    
    put(endpoint, body) {
      return this.fetch(endpoint, { method: 'PUT', body });
    },
    
    patch(endpoint, body) {
      return this.fetch(endpoint, { method: 'PATCH', body });
    },
    
    delete(endpoint) {
      return this.fetch(endpoint, { method: 'DELETE' });
    }
  };
  
  // ==================== UI Functions ====================
  function setLoading(loading) {
    if (loading) {
      loadingOverlay.classList.add('show');
    } else {
      loadingOverlay.classList.remove('show');
    }
  }
  
  function showNotification(message, type = 'success') {
    notification.classList.remove('notification-success', 'notification-error');
    notification.classList.add(`notification-${type}`);
    
    const iconElement = notification.querySelector('.notification-icon i');
    iconElement.className = type === 'success' ? 'fas fa-check-circle' : 'fas fa-exclamation-circle';
    
    notificationMessage.textContent = message;
    notification.classList.add('show');
    
    setTimeout(() => {
      hideNotification();
    }, 5000);
  }
  
  function hideNotification() {
    notification.classList.remove('show');
  }
  
  /**
   * Форматирование даты для заметок и диалогов
   */
  function formatDate(dateString) {
    if (!dateString) return 'Нет данных';
    
    const date = utcToMsk(dateString);
    const mskNow = getMskNow();
    
    const diff = mskNow - date;
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    
    const timeStr = date.toLocaleTimeString('ru-RU', { 
      hour: '2-digit', 
      minute: '2-digit',
      timeZone: 'UTC'
    });
    
    if (days === 0) {
      return 'Сегодня ' + timeStr;
    } else if (days === 1) {
      return 'Вчера ' + timeStr;
    } else if (days < 7) {
      return days + ' дн. назад';
    } else {
      return date.toLocaleDateString('ru-RU', { 
        day: '2-digit', 
        month: 'long', 
        year: 'numeric',
        timeZone: 'UTC'
      }) + ' ' + timeStr;
    }
  }
  
  /**
   * Форматирование даты и времени для задач (с указанием МСК)
   */
  function formatDateTime(dateString) {
    if (!dateString) return '';
    
    const date = utcToMsk(dateString);
    const mskNow = getMskNow();
    
    const tomorrow = new Date(mskNow);
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    
    const timeStr = date.toLocaleTimeString('ru-RU', { 
      hour: '2-digit', 
      minute: '2-digit',
      timeZone: 'UTC'
    });
    
    const dateOnly = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    const todayOnly = new Date(Date.UTC(mskNow.getUTCFullYear(), mskNow.getUTCMonth(), mskNow.getUTCDate()));
    const tomorrowOnly = new Date(Date.UTC(tomorrow.getUTCFullYear(), tomorrow.getUTCMonth(), tomorrow.getUTCDate()));
    
    if (dateOnly.getTime() === todayOnly.getTime()) {
      return 'Сегодня ' + timeStr + ' МСК';
    } else if (dateOnly.getTime() === tomorrowOnly.getTime()) {
      return 'Завтра ' + timeStr + ' МСК';
    } else {
      return date.toLocaleDateString('ru-RU', { 
        day: '2-digit', 
        month: 'long',
        timeZone: 'UTC'
      }) + ' ' + timeStr + ' МСК';
    }
  }
  
  function formatPhoneNumber(phone) {
    if (!phone) return '';
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.startsWith('7') && cleaned.length === 11) {
      return `+7 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7, 9)}-${cleaned.slice(9)}`;
    }
    return phone;
  }
  
  /**
   * 🆕 v5.0: Форматирование стоимости в рублях
   * @param {number|null} cost - стоимость
   * @returns {string|null} форматированная строка или null
   */
  function formatCost(cost) {
    if (cost === null || cost === undefined || cost === 0) return null;
    return cost.toFixed(2) + ' ₽';
  }
  
  /**
   * Получить HTML для аватара
   * ✅ v4.0: Всегда показываем иконку fa-user
   */
  function getAvatarContent() {
    return '<i class="fas fa-user"></i>';
  }
  
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  
  function getTaskStatusInfo(status) {
    const statusMap = {
      'scheduled': { icon: '📅', label: 'Запланирована' },
      'pending': { icon: '⏳', label: 'Ожидает' },
      'calling': { icon: '📞', label: 'Звонок...' },
      'completed': { icon: '✅', label: 'Выполнена' },
      'failed': { icon: '❌', label: 'Ошибка' },
      'cancelled': { icon: '🚫', label: 'Отменена' }
    };
    return statusMap[status] || { icon: '❓', label: status };
  }
  
  function getCallDirectionInfo(direction) {
    const normalizedDirection = direction ? direction.toLowerCase() : 'chat';
    
    const directionMap = {
      'inbound': { icon: '📞', label: 'Входящий звонок' },
      'outbound': { icon: '📱', label: 'Исходящий звонок' },
      'chat': { icon: '💬', label: 'Диалог' }
    };
    return directionMap[normalizedDirection] || { icon: '💬', label: 'Диалог' };
  }
  
  function getMessageEnding(count) {
    if (count % 10 === 1 && count % 100 !== 11) return 'е';
    if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return 'я';
    return 'й';
  }
  
  // ==================== Load Contact ====================
  async function loadContact() {
    try {
      setLoading(true);
      
      const contact = await api.get(`/contacts/${contactId}`);
      currentContact = contact;
      
      // Update left panel
      contactAvatar.innerHTML = getAvatarContent();
      nameInput.value = contact.name || '';
      nameInput.placeholder = contact.name ? 'Введите имя контакта...' : 'Без имени';
      contactPhone.textContent = formatPhoneNumber(contact.phone);
      statusDropdown.value = contact.status || 'new';
      
      // Load notes
      await loadNotes();
      
      // Load assistants (for task creation)
      await loadAssistants();

      // Load phone numbers (for caller_id selection)
      await loadPhoneNumbers();

      // Load unified timeline
      await loadTimeline();
      
    } catch (error) {
      console.error('Error loading contact:', error);
      showNotification(error.message || 'Ошибка загрузки контакта', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // ==================== Load Notes ====================
  async function loadNotes() {
    try {
      const data = await api.get(`/contacts/${contactId}/notes`);
      notes = data.notes || [];
      renderNotes();
    } catch (error) {
      console.error('Error loading notes:', error);
      showNotification('Ошибка загрузки заметок', 'error');
    }
  }
  
  // ==================== Render Notes ====================
  function renderNotes() {
    notesCount.textContent = notes.length;
    
    if (notes.length === 0) {
      notesFeed.innerHTML = `
        <div class="notes-empty">
          <i class="fas fa-sticky-note"></i>
          <p>Нет заметок</p>
        </div>
      `;
      return;
    }
    
    notesFeed.innerHTML = '';
    
    notes.forEach(note => {
      const noteCard = document.createElement('div');
      noteCard.className = 'note-card';
      noteCard.dataset.noteId = note.id;
      
      noteCard.innerHTML = `
        <div class="note-card-header">
          <span class="note-date">${formatDate(note.created_at)}</span>
          <button class="note-delete-btn" data-note-id="${note.id}" title="Удалить заметку">
            <i class="fas fa-trash-alt"></i>
          </button>
        </div>
        <div class="note-text">${escapeHtml(note.note_text)}</div>
      `;
      
      notesFeed.appendChild(noteCard);
      
      // Add delete listener
      const deleteBtn = noteCard.querySelector('.note-delete-btn');
      deleteBtn.addEventListener('click', () => deleteNote(note.id));
    });
  }
  
  // ==================== Add Note ====================
  async function addNote() {
    try {
      const noteText = noteInput.value.trim();
      
      if (!noteText) {
        showNotification('Введите текст заметки', 'error');
        return;
      }
      
      setLoading(true);
      
      await api.post(`/contacts/${contactId}/notes`, {
        note_text: noteText
      });
      
      noteInput.value = '';
      await loadNotes();
      
      showNotification('Заметка добавлена', 'success');
      
    } catch (error) {
      console.error('Error adding note:', error);
      showNotification(error.message || 'Ошибка добавления заметки', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // ==================== Delete Note ====================
  async function deleteNote(noteId) {
    try {
      if (!confirm('Удалить эту заметку?')) {
        return;
      }
      
      setLoading(true);
      
      await api.delete(`/contacts/notes/${noteId}`);
      await loadNotes();
      
      showNotification('Заметка удалена', 'success');
      
    } catch (error) {
      console.error('Error deleting note:', error);
      showNotification(error.message || 'Ошибка удаления заметки', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // ==================== Load Assistants (OpenAI + Gemini) ====================
  async function loadAssistants() {
    try {
      assistants = [];
      
      console.log('[ASSISTANTS] Loading assistants...');
      
      // include_agent_voices=true — здесь выбирают, кем позвонить контакту,
      // поэтому голоса агентов обзвона доступны (на страницах управления
      // ассистентами они скрыты).

      // 1. Загружаем OpenAI ассистентов
      try {
        const openaiData = await api.get('/assistants?include_agent_voices=true');
        const openaiList = Array.isArray(openaiData) ? openaiData : (openaiData.assistants || []);
        
        openaiList.forEach(a => {
          assistants.push({
            id: a.id,
            name: a.name,
            type: 'openai',
            displayName: a.name
          });
        });
        
        console.log(`[ASSISTANTS] ✅ Loaded ${openaiList.length} OpenAI assistants`);
      } catch (e) {
        console.warn('[ASSISTANTS] ⚠️ Error loading OpenAI assistants:', e.message);
      }
      
      // 2. Загружаем Gemini ассистентов
      try {
        const geminiData = await api.get('/gemini-assistants?include_agent_voices=true');
        const geminiList = Array.isArray(geminiData) ? geminiData : [];
        
        geminiList.forEach(a => {
          assistants.push({
            id: a.id,
            name: a.name,
            type: 'gemini',
            displayName: a.name
          });
        });
        
        console.log(`[ASSISTANTS] ✅ Loaded ${geminiList.length} Gemini assistants`);
      } catch (e) {
        console.warn('[ASSISTANTS] ⚠️ Error loading Gemini assistants:', e.message);
      }
      
      // 3. Загружаем Cartesia ассистентов
      try {
        const cartesiaData = await api.get('/cartesia-assistants?include_agent_voices=true');
        const cartesiaList = Array.isArray(cartesiaData) ? cartesiaData : (cartesiaData.assistants || []);

        cartesiaList.forEach(a => {
          assistants.push({
            id: a.id,
            name: a.name,
            type: 'cartesia',
            displayName: a.name
          });
        });

        console.log(`[ASSISTANTS] ✅ Loaded ${cartesiaList.length} Cartesia assistants`);
      } catch (e) {
        console.warn('[ASSISTANTS] ⚠️ Error loading Cartesia assistants:', e.message);
      }

      console.log(`[ASSISTANTS] 🎯 Total assistants: ${assistants.length}`);

      renderAssistantSelects();
      
    } catch (error) {
      console.error('[ASSISTANTS] ❌ Fatal error loading assistants:', error);
      assistants = [];
      renderAssistantSelects();
    }
  }
  
  function renderAssistantSelects() {
    [taskCreateAssistant, taskEditAssistant].forEach(selectElement => {
      selectElement.innerHTML = '';
      
      if (assistants.length === 0) {
        selectElement.innerHTML = '<option value="">Нет доступных ассистентов</option>';
        createTaskBtn.disabled = true;
        return;
      }
      
      createTaskBtn.disabled = false;
      
      const placeholderOption = document.createElement('option');
      placeholderOption.value = '';
      placeholderOption.textContent = 'Выберите ассистента...';
      selectElement.appendChild(placeholderOption);
      
      const openaiAssistants = assistants.filter(a => a.type === 'openai');
      const geminiAssistants = assistants.filter(a => a.type === 'gemini');
      const cartesiaAssistants = assistants.filter(a => a.type === 'cartesia');

      // OpenAI group
      if (openaiAssistants.length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '🤖 OpenAI Assistants';

        openaiAssistants.forEach(assistant => {
          const option = document.createElement('option');
          option.value = `${assistant.id}|openai`;
          option.textContent = assistant.name;
          optgroup.appendChild(option);
        });

        selectElement.appendChild(optgroup);
      }

      // Gemini group
      if (geminiAssistants.length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '✨ Gemini Assistants';

        geminiAssistants.forEach(assistant => {
          const option = document.createElement('option');
          option.value = `${assistant.id}|gemini`;
          option.textContent = assistant.name;
          optgroup.appendChild(option);
        });

        selectElement.appendChild(optgroup);
      }

      // Cartesia group
      if (cartesiaAssistants.length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '🎧 Cartesia Assistants';

        cartesiaAssistants.forEach(assistant => {
          const option = document.createElement('option');
          option.value = `${assistant.id}|cartesia`;
          option.textContent = assistant.name;
          optgroup.appendChild(option);
        });

        selectElement.appendChild(optgroup);
      }
    });
    
    console.log('[ASSISTANTS] ✅ Assistant selects rendered');
  }

  // ==================== Load Phone Numbers ====================
  async function loadPhoneNumbers() {
    try {
      const data = await api.get('/telephony/my-phone-numbers');
      phoneNumbers = data.phone_numbers || [];
      console.log(`[PHONE-NUMBERS] ✅ Loaded ${phoneNumbers.length} numbers`);
      renderPhoneNumberSelects();
    } catch (error) {
      console.warn('[PHONE-NUMBERS] ⚠️ Could not load phone numbers:', error.message);
      phoneNumbers = [];
      renderPhoneNumberSelects();
    }
  }

  function renderPhoneNumberSelects() {
    const selects = [taskCreateCallerId, taskEditCallerId].filter(Boolean);

    selects.forEach(select => {
      select.innerHTML = '';

      // Опция "Автоматически"
      const autoOption = document.createElement('option');
      autoOption.value = '';
      autoOption.textContent = '📞 Автоматически (первый активный)';
      select.appendChild(autoOption);

      if (phoneNumbers.length === 0) {
        return;
      }

      phoneNumbers.forEach(pn => {
        const option = document.createElement('option');
        option.value = pn.phone_number;

        const activeLabel = pn.is_active ? '' : ' ⚠️ Неактивен';
        const regionLabel = pn.phone_region ? ` (${pn.phone_region})` : '';
        option.textContent = `${formatPhoneNumber(pn.phone_number)}${regionLabel}${activeLabel}`;

        if (!pn.is_active) {
          option.disabled = true;
          option.style.color = '#999';
        }

        select.appendChild(option);
      });
    });
  }

  // ==================== Load Unified Timeline ====================
  async function loadTimeline() {
    try {
      // Load tasks
      const tasksResponse = await api.get(`/contacts/${contactId}/tasks`);
      const tasks = tasksResponse.tasks || [];
      
      // Load conversations
      const conversationsResponse = await api.get(`/contacts/${contactId}?include_conversations=true`);
      const conversations = conversationsResponse.conversations || [];
      
      console.log('[TIMELINE] Loaded:', tasks.length, 'tasks,', conversations.length, 'conversations');
      
      // ✅ v4.0: Используем session_id как уникальный идентификатор для conversations
      timelineItems = [
        ...tasks.map(task => ({ ...task, type: 'task', itemId: task.id })),
        ...conversations.map(conv => ({ 
          ...conv, 
          type: 'conversation', 
          itemId: conv.session_id, // ✅ FIX: Используем session_id
          expanded: false 
        }))
      ];
      
      // Sort by date (newest first)
      timelineItems.sort((a, b) => {
        const dateA = new Date(a.scheduled_time || a.created_at);
        const dateB = new Date(b.scheduled_time || b.created_at);
        return dateB - dateA;
      });
      
      renderTimeline();
      
      // Update conversations count for delete modal
      deleteConversationsCount.textContent = conversations.length;
      
    } catch (error) {
      console.error('Error loading timeline:', error);
      timelineContainer.innerHTML = `
        <div class="empty-state">
          <i class="fas fa-exclamation-circle"></i>
          <div>Ошибка загрузки истории</div>
        </div>
      `;
    }
  }
  
  // ==================== Render Unified Timeline ====================
  function renderTimeline() {
    if (timelineItems.length === 0) {
      timelineContainer.innerHTML = `
        <div class="empty-state">
          <i class="fas fa-clock"></i>
          <div>История взаимодействий пуста</div>
          <p style="margin-top: 0.5rem; font-size: 0.875rem;">Создайте первую задачу или начните диалог</p>
        </div>
      `;
      return;
    }
    
    timelineContainer.innerHTML = '';
    
    timelineItems.forEach((item, index) => {
      if (item.type === 'task') {
        timelineContainer.appendChild(renderTaskItem(item));
      } else {
        timelineContainer.appendChild(renderConversationItem(item, index));
      }
    });
  }
  
  // ==================== Render Task Item ====================
  function renderTaskItem(task) {
    const statusInfo = getTaskStatusInfo(task.status);
    
    const scheduledMsk = utcToMsk(task.scheduled_time);
    const mskNow = getMskNow();
    const isOverdue = scheduledMsk < mskNow && task.status === 'scheduled';
    
    const item = document.createElement('div');
    item.className = 'timeline-item task-item';
    item.dataset.taskId = task.id;
    
    item.innerHTML = `
      <div class="timeline-item-header">
        <div class="timeline-item-info">
          <div class="timeline-item-type">
            📅 ${escapeHtml(task.title)}
          </div>
          <div class="timeline-item-meta">
            <span><span class="task-status-badge status-${task.status}">${statusInfo.icon} ${statusInfo.label}</span></span>
            <span>${formatDateTime(task.scheduled_time)}${isOverdue ? ' <span style="color: var(--error-red); font-weight: 600;">⚠️ Просрочено</span>' : ''}</span>
            <span>${task.assistant_name || 'Ассистент не указан'}</span>
          </div>
        </div>
        <div class="timeline-item-toggle">
          <i class="fas fa-chevron-right"></i>
        </div>
      </div>
    `;
    
    // Click to open task view modal
    item.addEventListener('click', () => openTaskViewModal(task.id));
    
    return item;
  }
  
  // ==================== Render Conversation Item ====================
  function renderConversationItem(conversation, index) {
    const callDirection = getCallDirectionInfo(conversation.call_direction);
    const messageCount = conversation.messages_count || 0;
    
    const item = document.createElement('div');
    item.className = `timeline-item conversation-item ${conversation.expanded ? 'expanded' : ''}`;
    // ✅ v4.0 FIX: Используем session_id вместо id
    item.dataset.conversationId = conversation.session_id;
    item.dataset.index = index;
    
    item.innerHTML = `
      <div class="timeline-item-header">
        <div class="timeline-item-info">
          <div class="timeline-item-type">
            ${callDirection.icon} ${callDirection.label}
            ${conversation.task_id ? '<span style="font-size: 0.75rem; color: var(--text-light);">(по задаче)</span>' : ''}
          </div>
          <div class="timeline-item-meta">
            <span>${formatDate(conversation.created_at)}</span>
            <span>${messageCount} сообщени${getMessageEnding(messageCount)}</span>
          </div>
        </div>
        <div class="timeline-item-toggle">
          <i class="fas fa-chevron-down"></i>
        </div>
      </div>
      <div class="timeline-item-body">
        <div class="timeline-item-content" id="conversation-content-${index}">
          <div style="text-align: center; padding: 1rem; color: var(--text-light);">
            <i class="fas fa-spinner fa-spin"></i> Загрузка сообщений...
          </div>
        </div>
      </div>
    `;
    
    // Click to toggle conversation
    const header = item.querySelector('.timeline-item-header');
    header.addEventListener('click', (e) => {
      if (e.target.closest('button') || e.target.closest('a')) return;
      // ✅ v4.0 FIX: Передаём session_id
      toggleConversation(conversation.session_id, index);
    });
    
    return item;
  }
  
  // ==================== Toggle Conversation ====================
  async function toggleConversation(sessionId, index) {
    console.log('[TOGGLE] sessionId:', sessionId, 'index:', index);
    
    // ✅ v4.0 FIX: Ищем по itemId (который равен session_id для conversations)
    const item = timelineItems.find(i => i.type === 'conversation' && i.itemId === sessionId);
    if (!item) {
      console.error('[TOGGLE] Item not found for sessionId:', sessionId);
      return;
    }
    
    const element = document.querySelector(`[data-conversation-id="${sessionId}"]`);
    if (!element) {
      console.error('[TOGGLE] Element not found for sessionId:', sessionId);
      return;
    }
    
    const wasExpanded = item.expanded;
    
    // Close all conversations
    timelineItems.forEach(i => {
      if (i.type === 'conversation') {
        i.expanded = false;
      }
    });
    document.querySelectorAll('.conversation-item').forEach(el => {
      el.classList.remove('expanded');
    });
    
    // Toggle current
    if (!wasExpanded) {
      item.expanded = true;
      element.classList.add('expanded');
      
      // Load messages if not loaded
      const contentContainer = document.getElementById(`conversation-content-${index}`);
      if (contentContainer && contentContainer.querySelector('.fa-spinner')) {
        // ✅ v4.0 FIX: Используем session_id для загрузки
        await loadConversationMessages(item.session_id, contentContainer);
      }
    }
  }
  
  // ==================== Load Conversation Messages ====================
  // 🆕 v5.0: Добавлена поддержка call_cost и record_url
  async function loadConversationMessages(sessionId, container) {
    try {
      console.log('[MESSAGES] Loading for sessionId:', sessionId);
      const data = await api.get(`/conversations/${sessionId}?include_functions=false`);
      
      container.innerHTML = '';
      
      // Рендерим сообщения
      if (!data.messages || data.messages.length === 0) {
        container.innerHTML = `
          <div style="text-align: center; padding: 1rem; color: var(--text-light);">
            Нет сообщений
          </div>
        `;
      } else {
        data.messages.forEach(msg => {
          const messageDiv = document.createElement('div');
          messageDiv.className = msg.type === 'user' ? 'message message-user' : 'message message-assistant';
          
          messageDiv.innerHTML = `
            <div class="message-role">${msg.type === 'user' ? '👤 Клиент' : '🤖 Ассистент'}</div>
            <div class="message-text">${escapeHtml(msg.text)}</div>
          `;
          
          container.appendChild(messageDiv);
        });
      }
      
      // =============================================================================
      // 🆕 v5.0: Секция информации о звонке (стоимость + запись)
      // =============================================================================
      const callCostFormatted = formatCost(data.call_cost);
      const hasRecording = !!data.record_url;
      
      // Показываем секцию только если есть стоимость или запись
      if (callCostFormatted || hasRecording) {
        const callInfoSection = document.createElement('div');
        callInfoSection.className = 'conversation-call-info';
        
        let callInfoHtml = '';
        
        // Стоимость звонка
        if (callCostFormatted) {
          callInfoHtml += `
            <div class="call-info-row cost">
              <i class="fas fa-ruble-sign"></i>
              <span>Стоимость звонка: ${callCostFormatted}</span>
            </div>
          `;
        }
        
        // Аудиоплеер
        if (hasRecording) {
          callInfoHtml += `
            <div class="audio-player-section">
              <div class="audio-player-title">
                <i class="fas fa-headphones"></i>
                Запись звонка
              </div>
              <audio class="audio-player" controls preload="metadata">
                <source src="${data.record_url}" type="audio/mpeg">
                <source src="${data.record_url}" type="audio/wav">
                Ваш браузер не поддерживает воспроизведение аудио.
              </audio>
            </div>
          `;
        }
        
        callInfoSection.innerHTML = callInfoHtml;
        container.appendChild(callInfoSection);
        
        console.log('[MESSAGES] ✅ Call info added - Cost:', callCostFormatted, 'Recording:', hasRecording);
      }
      
    } catch (error) {
      console.error('Error loading messages:', error);
      container.innerHTML = `
        <div style="text-align: center; padding: 1rem; color: var(--error-red);">
          <i class="fas fa-exclamation-circle"></i>
          Ошибка загрузки сообщений
        </div>
      `;
    }
  }
  
  // ==================== Task Operations ====================
  
  /**
   * Открыть модалку создания задачи
   */
  function openTaskCreateModal() {
    const mskNow = getMskNow();
    mskNow.setUTCHours(mskNow.getUTCHours() + 1);
    mskNow.setUTCMinutes(0, 0, 0);
    
    const year = mskNow.getUTCFullYear();
    const month = String(mskNow.getUTCMonth() + 1).padStart(2, '0');
    const day = String(mskNow.getUTCDate()).padStart(2, '0');
    const hours = String(mskNow.getUTCHours()).padStart(2, '0');
    const minutes = String(mskNow.getUTCMinutes()).padStart(2, '0');
    
    taskCreateForm.reset();
    taskCreateDatetime.value = `${year}-${month}-${day}T${hours}:${minutes}`;
    
    taskCreateModal.classList.add('show');
  }
  
  function closeTaskCreateModal() {
    taskCreateModal.classList.remove('show');
    taskCreateForm.reset();
  }
  
  /**
   * Создать задачу
   */
  async function createTask(e) {
    e.preventDefault();
    
    const assistantValue = taskCreateAssistant.value;
    if (!assistantValue) {
      showNotification('Выберите ассистента', 'error');
      return;
    }
    
    const [assistantId, assistantType] = assistantValue.split('|');
    const scheduledTime = taskCreateDatetime.value;
    const title = taskCreateTitle.value.trim();
    const description = taskCreateDescription.value.trim();
    const customGreeting = taskCreateGreeting.value.trim();
    
    if (!title) {
      showNotification('Введите название задачи', 'error');
      return;
    }
    
    if (!scheduledTime) {
      showNotification('Укажите время звонка', 'error');
      return;
    }
    
    try {
      setLoading(true);
      
      const body = {
        scheduled_time: mskToUtc(scheduledTime),
        title: title,
        description: description || null,
        custom_greeting: customGreeting || null,
        caller_id: taskCreateCallerId.value || null,
        assistant_id: assistantId
      };

      console.log('[TASK-CREATE] Sending:', body);
      
      await api.post(`/contacts/${contactId}/tasks`, body);
      
      closeTaskCreateModal();
      await loadTimeline();
      
      showNotification('Задача создана успешно', 'success');
      
    } catch (error) {
      console.error('Error creating task:', error);
      showNotification(error.message || 'Ошибка создания задачи', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  /**
   * Открыть модалку просмотра задачи
   */
  async function openTaskViewModal(taskId) {
    try {
      setLoading(true);
      currentTaskId = taskId;
      
      const response = await api.get(`/contacts/tasks/${taskId}`);
      currentTaskData = response;
      
      populateTaskViewMode();
      
      taskViewMode.style.display = 'block';
      taskEditForm.style.display = 'none';
      taskModalTitle.innerHTML = '<i class="fas fa-calendar-alt"></i> Детали задачи';
      
      taskViewModal.classList.add('show');
      
      setLoading(false);
      
    } catch (error) {
      console.error('Error loading task:', error);
      showNotification('Ошибка загрузки задачи', 'error');
      setLoading(false);
    }
  }
  
  /**
   * Заполнить режим просмотра задачи
   */
  function populateTaskViewMode() {
    const task = currentTaskData;
    const statusInfo = getTaskStatusInfo(task.status);
    
    // Status
    taskViewStatus.innerHTML = `
      <span class="task-status-badge status-${task.status}">
        ${statusInfo.icon} ${statusInfo.label}
      </span>
    `;
    
    // Title
    taskViewTitle.textContent = task.title;
    
    // DateTime
    const scheduledMsk = utcToMsk(task.scheduled_time);
    const mskNow = getMskNow();
    const isOverdue = scheduledMsk < mskNow && task.status === 'scheduled';
    
    taskViewDatetime.innerHTML = 
      formatDateTime(task.scheduled_time) + 
      (isOverdue ? ' <span style="color: var(--error-red); font-weight: 600;">⚠️ Просрочено</span>' : '');
    
    // Assistant
    const assistantIcon = task.assistant_type === 'openai' ? '🤖' : task.assistant_type === 'gemini' ? '✨' : '🎧';
    taskViewAssistant.textContent = `${assistantIcon} ${task.assistant_name}`;
    
    // Description
    if (task.description) {
      taskViewDescriptionSection.style.display = 'block';
      taskViewDescription.textContent = task.description;
    } else {
      taskViewDescriptionSection.style.display = 'none';
    }
    
    // Custom greeting
    if (task.custom_greeting) {
      taskViewGreetingSection.style.display = 'block';
      taskViewGreeting.textContent = task.custom_greeting;
    } else {
      taskViewGreetingSection.style.display = 'none';
    }

    // Caller ID
    const taskViewCallerIdSection = document.getElementById('task-view-caller-id-section');
    const taskViewCallerIdValue = document.getElementById('task-view-caller-id');
    if (taskViewCallerIdSection && taskViewCallerIdValue) {
      if (task.caller_id) {
        taskViewCallerIdSection.style.display = 'block';
        taskViewCallerIdValue.textContent = formatPhoneNumber(task.caller_id);
      } else {
        taskViewCallerIdSection.style.display = 'none';
      }
    }

    // ✅ v4.0: Показываем кнопку редактирования только для scheduled/pending
    if (task.status === 'scheduled' || task.status === 'pending') {
      taskViewEditBtn.style.display = 'inline-flex';
    } else {
      taskViewEditBtn.style.display = 'none';
    }
    
    // ✅ v4.0: Кнопка удаления всегда видна
    taskViewDeleteBtn.style.display = 'inline-flex';
  }
  
  /**
   * Переключиться в режим редактирования
   */
  function switchToEditMode() {
    const task = currentTaskData;
    
    let assistantValue;
    if (task.assistant_type === 'openai') {
      assistantValue = `${task.assistant_id}|openai`;
    } else if (task.assistant_type === 'gemini') {
      assistantValue = `${task.gemini_assistant_id}|gemini`;
    } else if (task.assistant_type === 'cartesia') {
      assistantValue = `${task.cartesia_assistant_id}|cartesia`;
    }
    
    taskEditAssistant.value = assistantValue;
    taskEditDatetime.value = formatDatetimeLocalMsk(task.scheduled_time);
    taskEditTitle.value = task.title;
    taskEditDescription.value = task.description || '';
    taskEditGreeting.value = task.custom_greeting || '';

    // Caller ID
    if (taskEditCallerId) {
      taskEditCallerId.value = task.caller_id || '';
    }

    taskViewMode.style.display = 'none';
    taskEditForm.style.display = 'block';
    taskModalTitle.innerHTML = '<i class="fas fa-edit"></i> Редактирование задачи';
  }
  
  function switchToViewMode() {
    taskViewMode.style.display = 'block';
    taskEditForm.style.display = 'none';
    taskModalTitle.innerHTML = '<i class="fas fa-calendar-alt"></i> Детали задачи';
  }
  
  /**
   * Обновить задачу
   */
  async function updateTask(e) {
    e.preventDefault();
    
    const assistantValue = taskEditAssistant.value;
    if (!assistantValue) {
      showNotification('Выберите ассистента', 'error');
      return;
    }
    
    const [assistantId, assistantType] = assistantValue.split('|');
    const scheduledTime = taskEditDatetime.value;
    const title = taskEditTitle.value.trim();
    const description = taskEditDescription.value.trim();
    const customGreeting = taskEditGreeting.value.trim();
    
    if (!title) {
      showNotification('Введите название задачи', 'error');
      return;
    }
    
    if (!scheduledTime) {
      showNotification('Укажите время звонка', 'error');
      return;
    }
    
    try {
      setLoading(true);
      
      const body = {
        scheduled_time: mskToUtc(scheduledTime),
        title: title,
        description: description || null,
        custom_greeting: customGreeting || null,
        caller_id: taskEditCallerId.value || null,
        assistant_id: assistantId
      };

      console.log('[TASK-UPDATE] Sending:', body);
      
      const response = await api.put(`/contacts/tasks/${currentTaskId}`, body);
      currentTaskData = response;
      
      showNotification('Задача обновлена успешно', 'success');
      
      await loadTimeline();
      closeTaskViewModal();
      
      setLoading(false);
      
    } catch (error) {
      console.error('Error updating task:', error);
      showNotification(error.message || 'Ошибка обновления задачи', 'error');
      setLoading(false);
    }
  }
  
  function closeTaskViewModal() {
    taskViewModal.classList.remove('show');
    currentTaskId = null;
    currentTaskData = null;
  }
  
  // ==================== Delete Task ====================
  
  /**
   * ✅ v4.0: Открыть модалку подтверждения удаления задачи
   */
  function openDeleteTaskModal() {
    if (!currentTaskId || !currentTaskData) {
      showNotification('Задача не выбрана', 'error');
      return;
    }
    
    taskToDeleteId = currentTaskId;
    
    // Формируем текст подтверждения
    const statusInfo = getTaskStatusInfo(currentTaskData.status);
    deleteTaskText.innerHTML = `
      Вы уверены, что хотите удалить задачу "<strong>${escapeHtml(currentTaskData.title)}</strong>"?
      <br><br>
      Текущий статус: <span class="task-status-badge status-${currentTaskData.status}">${statusInfo.icon} ${statusInfo.label}</span>
      <br><br>
      <strong>Это действие нельзя отменить.</strong>
    `;
    
    deleteTaskModal.classList.add('show');
  }
  
  function closeDeleteTaskModal() {
    deleteTaskModal.classList.remove('show');
    taskToDeleteId = null;
  }
  
  /**
   * ✅ v4.0: Подтвердить удаление задачи
   */
  async function confirmDeleteTask() {
    if (!taskToDeleteId) {
      showNotification('Задача не выбрана', 'error');
      return;
    }
    
    try {
      setLoading(true);
      
      await api.delete(`/contacts/tasks/${taskToDeleteId}`);
      
      closeDeleteTaskModal();
      closeTaskViewModal();
      await loadTimeline();
      
      showNotification('Задача удалена', 'success');
      
    } catch (error) {
      console.error('Error deleting task:', error);
      showNotification(error.message || 'Ошибка удаления задачи', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // ==================== Contact Operations ====================
  
  async function updateStatus(newStatus) {
    try {
      setLoading(true);
      
      await api.patch(`/contacts/${contactId}/status`, {
        status: newStatus
      });
      
      showNotification('Статус обновлен', 'success');
      
    } catch (error) {
      console.error('Error updating status:', error);
      showNotification(error.message || 'Ошибка обновления статуса', 'error');
      statusDropdown.value = currentContact.status;
    } finally {
      setLoading(false);
    }
  }
  
  async function saveName() {
    try {
      const newName = nameInput.value.trim();
      
      setLoading(true);
      
      const response = await api.put(`/contacts/${contactId}`, {
        name: newName || null
      });
      
      currentContact = response.contact;
      
      showNotification('Имя сохранено', 'success');
      
    } catch (error) {
      console.error('Error saving name:', error);
      showNotification(error.message || 'Ошибка сохранения имени', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  async function deleteContact() {
    try {
      setLoading(true);
      
      await api.delete(`/contacts/${contactId}`);
      
      showNotification('Контакт удален', 'success');
      
      setTimeout(() => {
        window.location.href = '/static/crm.html';
      }, 1500);
      
    } catch (error) {
      console.error('Error deleting contact:', error);
      showNotification(error.message || 'Ошибка удаления контакта', 'error');
      setLoading(false);
    }
  }
  
  // ==================== Event Listeners ====================
  
  // Name
  saveNameBtn.addEventListener('click', saveName);
  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveName();
    }
  });
  
  // Notes
  addNoteBtn.addEventListener('click', addNote);
  noteInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      e.preventDefault();
      addNote();
    }
  });
  
  // Status
  statusDropdown.addEventListener('change', (e) => {
    updateStatus(e.target.value);
  });
  
  // Task Create Modal
  createTaskBtn.addEventListener('click', openTaskCreateModal);
  taskCreateCancelBtn.addEventListener('click', closeTaskCreateModal);
  taskCreateForm.addEventListener('submit', createTask);
  taskCreateModal.addEventListener('click', (e) => {
    if (e.target === taskCreateModal) {
      closeTaskCreateModal();
    }
  });
  
  // Task View/Edit Modal
  taskViewCloseBtn.addEventListener('click', closeTaskViewModal);
  taskViewEditBtn.addEventListener('click', switchToEditMode);
  taskViewDeleteBtn.addEventListener('click', openDeleteTaskModal);
  taskEditCancelBtn.addEventListener('click', switchToViewMode);
  taskEditForm.addEventListener('submit', updateTask);
  taskViewModal.addEventListener('click', (e) => {
    if (e.target === taskViewModal) {
      closeTaskViewModal();
    }
  });
  
  // ✅ v4.0: Delete Task Modal
  cancelDeleteTaskBtn.addEventListener('click', closeDeleteTaskModal);
  confirmDeleteTaskBtn.addEventListener('click', confirmDeleteTask);
  deleteTaskModal.addEventListener('click', (e) => {
    if (e.target === deleteTaskModal) {
      closeDeleteTaskModal();
    }
  });
  
  // Delete Contact
  deleteContactBtn.addEventListener('click', () => {
    deleteModal.classList.add('show');
  });
  cancelDeleteBtn.addEventListener('click', () => {
    deleteModal.classList.remove('show');
  });
  confirmDeleteBtn.addEventListener('click', () => {
    deleteModal.classList.remove('show');
    deleteContact();
  });
  deleteModal.addEventListener('click', (e) => {
    if (e.target === deleteModal) {
      deleteModal.classList.remove('show');
    }
  });
  
  // Notification
  notificationClose.addEventListener('click', hideNotification);
  
  // ==================== Initialization ====================
  if (!api.isAuthenticated()) {
    window.location.href = '/static/login.html';
  } else {
    const urlParams = new URLSearchParams(window.location.search);
    contactId = urlParams.get('id');
    
    if (!contactId) {
      showNotification('ID контакта не указан', 'error');
      setTimeout(() => {
        window.location.href = '/static/crm.html';
      }, 2000);
    } else {
      loadContact();
    }
  }
});
