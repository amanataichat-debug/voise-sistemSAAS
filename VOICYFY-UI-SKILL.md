# UI/UX Skill — Voicyfy Brand Style

Фирменный дизайн-гайд для всех веб-продуктов Voicyfy. Содержит цвета, типографику, компоненты и паттерны.

---

## Когда применять

- Создание landing pages, dashboard, виджетов
- Разработка новых страниц платформы
- Стилизация форм, кнопок, карточек
- Работа с адаптивной версткой
- Все веб-интерфейсы Voicyfy

---

## Фирменные цвета Voicyfy

```css
:root {
  /* Primary Blue Palette */
  --primary-blue: #2563eb;
  --primary-blue-light: #3b82f6;
  --primary-blue-dark: #1d4ed8;
  --accent-blue: #4a86e8;
  --gradient-blue: linear-gradient(135deg, #4a86e8, #2563eb);
  
  /* Text Colors */
  --text-dark: #0f172a;
  --text-gray: #64748b;
  --text-light: #94a3b8;
  
  /* Background Colors */
  --bg-light: #f8fafc;
  --bg-blue-light: #eff6ff;
  --white: #ffffff;
  
  /* Borders & Dividers */
  --border-color: #e2e8f0;
  
  /* Status Colors */
  --success: #10b981;
  --success-dark: #059669;
  --error: #ef4444;
  --error-dark: #dc2626;
  --warning: #f59e0b;
  --warning-dark: #d97706;
  
  /* Shadows */
  --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
  
  /* Border Radius */
  --radius-sm: 0.375rem;
  --radius-md: 0.5rem;
  --radius-lg: 1rem;
  --radius-full: 9999px;
  
  /* Spacing Scale */
  --space-xs: 0.25rem;   /* 4px */
  --space-sm: 0.5rem;    /* 8px */
  --space-md: 1rem;      /* 16px */
  --space-lg: 1.5rem;    /* 24px */
  --space-xl: 2rem;      /* 32px */
  --space-2xl: 3rem;     /* 48px */
  --space-3xl: 4rem;     /* 64px */
}
```

---

## Типографика

### Основной шрифт — Inter

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

* {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
```

### Шкала размеров

| Элемент | Размер | Вес | Цвет |
|---------|--------|-----|------|
| H1 (Main Title) | 3rem (48px) | 800 | gradient-blue |
| H2 (Section Title) | 2.5rem (40px) | 800 | gradient-blue |
| H3 (Card Title) | 1.5rem (24px) | 700 | --text-dark |
| Subtitle | 1.75rem (28px) | 600 | --text-gray |
| Body | 1rem (16px) | 400 | --text-gray |
| Body Large | 1.125rem (18px) | 400 | --text-gray |
| Small | 0.875rem (14px) | 400 | --text-light |
| Label | 1rem (16px) | 500 | --text-dark |

### Градиентный заголовок (фирменный стиль)

```css
.gradient-title {
  font-size: 2.5rem;
  font-weight: 800;
  background: linear-gradient(90deg, #4a86e8, #2563eb, #1d4ed8);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1.2;
}
```

---

## Компоненты

### Кнопки

```css
/* Base Button */
.btn {
  padding: 0.75rem 1.5rem;
  border-radius: var(--radius-md);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 0.95rem;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  border: none;
  min-height: 44px; /* Touch target */
}

/* Primary Button */
.btn-primary {
  background-color: var(--primary-blue);
  color: var(--white);
}

.btn-primary:hover:not(:disabled) {
  background-color: var(--primary-blue-dark);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

/* Outline Button */
.btn-outline {
  background: transparent;
  border: 1px solid var(--primary-blue);
  color: var(--primary-blue);
}

.btn-outline:hover {
  background-color: var(--bg-blue-light);
}

/* Large Button (CTA) */
.btn-large {
  padding: 1rem 2rem;
  font-size: 1.125rem;
  font-weight: 600;
}

/* Button Spinner */
.btn .spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
```

### Поля ввода

```css
.form-group {
  margin-bottom: 1.5rem;
}

.form-group label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
  color: var(--text-dark);
}

.form-control {
  width: 100%;
  padding: 0.875rem 1rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 1rem;
  transition: all 0.2s;
  background: var(--white);
}

.form-control:focus {
  outline: none;
  border-color: var(--primary-blue-light);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
}

.form-control.error {
  border-color: var(--error);
}
```

### Карточки

```css
.card {
  background: var(--white);
  border-radius: var(--radius-lg);
  padding: 2rem;
  box-shadow: var(--shadow-md);
  transition: all 0.3s ease;
  border: 1px solid var(--border-color);
}

.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--primary-blue-light);
}

