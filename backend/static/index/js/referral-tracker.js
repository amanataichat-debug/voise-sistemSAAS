/**
 * Voicyfy - Referral Tracker
 * Handles UTM data capture and referral tracking (Silent Mode)
 */

class ReferralTracker {
  /**
   * Initialize referral tracking
   */
  static init() {
    console.log('🔗 Initializing Referral Tracker...');
    this.captureUTMData();
  }
  
  /**
   * Capture UTM parameters from URL
   */
  static captureUTMData() {
    const urlParams = new URLSearchParams(window.location.search);
    
    const utmData = {
      utm_source: urlParams.get('utm_source'),
      utm_medium: urlParams.get('utm_medium'),
      utm_campaign: urlParams.get('utm_campaign'),
      utm_content: urlParams.get('utm_content'),
      utm_term: urlParams.get('utm_term')
    };
    
    console.log('🔍 Checking UTM data:', utmData);
    
    if (utmData.utm_campaign && utmData.utm_source === 'partner') {
      localStorage.setItem('referral_code', utmData.utm_campaign);
      localStorage.setItem('utm_data', JSON.stringify(utmData));
      console.log('✅ Referral data captured:', utmData);
    }
  }
  
  /**
   * Check if current page is registration page
   * @returns {boolean}
   */
  static isRegistrationPage() {
    return document.querySelector('#register-form') !== null;
  }
  
  /**
   * Get stored referral data
   * @returns {Object|null} Referral data or null
   */
  static getReferralData() {
    const referralCode = localStorage.getItem('referral_code');
    const utmData = localStorage.getItem('utm_data');
    
    if (referralCode) {
      return {
        referral_code: referralCode,
        utm_data: utmData ? JSON.parse(utmData) : null
      };
    }
    
    return null;
  }
  
  /**
   * Clear stored referral data
   */
  static clearReferralData() {
    localStorage.removeItem('referral_code');
    localStorage.removeItem('utm_data');
    console.log('🧹 Referral data cleared');
  }
}
