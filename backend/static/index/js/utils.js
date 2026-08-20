/**
 * Voicyfy - Utility Functions
 * Inline notifications and helper functions
 */

/**
 * Show inline notification in a container
 * @param {string} containerId - ID of the container element
 * @param {string} type - Type: 'loading', 'success', 'error', 'warning', 'info'
 * @param {string} message - Notification message
 * @param {string|null} iconClass - Optional custom icon class
 */
function showInlineNotification(containerId, type, message, iconClass = null) {
  const container = document.getElementById(containerId);
  if (!container) return;
  
  let icon = '';
  if (type === 'loading') {
    icon = '<div class="spinner"></div>';
  } else if (iconClass) {
    icon = `<i class="${iconClass}"></i>`;
  } else {
    const defaultIcons = {
      success: 'fas fa-check-circle',
      error: 'fas fa-exclamation-circle',
      warning: 'fas fa-exclamation-triangle',
      info: 'fas fa-info-circle'
    };
    icon = `<i class="${defaultIcons[type] || defaultIcons.info}"></i>`;
  }
  
  container.innerHTML = `
    <div class="inline-notification ${type}">
      ${icon}
      <span>${message}</span>
    </div>
  `;
}

/**
 * Clear inline notification from a container
 * @param {string} containerId - ID of the container element
 */
function clearInlineNotification(containerId) {
  const container = document.getElementById(containerId);
  if (container) {
    container.innerHTML = '';
  }
}
