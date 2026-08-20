/**
 * Voicyfy - API Client
 * Handles all API requests to the backend
 */

const api = {
  baseUrl: '/api',
  
  /**
   * Base fetch method with authentication
   * @param {string} endpoint - API endpoint
   * @param {Object} options - Fetch options
   * @returns {Promise<Object>} Response data
   */
  async fetch(endpoint, options = {}) {
    const token = localStorage.getItem('auth_token');
    
    if (token) {
      options.headers = {
        ...options.headers,
        'Authorization': `Bearer ${token}`
      };
    }
    
    if (options.body && typeof options.body !== 'string') {
      options.headers = {
        ...options.headers,
        'Content-Type': 'application/json'
      };
      options.body = JSON.stringify(options.body);
    }
    
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, options);
      const data = await response.json();
      
      if (!response.ok) {
        throw new Error(data.detail || data.message || 'API Error');
      }
      
      return data;
    } catch (error) {
      console.error('API Error:', error);
      throw error;
    }
  },
  
  /**
   * Register a new user
   * @param {Object} userData - User registration data
   * @returns {Promise<Object>} Registration response
   */
  register(userData) {
    return this.fetch('/auth/register', {
      method: 'POST',
      body: userData
    });
  },
  
  /**
   * Login user
   * @param {Object} credentials - Login credentials (email, password)
   * @returns {Promise<Object>} Login response with token
   */
  login(credentials) {
    return this.fetch('/auth/login', {
      method: 'POST',
      body: credentials
    });
  },
  
  /**
   * Verify email with code
   * @param {Object} data - Verification data (email, code)
   * @returns {Promise<Object>} Verification response
   */
  verifyEmail(data) {
    return this.fetch('/email-verification/verify', {
      method: 'POST',
      body: data
    });
  },
  
  /**
   * Resend verification code
   * @param {Object} data - Email data
   * @returns {Promise<Object>} Resend response
   */
  resendVerificationCode(data) {
    return this.fetch('/email-verification/resend', {
      method: 'POST',
      body: data
    });
  }
};
