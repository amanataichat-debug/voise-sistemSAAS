import React from 'react';

const icons = {
  success: 'fas fa-check-circle',
  error: 'fas fa-exclamation-circle',
  warning: 'fas fa-exclamation-triangle',
  info: 'fas fa-info-circle',
};

function InlineNotification({ notification }) {
  if (!notification) return null;

  return (
    <div className={`inline-notification ${notification.type}`}>
      {notification.type === 'loading' ? (
        <div className="spinner" />
      ) : (
        <i className={icons[notification.type] || icons.info} />
      )}
      <span>{notification.message}</span>
    </div>
  );
}

export default InlineNotification;
