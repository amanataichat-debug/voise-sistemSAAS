export function createNotification(type, message) {
  return { type, message, id: Date.now() };
}
