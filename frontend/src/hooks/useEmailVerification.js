import { useState, useEffect, useRef, useCallback } from 'react';
import api from '../utils/api';

export function useEmailVerification(email, onVerified) {
  const [attempts, setAttempts] = useState(3);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [isTimerActive, setIsTimerActive] = useState(false);
  const [notification, setNotification] = useState(null);
  const [isVerifying, setIsVerifying] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [codeDisabled, setCodeDisabled] = useState(false);
  const timerRef = useRef(null);

  const startTimer = useCallback(() => {
    setSecondsLeft(60);
    setIsTimerActive(true);
  }, []);

  useEffect(() => {
    if (!isTimerActive) return;

    timerRef.current = setInterval(() => {
      setSecondsLeft(prev => {
        if (prev <= 1) {
          clearInterval(timerRef.current);
          setIsTimerActive(false);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, [isTimerActive]);

  // Start timer on mount
  useEffect(() => {
    startTimer();
  }, [startTimer]);

  const verifyCode = useCallback(async (code) => {
    if (code.length !== 6 || !/^\d{6}$/.test(code)) {
      setNotification({ type: 'error', message: 'Введите 6-значный код' });
      return;
    }

    setIsVerifying(true);
    setNotification(null);

    try {
      const response = await api.verifyEmail({
        email: email,
        code: code
      });

      localStorage.setItem('auth_token', response.data.token);

      setNotification({ type: 'success', message: 'Email подтвержден! Переходим в dashboard...' });

      setTimeout(() => {
        window.location.href = '/static/dashboard.html';
      }, 500);

    } catch (error) {
      setIsVerifying(false);

      const newAttempts = attempts - 1;
      setAttempts(newAttempts);

      if (newAttempts === 0) {
        setNotification({ type: 'error', message: 'Исчерпаны попытки ввода кода. Запросите новый код.' });
        setCodeDisabled(true);
      } else {
        setNotification({ type: 'error', message: `Неверный код. Осталось попыток: ${newAttempts}` });
      }
    }
  }, [email, attempts]);

  const resendCode = useCallback(async () => {
    setIsResending(true);
    setNotification(null);

    try {
      await api.resendVerificationCode({ email: email });

      setAttempts(3);
      setCodeDisabled(false);
      startTimer();

      setNotification({ type: 'success', message: 'Новый код отправлен на email!' });

    } catch (error) {
      if (error.message.includes('wait') || error.message.includes('подождите')) {
        setNotification({ type: 'warning', message: 'Подождите перед повторной отправкой' });
      } else {
        setNotification({ type: 'error', message: 'Ошибка отправки кода. Попробуйте позже.' });
      }
    } finally {
      setIsResending(false);
    }
  }, [email, startTimer]);

  return {
    attempts,
    secondsLeft,
    isTimerActive,
    notification,
    isVerifying,
    isResending,
    codeDisabled,
    verifyCode,
    resendCode
  };
}
