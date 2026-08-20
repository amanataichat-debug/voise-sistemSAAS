import { useEffect } from 'react';

export function useAuth() {
  useEffect(() => {
    const token = localStorage.getItem('auth_token');
    if (token) {
      window.location.href = '/static/dashboard.html';
    }
  }, []);
}
