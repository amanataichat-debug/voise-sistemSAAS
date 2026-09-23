import React from 'react';
import Icon from './Icon';

const ICONS = {
  success: 'circle-check',
  error: 'circle-alert',
  warning: 'triangle-alert',
  info: 'info',
};

// Единое уведомление под формой: loading | success | error | warning | info
function InlineNotification({ notification }) {
  if (!notification) return null;
  const { type, message } = notification;
  return (
    <div className={`note lp-inote lp-inote-${type}`} role="status">
      {type === 'loading' ? <span className="spin" /> : <Icon name={ICONS[type] || ICONS.info} className="ic-sm" />}
      <span>{message}</span>
    </div>
  );
}

export default InlineNotification;