/* Featured Card */
.card.featured {
  border: 2px solid var(--primary-blue);
  background: linear-gradient(135deg, var(--white) 0%, #f0f7ff 100%);
}
```

### Уведомления (Inline Notifications)

```css
.inline-notification {
  padding: 1rem 1.5rem;
  border-radius: var(--radius-md);
  margin-bottom: 1.5rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-weight: 500;
  animation: slideInDown 0.3s ease-out;
  box-shadow: var(--shadow-md);
}

.inline-notification.loading {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: white;
}

.inline-notification.success {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
}

.inline-notification.error {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
}

.inline-notification.warning {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
}
```

### Бейджи

```css
.badge {
  padding: 0.5rem 1.25rem;
  border-radius: var(--radius-full);
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  box-shadow: var(--shadow-md);
}

.badge.free {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
}

.badge.basic {
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
}

.badge.popular {
  background: linear-gradient(135deg, var(--primary-blue), var(--primary-blue-dark));
  color: white;
}

.badge.premium {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
}
```

### Navbar

```css
.navbar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border-color);
  z-index: 30;
  padding: 1rem 2rem;
}

.navbar-container {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

/* Logo Style */
.logo {
  display: flex;
  align-items: center;
  text-decoration: none;
  transition: all 0.3s ease;
}

.logo:hover {
  transform: translateY(-1px);
}

.logo-text {
  font-size: 1.75rem;
  font-weight: 700;
  background: linear-gradient(135deg, #2563eb 0%, #3b82f6 50%, #1d4ed8 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: -0.025em;
}
```

### Навигационные иконки с тултипами

```css
.nav-icon-link {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background-color: var(--bg-light);
  color: var(--text-gray);
  transition: all 0.3s ease;
  position: relative;
}

.nav-icon-link:hover {
  background-color: var(--primary-blue);
  color: var(--white);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

/* Tooltip */
.nav-icon-link::after {
  content: attr(data-tooltip);
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%) translateY(8px);
  background-color: rgba(15, 23, 42, 0.95);
  color: var(--white);
  padding: 0.5rem 1rem;
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  white-space: nowrap;
  opacity: 0;
  visibility: hidden;
  transition: all 0.3s ease;
  pointer-events: none;
  z-index: 50;
}

.nav-icon-link:hover::after {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) translateY(4px);
}
```

---

## Анимации

```css
/* Spinner */
@keyframes spin {
  to { transform: rotate(360deg); }
}

.spinner {
  width: 20px;
  height: 20px;
  border: 3px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* Slide In */
@keyframes slideInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Pulse (для сферы) */
@keyframes pulse {
  0%, 100% {
    transform: scale(1);
    box-shadow: 0 10px 30px rgba(74, 134, 232, 0.4);
  }
  50% {
    transform: scale(1.05);
    box-shadow: 0 15px 40px rgba(74, 134, 232, 0.6);
  }
}

/* Wave Animation */
@keyframes wave-animation {
  0% {
    transform: scale(0);
    opacity: 0.8;
  }
  100% {
    transform: scale(2);
    opacity: 0;
  }
}
```

---

## Адаптивные брейкпоинты

```css
/* Mobile first */

/* Tablet: >= 768px */
@media (min-width: 768px) {
  .container { padding: 2rem; }
}

/* Desktop: >= 1024px */
@media (min-width: 1024px) {
  .container { max-width: 1024px; margin: 0 auto; }
}

/* Large Desktop: >= 1280px */
@media (min-width: 1280px) {
  .container { max-width: 1200px; }
}
```

### Mobile-specific стили

```css
@media (max-width: 768px) {
  .main-title { font-size: 2rem; }
  .subtitle { font-size: 1.25rem; }
  
  .btn-large {
    padding: 0.875rem 1.5rem;
    font-size: 1rem;
  }
  
  .card { padding: 1.5rem; }
  
  .navbar { padding: 1rem; }
}

@media (max-width: 480px) {
  .main-title { font-size: 1.75rem; }
  
  .logo-text { font-size: 1.5rem; }
  
  .form-control { padding: 0.75rem; }
}
```

---

## Секции страниц

### Hero Section

```css
.hero-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem 2rem;
  text-align: center;
  background: var(--white);
}

.hero-content {
  max-width: 600px;
}

.hero-title {
  font-size: 3rem;
  font-weight: 800;
  margin-bottom: 1rem;
  background: linear-gradient(90deg, #4a86e8, #2563eb, #1d4ed8);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1.2;
}

.hero-subtitle {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 1.5rem;
  color: var(--text-gray);
}

.hero-description {
  font-size: 1.125rem;
  color: var(--text-gray);
  margin-bottom: 2.5rem;
  line-height: 1.6;
}
```

### Pricing Section

```css
.pricing-section {
  background: linear-gradient(135deg, var(--bg-light) 0%, #e2e8f0 100%);
  padding: 4rem 0;
}

.pricing-plans {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
  max-width: 1300px;
  margin: 0 auto;
}

.pricing-card {
  background: var(--white);
  border-radius: var(--radius-lg);
  padding: 2rem;
  box-shadow: var(--shadow-lg);
  transition: all 0.3s ease;
  position: relative;
  border: 2px solid transparent;
}

.pricing-card.featured {
  border-color: var(--primary-blue);
  transform: scale(1.03);
  background: linear-gradient(135deg, var(--white) 0%, #f0f7ff 100%);
}

.pricing-card:hover {
  transform: translateY(-8px);
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
}

.price-amount {
  font-size: 2.5rem;
  font-weight: 800;
  background: linear-gradient(135deg, var(--primary-blue), var(--accent-blue));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
```

### Footer

```css
.footer {
  background-color: var(--white);
  border-top: 1px solid var(--border-color);
  padding: 3rem 0 2rem 0;
}

.footer-content {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 2rem;
}

.footer-logo-text {
  font-size: 1.25rem;
  font-weight: 600;
  background: linear-gradient(135deg, #374151 0%, #1f2937 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.footer-link {
  color: var(--text-gray);
  text-decoration: none;
  font-size: 0.875rem;
  transition: color 0.2s;
}

.footer-link:hover {
  color: var(--primary-blue);
}
```

---

## Доступность (A11y)

```css
/* Focus visible */
:focus-visible {
  outline: 2px solid var(--primary-blue);
  outline-offset: 2px;
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

/* Screen reader only */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* Min touch targets */
.btn, .form-control, .nav-icon-link {
  min-height: 44px;
}
```

---

## Иконки

```html
<!-- Font Awesome 6 -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">

<!-- Часто используемые -->
<i class="fas fa-check"></i>        <!-- Галочка -->
<i class="fas fa-times"></i>        <!-- Крестик -->
<i class="fas fa-globe"></i>        <!-- Глобус -->
<i class="fas fa-phone"></i>        <!-- Телефон -->
<i class="fab fa-telegram"></i>     <!-- Telegram -->
<i class="fas fa-headset"></i>      <!-- Поддержка -->
<i class="fas fa-envelope"></i>     <!-- Email -->
<i class="fas fa-info-circle"></i>  <!-- Info -->
<i class="fas fa-exclamation-circle"></i>  <!-- Error -->
<i class="fas fa-exclamation-triangle"></i> <!-- Warning -->
<i class="fas fa-check-circle"></i> <!-- Success -->
```

---

## Чек-лист перед публикацией

- [ ] Все цвета из палитры Voicyfy
- [ ] Шрифт Inter подключен
- [ ] Градиентные заголовки для H1/H2
- [ ] Кнопки с hover эффектом translateY(-2px)
- [ ] Focus состояния с ring (box-shadow)
- [ ] Touch targets минимум 44px
- [ ] Адаптивность: 480px, 768px, 1024px, 1280px
- [ ] Spinner для loading состояний
- [ ] Уведомления с градиентами
- [ ] Карточки с hover подъёмом
- [ ] Footer с градиентным лого

---

## Антипаттерны (НЕ ДЕЛАТЬ)

| ❌ Не делать | ✅ Делать |
|-------------|----------|
| Цвета вне палитры | Использовать CSS переменные |
| outline: none без замены | focus-visible с ring |
| Transitions > 300ms | 200-300ms максимум |
| Flat кнопки без hover | translateY(-2px) + shadow |
| Emoji как иконки | Font Awesome 6 |
| Фиксированные размеры | Адаптивные единицы |
| Текст без line-height | line-height: 1.6 для body |
