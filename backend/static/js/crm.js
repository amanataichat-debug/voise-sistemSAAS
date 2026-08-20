// backend/static/js/crm.js
/**
 * CRM Interface для Voicyfy
 * Управление контактами (клиентами)
 * Version: 2.0 - Added Kanban view with drag & drop
 */

document.addEventListener('DOMContentLoaded', function() {
  // ==================== Элементы ====================
  const contactsGrid = document.getElementById('contacts-grid');
  const kanbanBoard = document.getElementById('kanban-board');
  const searchInput = document.getElementById('search-input');
  const refreshBtn = document.getElementById('refresh-btn');
  const prevBtn = document.getElementById('prev-btn');
  const nextBtn = document.getElementById('next-btn');
  const paginationInfo = document.getElementById('pagination-info');
  const pagination = document.getElementById('pagination');
  const notification = document.getElementById('notification');
  const notificationMessage = document.getElementById('notification-message');
  const notificationClose = document.getElementById('notification-close');
  const loadingOverlay = document.getElementById('loading-overlay');
  const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
  const sidebar = document.getElementById('sidebar');
  const sidebarOverlay = document.getElementById('sidebar-overlay');
  const userMenuButton = document.getElementById('user-menu-button');
  const userDropdown = document.getElementById('user-dropdown');
  const userEmailDisplay = document.getElementById('user-name');
  const userAvatar = document.getElementById('user-avatar');
  const statusBadges = document.querySelectorAll('.status-badge');
  const viewToggleBtns = document.querySelectorAll('.view-toggle-btn');
  const gridViewBtn = document.getElementById('grid-view-btn');
  const kanbanViewBtn = document.getElementById('kanban-view-btn');
  const contactsContainer = document.getElementById('contacts-container');
  const kanbanContainer = document.getElementById('kanban-container');
  
  // ==================== Состояние ====================
  let currentPage = 0;
  let totalPages = 1;
  let totalContacts = 0;
  let currentStatus = '';
  let searchQuery = '';
  let currentView = 'grid'; // 'grid' or 'kanban'
  let allContacts = []; // Для Kanban view
  const perPage = 20;
  
  // Таймер для debounce поиска
  let searchTimeout = null;
  
  // ==================== API функции ====================
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
    
    patch(endpoint, body) {
      return this.fetch(endpoint, { method: 'PATCH', body });
    }
  };
  
  // ==================== UI функции ====================
  function setLoading(loading) {
    loadingOverlay.style.display = loading ? 'flex' : 'none';
  }
  
  function showNotification(message, type = 'success') {
    notification.classList.remove('notification-success', 'notification-error', 'notification-info');
    notification.classList.add(`notification-${type}`);
    
    const iconElement = notification.querySelector('.notification-icon i');
    iconElement.className = type === 'success' ? 'fas fa-check-circle' : 
                         type === 'error' ? 'fas fa-exclamation-circle' : 
                         'fas fa-info-circle';
    
    notificationMessage.textContent = message;
    notification.style.display = 'flex';
    
    setTimeout(() => {
      notification.classList.add('show');
    }, 10);
    
    setTimeout(() => {
      hideNotification();
    }, 5000);
  }
  
  function hideNotification() {
    notification.classList.remove('show');
    setTimeout(() => {
      notification.style.display = 'none';
    }, 300);
  }
  
  function formatDate(dateString) {
    if (!dateString) return 'Нет данных';
    const date = new Date(dateString);
    const now = new Date();
    const diff = now - date;
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    
    if (days === 0) {
      return 'Сегодня ' + date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    } else if (days === 1) {
      return 'Вчера ' + date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    } else if (days < 7) {
      return days + ' дн. назад';
    } else {
      return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
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
  
  function getStatusLabel(status) {
    const labels = {
      'new': 'Новый',
      'active': 'Активный',
      'client': 'Клиент',
      'archived': 'Архив'
    };
    return labels[status] || status;
  }
  
  function getInitials(name, phone) {
    if (name) {
      const words = name.trim().split(' ');
      if (words.length >= 2) {
        return words[0][0].toUpperCase() + words[1][0].toUpperCase();
      }
      return name.substring(0, 2).toUpperCase();
    }
    if (phone) {
      const cleaned = phone.replace(/\D/g, '');
      return cleaned.slice(-2);
    }
    return '??';
  }
  
  // ==================== View Switching ====================
  function switchView(view) {
    currentView = view;
    
    // Update buttons
    viewToggleBtns.forEach(btn => btn.classList.remove('active'));
    if (view === 'grid') {
      gridViewBtn.classList.add('active');
      contactsContainer.style.display = 'block';
      kanbanContainer.style.display = 'none';
      loadContacts();
    } else {
      kanbanViewBtn.classList.add('active');
      contactsContainer.style.display = 'none';
      kanbanContainer.style.display = 'block';
      loadKanbanContacts();
    }
    
    // Save preference
    localStorage.setItem('crm_view', view);
  }
  
  // ==================== Grid View ====================
  function createContactCard(contact) {
    const card = document.createElement('div');
    card.className = 'contact-card';
    // ✅ ИСПРАВЛЕНО: Переход на детальную страницу
    card.onclick = () => window.location.href = `/static/crm-contact.html?id=${contact.id}`;
    
    const displayName = contact.name || 'Без имени';
    const initials = getInitials(contact.name, contact.phone);
    const formattedPhone = formatPhoneNumber(contact.phone);
    
    const notesPreview = contact.notes 
      ? contact.notes.substring(0, 100) + (contact.notes.length > 100 ? '...' : '')
      : 'Нет заметок';
    
    card.innerHTML = `
      <div class="card-status ${contact.status}">${getStatusLabel(contact.status)}</div>
      
      <div class="card-header">
        <div class="contact-avatar">${initials}</div>
        <div class="card-info">
          <div class="contact-name">${displayName}</div>
          <div class="contact-phone">${formattedPhone}</div>
        </div>
      </div>
      
      <div class="card-notes">${notesPreview}</div>
      
      <div class="card-footer">
        <div class="card-meta">
          <div class="meta-item">
            <i class="fas fa-comments"></i>
            ${contact.total_conversations || 0} диалогов
          </div>
          ${contact.last_interaction ? `
            <div class="meta-item">
              <i class="fas fa-clock"></i>
              ${formatDate(contact.last_interaction)}
            </div>
          ` : ''}
        </div>
      </div>
    `;
    
    return card;
  }
  
  function showEmptyState() {
    contactsGrid.innerHTML = `
      <div class="empty-state" style="grid-column: 1/-1;">
        <i class="fas fa-address-book"></i>
        <h3>Нет контактов</h3>
        <p>Контакты автоматически создаются при звонках с новых номеров</p>
      </div>
    `;
    pagination.style.display = 'none';
  }
  
  async function loadContacts() {
    try {
      setLoading(true);
      
      const offset = currentPage * perPage;
      
      let url = `/contacts?limit=${perPage}&offset=${offset}`;
      
      if (currentStatus) {
        url += `&status=${currentStatus}`;
      }
      
      if (searchQuery) {
        url += `&search=${encodeURIComponent(searchQuery)}`;
      }
      
      const response = await api.get(url);
      
      totalContacts = response.total;
      totalPages = Math.ceil(totalContacts / perPage);
      
      contactsGrid.innerHTML = '';
      
      if (response.contacts.length === 0) {
        showEmptyState();
        return;
      }
      
      response.contacts.forEach(contact => {
        const card = createContactCard(contact);
        contactsGrid.appendChild(card);
      });
      
      updatePagination();
      
    } catch (error) {
      console.error('Error loading contacts:', error);
      showNotification(error.message || 'Ошибка при загрузке контактов', 'error');
      showEmptyState();
    } finally {
      setLoading(false);
    }
  }
  
  function updatePagination() {
    if (totalPages <= 1) {
      pagination.style.display = 'none';
      return;
    }
    
    pagination.style.display = 'flex';
    paginationInfo.textContent = `Страница ${currentPage + 1} из ${totalPages}`;
    
    prevBtn.disabled = currentPage === 0;
    nextBtn.disabled = currentPage >= totalPages - 1;
  }
  
  // ==================== Kanban View ====================
  function createKanbanCard(contact) {
    const card = document.createElement('div');
    card.className = 'kanban-card';
    card.dataset.contactId = contact.id;
    card.onclick = () => window.location.href = `/static/crm-contact.html?id=${contact.id}`;
    
    const displayName = contact.name || 'Без имени';
    const initials = getInitials(contact.name, contact.phone);
    const formattedPhone = formatPhoneNumber(contact.phone);
    
    card.innerHTML = `
      <div class="kanban-card-header">
        <div class="contact-avatar-small">${initials}</div>
        <div class="kanban-card-info">
          <div class="kanban-card-name">${displayName}</div>
          <div class="kanban-card-phone">${formattedPhone}</div>
        </div>
      </div>
      <div class="kanban-card-meta">
        <span><i class="fas fa-comments"></i> ${contact.total_conversations || 0}</span>
        ${contact.last_interaction ? `<span><i class="fas fa-clock"></i> ${formatDate(contact.last_interaction)}</span>` : ''}
      </div>
    `;
    
    return card;
  }
  
  async function loadKanbanContacts() {
    try {
      setLoading(true);
      
      // Загружаем ВСЕ контакты для Kanban (без пагинации)
      let url = `/contacts?limit=100&offset=0`;
      
      if (searchQuery) {
        url += `&search=${encodeURIComponent(searchQuery)}`;
      }
      
      const response = await api.get(url);
      allContacts = response.contacts;
      
      renderKanban();
      
    } catch (error) {
      console.error('Error loading kanban contacts:', error);
      showNotification(error.message || 'Ошибка при загрузке контактов', 'error');
    } finally {
      setLoading(false);
    }
  }
  
  function renderKanban() {
    const statuses = ['new', 'active', 'client', 'archived'];
    const columns = {
      new: document.getElementById('kanban-col-new'),
      active: document.getElementById('kanban-col-active'),
      client: document.getElementById('kanban-col-client'),
      archived: document.getElementById('kanban-col-archived')
    };
    
    // Очищаем колонки
    Object.values(columns).forEach(col => {
      col.innerHTML = '';
    });
    
    // Группируем контакты по статусам
    const grouped = {
      new: [],
      active: [],
      client: [],
      archived: []
    };
    
    allContacts.forEach(contact => {
      if (grouped[contact.status]) {
        grouped[contact.status].push(contact);
      }
    });
    
    // Обновляем счетчики
    document.getElementById('count-new').textContent = grouped.new.length;
    document.getElementById('count-active').textContent = grouped.active.length;
    document.getElementById('count-client').textContent = grouped.client.length;
    document.getElementById('count-archived').textContent = grouped.archived.length;
    
    // Рендерим карточки
    statuses.forEach(status => {
      const contacts = grouped[status];
      const column = columns[status];
      
      if (contacts.length === 0) {
        column.innerHTML = '<div class="kanban-empty">Пусто</div>';
      } else {
        contacts.forEach(contact => {
          const card = createKanbanCard(contact);
          column.appendChild(card);
        });
      }
    });
    
    // Инициализируем Sortable для drag & drop
    initSortable();
  }
  
  function initSortable() {
    const columns = document.querySelectorAll('.kanban-column-content');
    
    columns.forEach(column => {
      new Sortable(column, {
        group: 'kanban',
        animation: 150,
        ghostClass: 'kanban-card-ghost',
        dragClass: 'kanban-card-drag',
        onEnd: async function(evt) {
          const contactId = evt.item.dataset.contactId;
          const newStatus = evt.to.id.replace('kanban-col-', '');
          
          // Обновляем статус на сервере
          try {
            await api.patch(`/contacts/${contactId}/status`, {
              status: newStatus
            });
            
            showNotification(`Статус изменен на "${getStatusLabel(newStatus)}"`, 'success');
            
            // Обновляем счетчики
            loadKanbanContacts();
            
          } catch (error) {
            console.error('Error updating status:', error);
            showNotification(error.message || 'Ошибка обновления статуса', 'error');
            // Возвращаем карточку обратно
            loadKanbanContacts();
          }
        }
      });
    });
  }
  
  // ==================== Фильтры ====================
  statusBadges.forEach(badge => {
    badge.addEventListener('click', function() {
      // В Kanban режиме статусы не работают как фильтр
      if (currentView === 'kanban') {
        return;
      }
      
      statusBadges.forEach(b => b.classList.remove('active'));
      this.classList.add('active');
      
      currentStatus = this.dataset.status;
      currentPage = 0;
      loadContacts();
    });
  });
  
  // ==================== Поиск ====================
  searchInput.addEventListener('input', function() {
    clearTimeout(searchTimeout);
    
    searchTimeout = setTimeout(() => {
      searchQuery = this.value.trim();
      currentPage = 0;
      
      if (currentView === 'grid') {
        loadContacts();
      } else {
        loadKanbanContacts();
      }
    }, 500);
  });
  
  // ==================== Пагинация ====================
  prevBtn.addEventListener('click', () => {
    if (currentPage > 0) {
      currentPage--;
      loadContacts();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });
  
  nextBtn.addEventListener('click', () => {
    if (currentPage < totalPages - 1) {
      currentPage++;
      loadContacts();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });
  
  // ==================== Обновление ====================
  refreshBtn.addEventListener('click', () => {
    if (currentView === 'grid') {
      loadContacts();
    } else {
      loadKanbanContacts();
    }
    showNotification('Данные обновлены', 'info');
  });
  
  // ==================== View Toggle ====================
  gridViewBtn.addEventListener('click', () => switchView('grid'));
  kanbanViewBtn.addEventListener('click', () => switchView('kanban'));
  
  // ==================== Мобильное меню ====================
  mobileMenuToggle.addEventListener('click', function() {
    sidebar.classList.toggle('mobile-open');
    sidebarOverlay.classList.toggle('show');
  });
  
  sidebarOverlay.addEventListener('click', function() {
    sidebar.classList.remove('mobile-open');
    sidebarOverlay.classList.remove('show');
  });
  
  // ==================== Выпадающее меню пользователя ====================
  userMenuButton.addEventListener('click', function(e) {
    e.stopPropagation();
    userDropdown.classList.toggle('show');
  });
  
  document.addEventListener('click', function(e) {
    if (userDropdown.classList.contains('show') && !userDropdown.contains(e.target) && !userMenuButton.contains(e.target)) {
      userDropdown.classList.remove('show');
    }
  });
  
  // ==================== Закрытие уведомления ====================
  notificationClose.addEventListener('click', hideNotification);
  
  // ==================== Загрузка информации о пользователе ====================
  async function loadUserInfo() {
    try {
      const userInfo = await api.get('/users/me');
      
      const firstName = userInfo.first_name || '';
      const lastName = userInfo.last_name || '';
      
      if (firstName || lastName) {
        userEmailDisplay.textContent = firstName + (lastName ? ` ${lastName}` : '');
        
        let initials = '';
        if (firstName) initials += firstName[0];
        if (lastName) initials += lastName[0];
        userAvatar.textContent = initials.toUpperCase();
      } else if (userInfo.company_name) {
        userEmailDisplay.textContent = userInfo.company_name;
        userAvatar.textContent = userInfo.company_name.substring(0, 2).toUpperCase();
      } else {
        userEmailDisplay.textContent = userInfo.email;
        userAvatar.textContent = userInfo.email.substring(0, 2).toUpperCase();
      }
    } catch (error) {
      console.error('Ошибка загрузки информации о пользователе:', error);
    }
  }
  
  // ==================== Проверка авторизации ====================
  if (!api.isAuthenticated()) {
    window.location.href = '/static/login.html';
    return;
  }
  
  // ==================== Инициализация ====================
  loadUserInfo();
  
  // Restore view preference
  const savedView = localStorage.getItem('crm_view') || 'grid';
  switchView(savedView);
});
