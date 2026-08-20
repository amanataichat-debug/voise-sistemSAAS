import { useEffect } from 'react';

export function useReferralTracker() {
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);

    const utmData = {
      utm_source: urlParams.get('utm_source'),
      utm_medium: urlParams.get('utm_medium'),
      utm_campaign: urlParams.get('utm_campaign'),
      utm_content: urlParams.get('utm_content'),
      utm_term: urlParams.get('utm_term')
    };

    if (utmData.utm_campaign && utmData.utm_source === 'partner') {
      localStorage.setItem('referral_code', utmData.utm_campaign);
      localStorage.setItem('utm_data', JSON.stringify(utmData));
    }
  }, []);

  const getReferralData = () => {
    const referralCode = localStorage.getItem('referral_code');
    const utmData = localStorage.getItem('utm_data');

    if (referralCode) {
      return {
        referral_code: referralCode,
        utm_data: utmData ? JSON.parse(utmData) : null
      };
    }

    return null;
  };

  const clearReferralData = () => {
    localStorage.removeItem('referral_code');
    localStorage.removeItem('utm_data');
  };

  return { getReferralData, clearReferralData };
}
