"""
Репозитории для работы с различными моделями.
Содержит конкретные реализации CRUD операций для моделей проекта.
"""

import uuid
from typing import List, Optional, Dict, Any, Union

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, not_

from backend.db.base import CRUDBase
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.file import File
from backend.schemas.user import UserCreate, UserUpdate
from backend.schemas.assistant import AssistantCreate, AssistantUpdate
from backend.schemas.conversation import ConversationCreate, ConversationUpdate
from backend.schemas.file import FileCreate, FileUpdate


class UserRepository(CRUDBase[User, UserCreate, UserUpdate]):
    """
    Репозиторий для работы с пользователями.
    """
    
    def get_by_email(self, db: Session, *, email: str) -> Optional[User]:
        """
        Получить пользователя по email.
        
        Args:
            db (Session): Сессия SQLAlchemy
            email (str): Email пользователя
            
        Returns:
            Optional[User]: Найденный пользователь или None
        """
        return db.query(User).filter(User.email == email).first()
    
    def create_with_hashed_password(self, db: Session, *, obj_in: UserCreate, hashed_password: str) -> User:
        """
        Создать пользователя с хешированным паролем.
        
        Args:
            db (Session): Сессия SQLAlchemy
            obj_in (UserCreate): Данные пользователя
            hashed_password (str): Хешированный пароль
            
        Returns:
            User: Созданный пользователь
        """
        db_obj = User(
            email=obj_in.email,
            password_hash=hashed_password,
            first_name=obj_in.first_name,
            last_name=obj_in.last_name,
            company_name=obj_in.company_name,
            subscription_plan="free"
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj


class AssistantRepository(CRUDBase[AssistantConfig, AssistantCreate, AssistantUpdate]):
    """
    Репозиторий для работы с ассистентами.
    """
    
    def get_by_user_id(self, db: Session, *, user_id: uuid.UUID) -> List[AssistantConfig]:
        """
        Получить список ассистентов пользователя.
        
        Args:
            db (Session): Сессия SQLAlchemy
            user_id (uuid.UUID): Идентификатор пользователя
            
        Returns:
            List[AssistantConfig]: Список ассистентов
        """
        return db.query(AssistantConfig).filter(AssistantConfig.user_id == user_id).all()
    
    def get_by_user_and_id(self, db: Session, *, user_id: uuid.UUID, assistant_id: uuid.UUID) -> Optional[AssistantConfig]:
        """
        Получить ассистента по ID и ID пользователя.
        
        Args:
            db (Session): Сессия SQLAlchemy
            user_id (uuid.UUID): Идентификатор пользователя
            assistant_id (uuid.UUID): Идентификатор ассистента
            
        Returns:
            Optional[AssistantConfig]: Найденный ассистент или None
        """
        return db.query(AssistantConfig).filter(
            and_(
                AssistantConfig.id == assistant_id,
                AssistantConfig.user_id == user_id
            )
        ).first()


class ConversationRepository(CRUDBase[Conversation, ConversationCreate, ConversationUpdate]):
    """
    Репозиторий для работы с диалогами.
    """
    
    def get_by_assistant_id(self, db: Session, *, assistant_id: uuid.UUID, skip: int = 0, limit: int = 100) -> List[Conversation]:
        """
        Получить список диалогов ассистента.
        
        Args:
            db (Session): Сессия SQLAlchemy
            assistant_id (uuid.UUID): Идентификатор ассистента
            skip (int, optional): Количество объектов для пропуска. По умолчанию 0.
            limit (int, optional): Максимальное количество возвращаемых объектов. По умолчанию 100.
            
        Returns:
            List[Conversation]: Список диалогов
        """
        return db.query(Conversation).filter(
            Conversation.assistant_id == assistant_id
        ).order_by(Conversation.created_at.desc()).offset(skip).limit(limit).all()
    
    def get_count_by_assistant_id(self, db: Session, *, assistant_id: uuid.UUID) -> int:
        """
        Получить количество диалогов ассистента.
        
        Args:
            db (Session): Сессия SQLAlchemy
            assistant_id (uuid.UUID): Идентификатор ассистента
            
        Returns:
            int: Количество диалогов
        """
        return db.query(Conversation).filter(
            Conversation.assistant_id == assistant_id
        ).count()


class FileRepository(CRUDBase[File, FileCreate, FileUpdate]):
    """
    Репозиторий для работы с файлами.
    """
    
    def get_by_assistant_id(self, db: Session, *, assistant_id: uuid.UUID) -> List[File]:
        """
        Получить список файлов ассистента.
        
        Args:
            db (Session): Сессия SQLAlchemy
            assistant_id (uuid.UUID): Идентификатор ассистента
            
        Returns:
            List[File]: Список файлов
        """
        return db.query(File).filter(File.assistant_id == assistant_id).all()
    
    def get_by_filename(self, db: Session, *, assistant_id: uuid.UUID, filename: str) -> Optional[File]:
        """
        Получить файл по имени.
        
        Args:
            db (Session): Сессия SQLAlchemy
            assistant_id (uuid.UUID): Идентификатор ассистента
            filename (str): Имя файла
            
        Returns:
            Optional[File]: Найденный файл или None
        """
        return db.query(File).filter(
            and_(
                File.assistant_id == assistant_id,
                File.name == filename
            )
        ).first()


# Создаем экземпляры репозиториев для использования в сервисах
user_repository = UserRepository(User)
assistant_repository = AssistantRepository(AssistantConfig)
conversation_repository = ConversationRepository(Conversation)
file_repository = FileRepository(File)
