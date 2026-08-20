// /static/agents/ui.js

// ============================================================================
// UI MODULE - Управление интерфейсом
// ============================================================================

const ui = {
  
  // ============================================================================
  // УВЕДОМЛЕНИЯ
  // ============================================================================
  
  /**
   * Показать уведомление пользователю
   * @param {string} message - Текст уведомления
   * @param {string} type - Тип уведомления (success, error, info, warning)
   * @param {number} duration - Длительность показа в мс (по умолчанию 5000)
   */
  showNotification(message, type = 'success', duration = 5000) {
    const notification = document.getElementById('notification');
    const notificationMessage = document.getElementById('notification-message');
    
    if (!notification || !notificationMessage) {
      console.error('[UI] Элементы уведомления не найдены');
      return;
    }
    
    // Удаляем все классы типов
    notification.classList.remove(
      'notification-success', 
      'notification-error', 
      'notification-info',
      'notification-warning'
    );
    
    // Добавляем класс нужного типа
    notification.classList.add(`notification-${type}`);
    
    // Устанавливаем иконку в зависимости от типа
    const iconElement = notification.querySelector('.notification-icon i');
    if (iconElement) {
      const iconClass = {
        success: 'fas fa-check-circle',
        error: 'fas fa-exclamation-circle',
        info: 'fas fa-info-circle',
        warning: 'fas fa-exclamation-triangle'
      }[type] || 'fas fa-info-circle';
      
      iconElement.className = iconClass;
    }
    
    // Устанавливаем текст сообщения
    notificationMessage.textContent = message;
    
    // Показываем уведомление
    notification.style.display = 'flex';
    setTimeout(() => {
      notification.classList.add('show');
    }, 10);
    
    // Автоматически скрываем через заданное время
    setTimeout(() => {
      this.hideNotification();
    }, duration);
    
    console.log(`[UI] Уведомление показано: ${type} - ${message}`);
  },
  
  /**
   * Скрыть текущее уведомление
   */
  hideNotification() {
    const notification = document.getElementById('notification');
    if (!notification) return;
    
    notification.classList.remove('show');
    setTimeout(() => {
      notification.style.display = 'none';
    }, 300);
    
    console.log('[UI] Уведомление скрыто');
  },
  
  // ============================================================================
  // ТАБЫ
  // ============================================================================
  
  /**
   * Переключение между вкладками
   * @param {string} tabId - ID вкладки для активации
   */
  switchTab(tabId) {
    console.log(`[UI] Переключение на вкладку: ${tabId}`);
    
    const tabs = document.querySelectorAll('.tab');
    const tabContents = document.querySelectorAll('.tab-content');
    
    // Активируем нужную вкладку в навигации
    tabs.forEach(tab => {
      const isActive = tab.getAttribute('data-tab') === tabId;
      tab.classList.toggle('active', isActive);
    });
    
    // Показываем нужный контент
    tabContents.forEach(content => {
      const isActive = content.id === `${tabId}-tab`;
      content.classList.toggle('active', isActive);
    });
  },
  
  // ============================================================================
  // ФОРМА АГЕНТА
  // ============================================================================
  
  /**
   * Заполнить форму агента данными
   * @param {object} agent - Объект с данными агента
   */
  fillAgentForm(agent) {
    if (!agent) {
      console.error('[UI] Попытка заполнить форму пустыми данными');
      return;
    }
    
    console.log('[UI] Заполнение формы данными агента:', agent.id);
    
    // Основные поля
    const nameInput = document.getElementById('agent-name');
    const descriptionInput = document.getElementById('agent-description');
    const systemPromptInput = document.getElementById('system-prompt');
    const greetingMessageInput = document.getElementById('greeting-message');
    const googleSheetIdInput = document.getElementById('google-sheet-id');
    
    if (nameInput) nameInput.value = agent.name || '';
    if (descriptionInput) descriptionInput.value = agent.description || '';
    if (systemPromptInput) systemPromptInput.value = agent.system_prompt || '';
    if (googleSheetIdInput) googleSheetIdInput.value = agent.google_sheet_id || '';
    
    // Приветственное сообщение с дефолтным значением
    if (greetingMessageInput) {
      greetingMessageInput.value = agent.greeting_message ?? 'Здравствуйте! Чем я могу вам помочь?';
    }

    // Vision AI чекбокс
    const enableVisionInput = document.getElementById('enable-vision');
    if (enableVisionInput) {
      enableVisionInput.checked = agent.enable_vision ?? false;
    }

    // Выбор голоса
    this.selectVoice(agent.voice || 'alloy');
    
    // Загрузка функций
    this.loadFunctions(agent.functions || []);
    
    console.log('[UI] Форма успешно заполнена');
  },
  
  /**
   * Выбрать голос в форме
   * @param {string} voiceValue - Значение голоса
   */
  selectVoice(voiceValue) {
    const voiceInput = document.querySelector(`input[name="voice"][value="${voiceValue}"]`);
    const voiceOptions = document.querySelectorAll('.voice-option');
    
    if (voiceInput) {
      // Убираем выделение со всех опций
      voiceOptions.forEach(option => {
        option.classList.remove('selected');
      });
      
      // Выделяем нужную опцию
      const voiceOption = voiceInput.closest('.voice-option');
      if (voiceOption) {
        voiceOption.classList.add('selected');
      }
      voiceInput.checked = true;
      
      console.log(`[UI] Выбран голос: ${voiceValue}`);
    } else {
      console.warn(`[UI] Голос "${voiceValue}" не найден в опциях`);
    }
  },
  
  /**
   * Загрузить состояние функций (ОБНОВЛЕНО - динамическая работа)
   * @param {Array} functions - Массив включенных функций
   */
  loadFunctions(functions) {
    console.log('[UI] Загрузка функций:', functions);
    
    // Сначала сбрасываем все чекбоксы
    document.querySelectorAll('input[type="checkbox"][data-function-name]').forEach(checkbox => {
      checkbox.checked = false;
      const functionName = checkbox.dataset.functionName;
      const infoDiv = document.getElementById(`${functionName}-info`);
      if (infoDiv) {
        infoDiv.style.display = 'none';
      }
    });
    
    // Затем включаем нужные функции
    functions.forEach(func => {
      const checkbox = document.querySelector(`input[data-function-name="${func.name}"]`);
      if (checkbox) {
        checkbox.checked = true;
        const infoDiv = document.getElementById(`${func.name}-info`);
        if (infoDiv) {
          infoDiv.style.display = 'block';
        }
        console.log(`[UI] Функция "${func.name}" включена`);
      } else {
        console.warn(`[UI] Чекбокс для функции "${func.name}" не найден`);
      }
    });
  },
  
  /**
   * Получить данные из формы агента (ОБНОВЛЕНО - динамический сбор функций)
   * @returns {object} Объект с данными формы
   */
  getFormData() {
    const nameInput = document.getElementById('agent-name');
    const descriptionInput = document.getElementById('agent-description');
    const systemPromptInput = document.getElementById('system-prompt');
    const greetingMessageInput = document.getElementById('greeting-message');
    const googleSheetIdInput = document.getElementById('google-sheet-id');
    const voiceInput = document.querySelector('input[name="voice"]:checked');
    
    const formData = {
      name: nameInput ? nameInput.value : '',
      description: descriptionInput ? descriptionInput.value : '',
      system_prompt: systemPromptInput ? systemPromptInput.value : '',
      voice: voiceInput ? voiceInput.value : 'alloy',
      google_sheet_id: googleSheetIdInput && googleSheetIdInput.value ? googleSheetIdInput.value : null,
      greeting_message: greetingMessageInput ? greetingMessageInput.value : 'Здравствуйте! Чем я могу вам помочь?',
      enable_vision: document.getElementById('enable-vision')?.checked ?? false
    };
    
    // ОБНОВЛЕНО: Динамический сбор включенных функций
    const enabledFunctions = [];
    
    document.querySelectorAll('input[type="checkbox"][data-function-name]:checked').forEach(checkbox => {
      const functionName = checkbox.dataset.functionName;
      
      // Ищем описание функции в глобальном списке availableFunctions
      // (availableFunctions доступен из index.js через window)
      const functionData = window.availableFunctions && window.availableFunctions.find(f => f.name === functionName);
      
      if (functionData) {
        enabledFunctions.push({
          name: functionData.name,
          description: functionData.description
        });
      } else {
        // Фоллбэк - если функции нет в списке, добавляем с базовым описанием
        console.warn(`[UI] Функция "${functionName}" не найдена в availableFunctions, используем фоллбэк`);
        enabledFunctions.push({
          name: functionName,
          description: `Функция ${functionName}`
        });
      }
    });
    
    formData.functions = enabledFunctions;
    
    console.log('[UI] Данные формы собраны:', formData);
    console.log('[UI] Включено функций:', enabledFunctions.length);
    
    return formData;
  },
  
  /**
   * Валидация формы
   * @returns {object} Объект с результатом валидации { valid: boolean, errors: array }
   */
  validateForm() {
    const errors = [];
    
    const nameInput = document.getElementById('agent-name');
    const systemPromptInput = document.getElementById('system-prompt');
    
    // Проверка обязательных полей
    if (!nameInput || !nameInput.value.trim()) {
      errors.push('Название агента обязательно для заполнения');
    }
    
    if (!systemPromptInput || !systemPromptInput.value.trim()) {
      errors.push('Системный промпт обязателен для заполнения');
    }
    
    // Проверка длины названия
    if (nameInput && nameInput.value.length > 100) {
      errors.push('Название агента не должно превышать 100 символов');
    }
    
    // Проверка длины приветственного сообщения
    const greetingMessageInput = document.getElementById('greeting-message');
    if (greetingMessageInput && greetingMessageInput.value.length > 200) {
      errors.push('Приветственное сообщение не должно превышать 200 символов');
    }
    
    const isValid = errors.length === 0;
    
    if (!isValid) {
      console.warn('[UI] Ошибки валидации формы:', errors);
    }
    
    return { valid: isValid, errors: errors };
  },
  
  /**
   * Показать ошибки валидации
   * @param {Array} errors - Массив ошибок
   */
  showValidationErrors(errors) {
    if (!errors || errors.length === 0) return;
    
    const errorMessage = errors.join('\n');
    this.showNotification(errorMessage, 'error', 7000);
  },
  
  // ============================================================================
  // ЗАГРУЗКА
  // ============================================================================
  
  /**
   * Показать/скрыть индикатор загрузки
   * @param {boolean} loading - true для показа, false для скрытия
   */
  setLoading(loading) {
    const loadingOverlay = document.getElementById('loading-overlay');
    if (!loadingOverlay) return;
    
    loadingOverlay.style.display = loading ? 'flex' : 'none';
    
    // Блокируем/разблокируем все кнопки
    const buttons = document.querySelectorAll('.btn');
    buttons.forEach(button => {
      button.disabled = loading;
    });
    
    console.log(`[UI] Загрузка: ${loading ? 'показана' : 'скрыта'}`);
  },
  
  /**
   * Показать скелетон загрузки в списке агентов
   */
  showAgentsListSkeleton() {
    const container = document.getElementById('agents-list');
    if (!container) return;
    
    container.innerHTML = `
      <div class="agents-list-skeleton">
        <div class="skeleton-item"></div>
        <div class="skeleton-item"></div>
        <div class="skeleton-item"></div>
      </div>
    `;
    
    console.log('[UI] Скелетон списка агентов показан');
  },
  
  // ============================================================================
  // МОДАЛЬНЫЕ ОКНА
  // ============================================================================
  
  /**
   * Показать модальное окно подтверждения
   * @param {string} title - Заголовок
   * @param {string} message - Сообщение
   * @param {string} confirmText - Текст кнопки подтверждения
   * @param {string} cancelText - Текст кнопки отмены
   * @returns {Promise<boolean>} true если пользователь подтвердил
   */
  showConfirmDialog(title, message, confirmText = 'Подтвердить', cancelText = 'Отмена') {
    return new Promise((resolve) => {
      // Используем стандартный confirm для простоты
      // В продакшене можно заменить на кастомное модальное окно
      const result = confirm(`${title}\n\n${message}`);
      resolve(result);
    });
  },
  
  /**
   * Показать модальное окно с информацией
   * @param {string} title - Заголовок
   * @param {string} message - Сообщение
   */
  showInfoDialog(title, message) {
    alert(`${title}\n\n${message}`);
  },
  
  // ============================================================================
  // КОПИРОВАНИЕ В БУФЕР
  // ============================================================================
  
  /**
   * Копировать текст в буфер обмена
   * @param {string} text - Текст для копирования
   * @param {string} successMessage - Сообщение об успехе
   * @returns {boolean} true если успешно скопировано
   */
  copyToClipboard(text, successMessage = 'Скопировано!') {
    try {
      // Современный способ через Clipboard API
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          this.showNotification(successMessage, 'success', 3000);
        }).catch(err => {
          console.error('[UI] Ошибка копирования через Clipboard API:', err);
          this.fallbackCopyToClipboard(text, successMessage);
        });
      } else {
        // Fallback для старых браузеров
        this.fallbackCopyToClipboard(text, successMessage);
      }
      return true;
    } catch (error) {
      console.error('[UI] Ошибка копирования:', error);
      this.showNotification('Ошибка копирования', 'error');
      return false;
    }
  },
  
  /**
   * Запасной метод копирования для старых браузеров
   * @param {string} text - Текст для копирования
   * @param {string} successMessage - Сообщение об успехе
   */
  fallbackCopyToClipboard(text, successMessage) {
    const tempTextarea = document.createElement('textarea');
    tempTextarea.value = text;
    tempTextarea.style.position = 'fixed';
    tempTextarea.style.opacity = '0';
    document.body.appendChild(tempTextarea);
    tempTextarea.select();
    
    try {
      document.execCommand('copy');
      this.showNotification(successMessage, 'success', 3000);
    } catch (err) {
      console.error('[UI] Ошибка fallback копирования:', err);
      this.showNotification('Ошибка копирования', 'error');
    }
    
    document.body.removeChild(tempTextarea);
  },
  
  // ============================================================================
  // ФОРМАТИРОВАНИЕ
  // ============================================================================
  
  /**
   * Форматировать дату в читаемый вид
   * @param {string|Date} date - Дата для форматирования
   * @param {string} format - Формат (full, short, time)
   * @returns {string} Отформатированная дата
   */
  formatDate(date, format = 'full') {
    if (!date) return '-';
    
    const dateObj = typeof date === 'string' ? new Date(date) : date;
    
    if (isNaN(dateObj.getTime())) {
      return '-';
    }
    
    const options = {
      full: { 
        year: 'numeric', 
        month: 'long', 
        day: 'numeric', 
        hour: '2-digit', 
        minute: '2-digit' 
      },
      short: { 
        year: 'numeric', 
        month: 'short', 
        day: 'numeric' 
      },
      time: { 
        hour: '2-digit', 
        minute: '2-digit' 
      }
    };
    
    return dateObj.toLocaleDateString('ru-RU', options[format] || options.full);
  },
  
  /**
   * Форматировать число с разделителями тысяч
   * @param {number} num - Число для форматирования
   * @returns {string} Отформатированное число
   */
  formatNumber(num) {
    if (num === null || num === undefined) return '0';
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  },
  
  /**
   * Сократить текст до заданной длины
   * @param {string} text - Текст для сокращения
   * @param {number} maxLength - Максимальная длина
   * @param {string} suffix - Суффикс (по умолчанию '...')
   * @returns {string} Сокращенный текст
   */
  truncateText(text, maxLength = 100, suffix = '...') {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength - suffix.length) + suffix;
  },
  
  // ============================================================================
  // АНИМАЦИИ
  // ============================================================================
  
  /**
   * Плавная прокрутка к элементу
   * @param {string|HTMLElement} target - Селектор или элемент
   * @param {number} offset - Смещение от верха в px
   */
  scrollToElement(target, offset = 20) {
    const element = typeof target === 'string' ? document.querySelector(target) : target;
    if (!element) return;
    
    const elementPosition = element.getBoundingClientRect().top + window.pageYOffset;
    const offsetPosition = elementPosition - offset;
    
    window.scrollTo({
      top: offsetPosition,
      behavior: 'smooth'
    });
    
    console.log('[UI] Прокрутка к элементу');
  },
  
  /**
   * Добавить эффект "пульсации" к элементу
   * @param {string|HTMLElement} target - Селектор или элемент
   * @param {number} duration - Длительность в мс
   */
  pulseElement(target, duration = 2000) {
    const element = typeof target === 'string' ? document.querySelector(target) : target;
    if (!element) return;
    
    element.classList.add('pulse-animation');
    
    setTimeout(() => {
      element.classList.remove('pulse-animation');
    }, duration);
    
    console.log('[UI] Анимация пульсации добавлена');
  },
  
  // ============================================================================
  // ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
  // ============================================================================
  
  /**
   * Проверка видимости элемента
   * @param {HTMLElement} element - Элемент для проверки
   * @returns {boolean} true если элемент видим
   */
  isElementVisible(element) {
    if (!element) return false;
    return element.offsetWidth > 0 && element.offsetHeight > 0;
  },
  
  /**
   * Получить размеры viewport
   * @returns {object} Объект с шириной и высотой { width, height }
   */
  getViewportSize() {
    return {
      width: window.innerWidth || document.documentElement.clientWidth,
      height: window.innerHeight || document.documentElement.clientHeight
    };
  },
  
  /**
   * Проверка мобильного устройства
   * @returns {boolean} true если мобильное устройство
   */
  isMobile() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  },
  
  /**
   * Дебаунс функции (задержка выполнения)
   * @param {Function} func - Функция для дебаунса
   * @param {number} wait - Время ожидания в мс
   * @returns {Function} Обернутая функция
   */
  debounce(func, wait = 300) {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func(...args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  },
  
  /**
   * Троттлинг функции (ограничение частоты вызовов)
   * @param {Function} func - Функция для троттлинга
   * @param {number} limit - Минимальный интервал между вызовами в мс
   * @returns {Function} Обернутая функция
   */
  throttle(func, limit = 300) {
    let inThrottle;
    return function(...args) {
      if (!inThrottle) {
        func.apply(this, args);
        inThrottle = true;
        setTimeout(() => inThrottle = false, limit);
      }
    };
  },
  
  /**
   * Очистить все поля формы
   * @param {string|HTMLElement} form - Селектор или элемент формы
   */
  clearForm(form) {
    const formElement = typeof form === 'string' ? document.querySelector(form) : form;
    if (!formElement) return;
    
    formElement.reset();
    console.log('[UI] Форма очищена');
  },
  
  /**
   * Заблокировать/разблокировать элемент
   * @param {string|HTMLElement} target - Селектор или элемент
   * @param {boolean} disabled - true для блокировки
   */
  setElementDisabled(target, disabled) {
    const element = typeof target === 'string' ? document.querySelector(target) : target;
    if (!element) return;
    
    element.disabled = disabled;
    
    if (disabled) {
      element.classList.add('disabled');
    } else {
      element.classList.remove('disabled');
    }
  },
  
  /**
   * Показать/скрыть элемент
   * @param {string|HTMLElement} target - Селектор или элемент
   * @param {boolean} show - true для показа
   * @param {string} displayType - Тип display (block, flex, inline-block и т.д.)
   */
  toggleElement(target, show, displayType = 'block') {
    const element = typeof target === 'string' ? document.querySelector(target) : target;
    if (!element) return;
    
    element.style.display = show ? displayType : 'none';
  },
  
  /**
   * Добавить класс с анимацией
   * @param {string|HTMLElement} target - Селектор или элемент
   * @param {string} className - Класс для добавления
   * @param {number} duration - Длительность в мс (0 для постоянного)
   */
  addClassTemporary(target, className, duration = 0) {
    const element = typeof target === 'string' ? document.querySelector(target) : target;
    if (!element) return;
    
    element.classList.add(className);
    
    if (duration > 0) {
      setTimeout(() => {
        element.classList.remove(className);
      }, duration);
    }
  },
  
  /**
   * Создать элемент из HTML строки
   * @param {string} html - HTML строка
   * @returns {HTMLElement} Созданный элемент
   */
  createElementFromHTML(html) {
    const template = document.createElement('template');
    template.innerHTML = html.trim();
    return template.content.firstChild;
  },
  
  /**
   * Экранирование HTML для предотвращения XSS
   * @param {string} text - Текст для экранирования
   * @returns {string} Экранированный текст
   */
  escapeHtml(text) {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
  }
};

// ============================================================================
// ЭКСПОРТ
// ============================================================================

export { ui };
export default ui;
