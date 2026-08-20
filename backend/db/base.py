"""
Базовые классы и функции для работы с БД.
Содержит базовый класс для всех моделей SQLAlchemy и общие функции для работы с БД.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

from sqlalchemy import Column, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base, DeclarativeMeta
from sqlalchemy.orm import Session

# Создаем базовый класс для всех моделей SQLAlchemy
Base = declarative_base()

# Определяем тип для идентификатора модели
ModelType = TypeVar("ModelType", bound=Base)
# Определяем тип для схемы создания
CreateSchemaType = TypeVar("CreateSchemaType")
# Определяем тип для схемы обновления
UpdateSchemaType = TypeVar("UpdateSchemaType")


class BaseModel(Base):
    """
    Абстрактный базовый класс с общими полями для всех моделей.
    """
    __abstract__ = True
    
    # Стандартные поля для всех моделей
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    @classmethod
    def get_by_id(cls, db: Session, id: uuid.UUID) -> Optional["BaseModel"]:
        """
        Получить объект по ID.
        
        Args:
            db (Session): Сессия SQLAlchemy
            id (uuid.UUID): Идентификатор объекта
            
        Returns:
            Optional[BaseModel]: Найденный объект или None
        """
        return db.query(cls).filter(cls.id == id).first()


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    Базовый класс для CRUD операций.
    """
    
    def __init__(self, model: Type[ModelType]):
        """
        Инициализатор CRUD класса.
        
        Args:
            model (Type[ModelType]): Класс модели SQLAlchemy
        """
        self.model = model
    
    def get(self, db: Session, id: uuid.UUID) -> Optional[ModelType]:
        """
        Получить объект по ID.
        
        Args:
            db (Session): Сессия SQLAlchemy
            id (uuid.UUID): Идентификатор объекта
            
        Returns:
            Optional[ModelType]: Найденный объект или None
        """
        return db.query(self.model).filter(self.model.id == id).first()
    
    def get_multi(
        self, db: Session, *, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        """
        Получить несколько объектов с пагинацией.
        
        Args:
            db (Session): Сессия SQLAlchemy
            skip (int, optional): Количество объектов для пропуска. По умолчанию 0.
            limit (int, optional): Максимальное количество возвращаемых объектов. По умолчанию 100.
            
        Returns:
            List[ModelType]: Список объектов
        """
        return db.query(self.model).offset(skip).limit(limit).all()
    
    def create(self, db: Session, *, obj_in: CreateSchemaType) -> ModelType:
        """
        Создать новый объект.
        
        Args:
            db (Session): Сессия SQLAlchemy
            obj_in (CreateSchemaType): Схема создания объекта
            
        Returns:
            ModelType: Созданный объект
        """
        obj_in_data = obj_in if isinstance(obj_in, dict) else obj_in.dict(exclude_unset=True)
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj
    
    def update(
        self, db: Session, *, db_obj: ModelType, obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        Обновить существующий объект.
        
        Args:
            db (Session): Сессия SQLAlchemy
            db_obj (ModelType): Существующий объект для обновления
            obj_in (Union[UpdateSchemaType, Dict[str, Any]]): Данные для обновления
            
        Returns:
            ModelType: Обновленный объект
        """
        obj_data = db_obj.__dict__
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.dict(exclude_unset=True)
        
        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])
        
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj
    
    def remove(self, db: Session, *, id: uuid.UUID) -> Optional[ModelType]:
        """
        Удалить объект по ID.
        
        Args:
            db (Session): Сессия SQLAlchemy
            id (uuid.UUID): Идентификатор объекта
            
        Returns:
            Optional[ModelType]: Удаленный объект или None, если объект не найден
        """
        obj = db.query(self.model).filter(self.model.id == id).first()
        if obj:
            db.delete(obj)
            db.commit()
        return obj
