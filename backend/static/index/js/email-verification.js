/**
 * Voicyfy - Email Verification
 * Handles email verification flow
 */

class EmailVerification {
  constructor() {
    this.attempts = 3;
    this.resendCooldown = 60;
    this.timerInterval = null;
    this.userEmail = null;
  }

  /**
   * Show verification section and start the flow
   * @param {string} email - User's email address
   * @param {string|null} message - Optional custom message for the notice
   */
  showVerificationSection(email, message = null) {
    this.userEmail = email;
    
    // Hide register button
    document.getElementById('register-submit-button').style.display = 'none';
    
    // Show verification section
    const section = document.getElementById('verification-section');
    section.style.display = 'block';
    
    // Set email in notification
    document.getElementById('verification-email').textContent = email;
    
    // Update notice message if provided (for "account exists" scenario)
    if (message) {
      const noticeElement = document.getElementById('verification-notice');
      noticeElement.className = 'verification-notice warning';
      noticeElement.innerHTML = `<i class="fas fa-info-circle"></i> ${message}`;
    }
    
    // Start resend timer
    this.startResendTimer();
    
    // Focus on code input
    document.getElementById('verification-code').focus();
  }

  /**
   * Start the resend cooldown timer
   */
  startResendTimer() {
    let seconds = this.resendCooldown;
    const timerElement = document.getElementById('timer-seconds');
    const timerContainer = document.getElementById('resend-timer');
    const resendButton = document.getElementById('resend-button');
    
    // Show timer, hide button
    timerContainer.style.display = 'inline';
    resendButton.style.display = 'none';
    resendButton.disabled = true;
    
    // Clear previous timer
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
    }
    
    this.timerInterval = setInterval(() => {
      seconds--;
      timerElement.textContent = seconds;
      
      if (seconds <= 0) {
        clearInterval(this.timerInterval);
        timerContainer.style.display = 'none';
        resendButton.style.display = 'block';
        resendButton.disabled = false;
      }
    }, 1000);
  }

  /**
   * Update the attempts display with appropriate styling
   */
  updateAttemptsDisplay() {
    const attemptsElement = document.getElementById('attempts-left');
    attemptsElement.textContent = `Осталось попыток: ${this.attempts}`;
    
    // Change color based on attempts
    attemptsElement.classList.remove('warning', 'danger');
    if (this.attempts === 2) {
      attemptsElement.classList.add('warning');
    } else if (this.attempts === 1) {
      attemptsElement.classList.add('danger');
    }
  }

  /**
   * Verify the entered code
   */
  async verifyCode() {
    const code = document.getElementById('verification-code').value.trim();
    
    if (code.length !== 6 || !/^\d{6}$/.test(code)) {
      showInlineNotification('verification-notification-container', 'error', 'Введите 6-значный код');
      return;
    }
    
    // Show loading in button
    const verifyButton = document.getElementById('verify-button');
    const originalText = verifyButton.innerHTML;
    verifyButton.disabled = true;
    verifyButton.innerHTML = '<div class="spinner"></div> Проверяем...';
    
    try {
      const response = await api.verifyEmail({
        email: this.userEmail,
        code: code
      });
      
      console.log('✅ Email verified:', response);
      
      // Save token
      localStorage.setItem('auth_token', response.data.token);
      
      // Show success notification
      showInlineNotification('verification-notification-container', 'success', 'Email подтвержден! Переходим в dashboard...');
      
      // Redirect immediately
      setTimeout(() => {
        window.location.href = '/static/dashboard.html';
      }, 500);
      
    } catch (error) {
      console.error('❌ Verification error:', error);
      
      // Restore button
      verifyButton.disabled = false;
      verifyButton.innerHTML = originalText;
      
      // Decrease attempts
      this.attempts--;
      this.updateAttemptsDisplay();
      
      if (this.attempts === 0) {
        showInlineNotification('verification-notification-container', 'error', 'Исчерпаны попытки ввода кода. Запросите новый код.');
        document.getElementById('verification-code').disabled = true;
        document.getElementById('verify-button').disabled = true;
      } else {
        showInlineNotification('verification-notification-container', 'error', `Неверный код. Осталось попыток: ${this.attempts}`);
        document.getElementById('verification-code').value = '';
        document.getElementById('verification-code').focus();
      }
    }
  }

  /**
   * Resend verification code
   */
  async resendCode() {
    const resendButton = document.getElementById('resend-button');
    const originalText = resendButton.innerHTML;
    resendButton.disabled = true;
    resendButton.innerHTML = '<div class="spinner"></div> Отправка...';
    
    try {
      await api.resendVerificationCode({ email: this.userEmail });
      
      // Reset attempts
      this.attempts = 3;
      this.updateAttemptsDisplay();
      
      // Clear code field
      document.getElementById('verification-code').value = '';
      document.getElementById('verification-code').disabled = false;
      document.getElementById('verify-button').disabled = false;
      
      // Start timer again
      this.startResendTimer();
      
      showInlineNotification('verification-notification-container', 'success', 'Новый код отправлен на email!');
      
    } catch (error) {
      console.error('❌ Resend error:', error);
      
      resendButton.disabled = false;
      resendButton.innerHTML = originalText;
      
      if (error.message.includes('wait') || error.message.includes('подождите')) {
        showInlineNotification('verification-notification-container', 'warning', 'Подождите перед повторной отправкой');
      } else {
        showInlineNotification('verification-notification-container', 'error', 'Ошибка отправки кода. Попробуйте позже.');
      }
    }
  }
}
