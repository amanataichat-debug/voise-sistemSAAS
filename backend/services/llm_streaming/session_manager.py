"""
Session Manager for WebSocket connections.

Manages mapping between session IDs and WebSocket connections.
Allows query_llm function to send streaming events to correct WebSocket.
"""

from typing import Dict, Optional
from fastapi import WebSocket
from backend.core.logging import get_logger

logger = get_logger(__name__)


class SessionManager:
    """
    Manages WebSocket sessions.
    
    Stores session_id -> WebSocket mapping for routing streaming events.
    """
    
    def __init__(self):
        """Initialize session manager."""
        self._sessions: Dict[str, Dict] = {}
        logger.info("[SESSION-MANAGER] Initialized")
    
    def register_session(
        self,
        session_id: str,
        websocket: WebSocket,
        assistant_id: str = None
    ):
        """
        Register a new WebSocket session.
        
        Args:
            session_id: Unique session identifier
            websocket: WebSocket connection object
            assistant_id: Optional assistant ID
        """
        self._sessions[session_id] = {
            "websocket": websocket,
            "assistant_id": assistant_id,
            "created_at": None  # можно добавить timestamp
        }
        
        logger.info(
            f"[SESSION-MANAGER] ✅ Registered session: {session_id} "
            f"(assistant: {assistant_id})"
        )
    
    def unregister_session(self, session_id: str):
        """
        Unregister a WebSocket session.
        
        Args:
            session_id: Session to remove
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"[SESSION-MANAGER] 🗑️ Unregistered session: {session_id}")
        else:
            logger.warning(f"[SESSION-MANAGER] ⚠️ Session not found: {session_id}")
    
    def get_websocket(self, session_id: str) -> Optional[WebSocket]:
        """
        Get WebSocket connection for session.
        
        Args:
            session_id: Session ID to lookup
            
        Returns:
            WebSocket object or None if not found
        """
        session = self._sessions.get(session_id)
        
        if session:
            return session["websocket"]
        
        logger.warning(f"[SESSION-MANAGER] ⚠️ WebSocket not found for session: {session_id}")
        return None
    
    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """
        Get full session information.
        
        Args:
            session_id: Session ID to lookup
            
        Returns:
            Session dict or None
        """
        return self._sessions.get(session_id)
    
    def get_active_sessions(self) -> int:
        """
        Get count of active sessions.
        
        Returns:
            Number of registered sessions
        """
        return len(self._sessions)
    
    def list_sessions(self) -> list:
        """
        List all active session IDs.
        
        Returns:
            List of session IDs
        """
        return list(self._sessions.keys())


# Global singleton instance
session_manager = SessionManager()
