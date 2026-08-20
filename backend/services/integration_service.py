"""
Integration service module for WellcomeAI application.
"""

from sqlalchemy.orm import Session
from typing import List, Dict, Any
import uuid
from fastapi import HTTPException, status

from backend.models.integration import Integration
from backend.core.logging import get_logger

logger = get_logger(__name__)

class IntegrationService:
    @staticmethod
    async def get_integrations(db: Session, assistant_id: str) -> List[Dict[str, Any]]:
        """
        Get all integrations for an assistant.
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            
        Returns:
            List of integrations
        """
        try:
            integrations = db.query(Integration).filter(Integration.assistant_id == assistant_id).all()
            
            result = []
            for integration in integrations:
                result.append({
                    "id": str(integration.id),
                    "assistant_id": str(integration.assistant_id),
                    "name": integration.name,
                    "type": integration.type,
                    "webhook_url": integration.webhook_url,
                    "is_active": integration.is_active,
                    "created_at": integration.created_at,
                    "updated_at": integration.updated_at
                })
            
            return result
        except Exception as e:
            logger.error(f"Error getting integrations: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error getting integrations: {str(e)}"
            )
    
    @staticmethod
    async def create_integration(db: Session, assistant_id: str, integration_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new integration for an assistant.
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            integration_data: Integration data
            
        Returns:
            New integration
        """
        try:
            # Validate required fields
            required_fields = ["name", "type", "webhook_url"]
            for field in required_fields:
                if field not in integration_data:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Missing required field: {field}"
                    )
            
            # Convert assistant_id to UUID if it's a string
            try:
                assistant_uuid = uuid.UUID(assistant_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid assistant ID format"
                )
            
            # Create integration
            integration = Integration(
                assistant_id=assistant_uuid,
                name=integration_data["name"],
                type=integration_data["type"],
                webhook_url=integration_data["webhook_url"],
                is_active=integration_data.get("is_active", True)
            )
            
            db.add(integration)
            db.commit()
            db.refresh(integration)
            
            return {
                "id": str(integration.id),
                "assistant_id": str(integration.assistant_id),
                "name": integration.name,
                "type": integration.type,
                "webhook_url": integration.webhook_url,
                "is_active": integration.is_active,
                "created_at": integration.created_at,
                "updated_at": integration.updated_at
            }
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error creating integration: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error creating integration: {str(e)}"
            )
    
    @staticmethod
    async def update_integration(db: Session, assistant_id: str, integration_id: str, integration_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing integration.
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            integration_id: Integration ID
            integration_data: Integration data
            
        Returns:
            Updated integration
        """
        try:
            # Find integration
            try:
                integration_uuid = uuid.UUID(integration_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid integration ID format"
                )
                
            try:
                assistant_uuid = uuid.UUID(assistant_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid assistant ID format"
                )
            
            integration = db.query(Integration).filter(
                Integration.id == integration_uuid,
                Integration.assistant_id == assistant_uuid
            ).first()
            
            if not integration:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Integration not found"
                )
            
            # Update fields
            if "name" in integration_data:
                integration.name = integration_data["name"]
            
            if "type" in integration_data:
                integration.type = integration_data["type"]
            
            if "webhook_url" in integration_data:
                integration.webhook_url = integration_data["webhook_url"]
            
            if "is_active" in integration_data:
                integration.is_active = integration_data["is_active"]
            
            db.commit()
            db.refresh(integration)
            
            return {
                "id": str(integration.id),
                "assistant_id": str(integration.assistant_id),
                "name": integration.name,
                "type": integration.type,
                "webhook_url": integration.webhook_url,
                "is_active": integration.is_active,
                "created_at": integration.created_at,
                "updated_at": integration.updated_at
            }
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating integration: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating integration: {str(e)}"
            )
    
    @staticmethod
    async def delete_integration(db: Session, assistant_id: str, integration_id: str) -> None:
        """
        Delete an integration.
        
        Args:
            db: Database session
            assistant_id: Assistant ID
            integration_id: Integration ID
        """
        try:
            # Find integration
            try:
                integration_uuid = uuid.UUID(integration_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid integration ID format"
                )
                
            try:
                assistant_uuid = uuid.UUID(assistant_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid assistant ID format"
                )
            
            integration = db.query(Integration).filter(
                Integration.id == integration_uuid,
                Integration.assistant_id == assistant_uuid
            ).first()
            
            if not integration:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Integration not found"
                )
            
            # Delete integration
            db.delete(integration)
            db.commit()
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting integration: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error deleting integration: {str(e)}"
            )
