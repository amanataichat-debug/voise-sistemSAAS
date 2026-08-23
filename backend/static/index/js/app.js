/**
 * Voksy AI - Main Application Logic
 * Handles tab switching, form submissions, and initialization
 */

document.addEventListener('DOMContentLoaded', function() {
  // Initialize global instances
  const emailVerification = new EmailVerification();
  ReferralTracker.init();
  
  // ========================================
  // TAB SWITCHING
  // ========================================
  const tabs = document.querySelectorAll('.auth-tab');
  const forms = document.querySelectorAll('.auth-form');
  const switchLinks = document.querySelectorAll('.switch-auth');
  const ctaButton = document.getElementById('cta-button');
  
  function switchTab(tabId) {
    tabs.forEach(tab => {
      tab.classList.toggle('active', tab.getAttribute('data-tab') === tabId);
    });
    
    forms.forEach(form => {
      form.classList.toggle('active', form.id === `${tabId}-form`);
    });
    
    // Clear notifications when switching tabs
    clearInlineNotification('register-notification-container');
    clearInlineNotification('login-notification-container');
  }
  
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const tabId = tab.getAttribute('data-tab');
      switchTab(tabId);
    });
  });
  
  switchLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const tabId = link.getAttribute('data-tab');
      switchTab(tabId);
    });
  });
  
  ctaButton.addEventListener('click', (e) => {
    e.preventDefault();
    switchTab('register');
    document.querySelector('.auth-section').scrollIntoView({ behavior: 'smooth' });
  });
  
  // ========================================
  // REGISTRATION HANDLER
  // ========================================
  const registerForm = document.getElementById('register-form');
  const registerButton = document.getElementById('register-submit-button');
  
  registerForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const email = document.getElementById('register-email').value;
    const password = document.getElementById('register-password').value;
    const firstName = document.getElementById('register-name').value;
    const companyName = document.getElementById('register-company').value;
    
    // Show loading notification immediately
    showInlineNotification('register-notification-container', 'loading', 'Отправляем код верификации на email...');
    
    // Disable button with spinner
    const originalButtonText = registerButton.innerHTML;
    registerButton.disabled = true;
    registerButton.innerHTML = '<div class="spinner"></div> Отправка...';
    
    try {
      const referralData = ReferralTracker.getReferralData();
      
      const userData = {
        email: email,
        password: password,
        first_name: firstName || null,
        last_name: null,
        company_name: companyName || null,
        referral_code: referralData?.referral_code || null,
        utm_data: referralData?.utm_data || null
      };
      
      console.log('📤 Registering user:', { ...userData, password: '[HIDDEN]' });
      
      const data = await api.register(userData);
      
      console.log('✅ Registration response:', data);
      
      // Update notification to success
      showInlineNotification('register-notification-container', 'success', 'Код отправлен! Проверьте email.');
      
      // Check if this is "account exists but not verified" scenario
      if (data.message && data.message.includes('exists but not verified')) {
        console.log('🔄 Account exists but not verified - showing verification');
        
        // Show verification section with warning message
        emailVerification.showVerificationSection(
          email,
          'Аккаунт уже существует. Новый код верификации отправлен на email!'
        );
        
        return;
      }
      
      // Normal new registration flow
      if (data.verification_required && data.verification_sent) {
        // Show verification section
        emailVerification.showVerificationSection(email);
        
        // Clear referral data
        ReferralTracker.clearReferralData();
        
      } else if (data.token) {
        // Old flow (if verification disabled)
        localStorage.setItem('auth_token', data.token);
        window.location.href = '/static/dashboard.html';
      }
      
    } catch (error) {
      console.error('❌ Registration error:', error);
      
      // Restore button
      registerButton.disabled = false;
      registerButton.innerHTML = originalButtonText;
      
      // Handle "Email already registered" for verified users
      if (error.message.includes('already registered')) {
        showInlineNotification('register-notification-container', 'error', 'Email уже зарегистрирован и подтверждён. Войдите в аккаунт.');
        setTimeout(() => switchTab('login'), 2000);
      } else {
        showInlineNotification('register-notification-container', 'error', error.message || 'Ошибка регистрации');
      }
    }
  });
  
  // ========================================
  // VERIFICATION HANDLERS
  // ========================================
  document.getElementById('verify-button').addEventListener('click', () => {
    emailVerification.verifyCode();
  });
  
  document.getElementById('resend-button').addEventListener('click', () => {
    emailVerification.resendCode();
  });
  
  // Enter key for verification
  document.getElementById('verification-code').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      emailVerification.verifyCode();
    }
  });
  
  // ========================================
  // LOGIN HANDLER
  // ========================================
  const loginForm = document.getElementById('login-form');
  const loginButton = document.getElementById('login-submit-button');
  
  loginForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    
    // Show loading
    const originalButtonText = loginButton.innerHTML;
    loginButton.disabled = true;
    loginButton.innerHTML = '<div class="spinner"></div> Вход...';
    showInlineNotification('login-notification-container', 'loading', 'Выполняется вход...');
    
    try {
      const data = await api.login({ email, password });
      
      localStorage.setItem('auth_token', data.token);
      
      showInlineNotification('login-notification-container', 'success', 'Успешный вход! Переходим...');
      
      setTimeout(() => {
        window.location.href = '/static/dashboard.html';
      }, 500);
      
    } catch (error) {
      console.error('❌ Login error:', error);
      
      // Restore button
      loginButton.disabled = false;
      loginButton.innerHTML = originalButtonText;
      
      // Check for unverified email
      if (error.message.includes('not verified') || error.message.includes('не подтвержден')) {
        showInlineNotification('login-notification-container', 'warning', 'Email не подтвержён! Проверьте почту для кода верификации.');
      } else if (error.message.includes('Invalid') || error.message.includes('password')) {
        showInlineNotification('login-notification-container', 'error', 'Неверный email или пароль');
      } else {
        showInlineNotification('login-notification-container', 'error', error.message || 'Ошибка входа');
      }
    }
  });
  
  // ========================================
  // CHECK IF ALREADY AUTHENTICATED
  // ========================================
  function checkAuth() {
    const token = localStorage.getItem('auth_token');
    
    if (token) {
      window.location.href = '/static/dashboard.html';
    }
  }
  
  checkAuth();
});
