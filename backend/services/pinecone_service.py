# services/pinecone_service.py
import re
import os
import uuid
import json
from typing import List, Dict, Optional, Tuple
import asyncio

import openai
import pinecone
from fastapi import HTTPException, status

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.models.pinecone_config import PineconeConfig

logger = get_logger(__name__)

# Индекс Pinecone: dense, dimension 1536 (text-embedding-3-small), metric cosine.
# Хост индекса SDK узнаёт сам по имени — адрес индекса в коде не хранить.
PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX", "voicufi")

class PineconeService:
    @staticmethod
    async def initialize():
        """Initialize Pinecone connection"""
        api_key = os.environ.get("PINECONE_API_KEY")
        environment = os.environ.get("PINECONE_ENVIRONMENT", "gcp-starter")
        
        if not api_key:
            logger.error("PINECONE_API_KEY environment variable not set")
            raise ValueError("Pinecone API key not configured")
            
        # Обновленный синтаксис инициализации Pinecone
        pc = pinecone.Pinecone(
            api_key=api_key,
            environment=environment
        )
        logger.info("Pinecone initialized successfully")
        return pc
    
    @staticmethod
    async def create_embeddings(text: str, api_key: str, model: str = "text-embedding-3-small") -> List[float]:
        """Create embeddings using OpenAI API"""
        client = openai.OpenAI(api_key=api_key)
        try:
            response = client.embeddings.create(
                model=model, 
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error creating embeddings: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create embeddings: {str(e)}"
            )
    
    @staticmethod
    async def create_or_update_knowledge_base(
        content: str, 
        api_key: str, 
        namespace: Optional[str] = None
    ) -> Tuple[str, int]:
        """Create a new namespace in Pinecone with embeddings from content or update existing"""
        try:
            # Initialize Pinecone
            pc = await PineconeService.initialize()
            
            # Check content limits
            if len(content) > 500000:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Content exceeds maximum limit of 500,000 characters"
                )
            
            # Generate a namespace if not provided
            if not namespace:
                namespace = f"kb-{uuid.uuid4().hex[:10]}"
                
            # Process content into chunks
            chunks = PineconeService._process_text_into_chunks(content)
            logger.info(f"Processed content into {len(chunks)} chunks")
            
            # Get index
            index_name = PINECONE_INDEX_NAME
            try:
                index = pc.Index(index_name)
            except Exception as e:
                logger.error(f"Error accessing Pinecone index: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to access Pinecone index"
                )
            
            # If namespace exists, delete old vectors
            try:
                stats = index.describe_index_stats()
                namespaces = stats.get("namespaces", {})
                if namespace in namespaces:
                    logger.info(f"Deleting existing namespace: {namespace}")
                    index.delete(namespace=namespace, delete_all=True)
            except Exception as e:
                logger.warning(f"Error checking/deleting namespace: {str(e)}")
            
            # Create batches for parallel processing
            batch_size = 10  # Adjust based on your rate limits
            batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
            
            # Process each batch
            for batch_idx, batch in enumerate(batches):
                logger.info(f"Processing batch {batch_idx+1}/{len(batches)}")
                vectors = []
                
                # Create embeddings for each chunk in batch
                for i, chunk in enumerate(batch):
                    chunk_id = batch_idx * batch_size + i
                    try:
                        embedding = await PineconeService.create_embeddings(chunk, api_key)
                        vectors.append({
                            'id': f"{namespace}-{chunk_id}",
                            'values': embedding,
                            'metadata': {
                                'text': chunk,
                                'chunk_id': chunk_id
                            }
                        })
                    except Exception as e:
                        logger.error(f"Error creating embedding for chunk {chunk_id}: {str(e)}")
                
                # Upsert vectors into the index
                if vectors:
                    try:
                        index.upsert(vectors=vectors, namespace=namespace)
                        logger.info(f"Upserted {len(vectors)} vectors in batch {batch_idx+1}")
                    except Exception as e:
                        logger.error(f"Error upserting vectors: {str(e)}")
                
                # Add delay to avoid rate limits
                await asyncio.sleep(1)
            
            logger.info(f"Knowledge base created/updated successfully with namespace: {namespace}")
            return namespace, len(content)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating/updating knowledge base: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create/update knowledge base: {str(e)}"
            )
    
    @staticmethod
    def _process_text_into_chunks(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
        """Process text into overlapping chunks for better vector representation"""
        chunks = []
        
        # Split by sentences or paragraphs
        paragraphs = text.split('\n\n')
        current_chunk = ""
        
        for paragraph in paragraphs:
            if len(current_chunk) + len(paragraph) > chunk_size:
                chunks.append(current_chunk.strip())
                # Keep some overlap for context continuity
                current_chunk = current_chunk[-overlap:] if overlap > 0 else ""
            
            current_chunk += paragraph + "\n\n"
        
        # Add the last chunk if not empty
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        return chunks
    
    @staticmethod
    async def search(
        query: str,
        namespace: str,
        api_key: str,
        top_k: int = 3,
        model: str = "text-embedding-3-small",
    ) -> List[Dict]:
        """
        Семантический поиск в namespace индекса voicufi.

        Важно: модель эмбеддингов должна совпадать с той, что использовалась при
        создании базы (create_or_update_knowledge_base → text-embedding-3-small),
        иначе вектора окажутся в разных пространствах и поиск будет нерелевантным.

        Возвращает список совпадений вида {id, score, text, metadata}.
        """
        if not query or not namespace:
            return []

        pc = await PineconeService.initialize()
        index = pc.Index(PINECONE_INDEX_NAME)

        embedding = await PineconeService.create_embeddings(query, api_key, model=model)

        response = index.query(
            vector=embedding,
            namespace=namespace,
            top_k=top_k,
            include_metadata=True,
        )

        matches = []
        for match in (response.get("matches", []) if isinstance(response, dict) else response.matches):
            md = match.get("metadata", {}) if isinstance(match, dict) else (match.metadata or {})
            matches.append({
                "id": match.get("id") if isinstance(match, dict) else match.id,
                "score": match.get("score") if isinstance(match, dict) else match.score,
                "text": md.get("text", ""),
                "metadata": md,
            })
        return matches

    @staticmethod
    async def delete_knowledge_base(namespace: str) -> bool:
        """Delete a namespace from Pinecone"""
        try:
            # Initialize Pinecone
            pc = await PineconeService.initialize()
            
            # Get the index
            index_name = PINECONE_INDEX_NAME
            try:
                index = pc.Index(index_name)
            except Exception as e:
                logger.error(f"Error accessing Pinecone index: {str(e)}")
                return False
            
            # Delete all vectors in the namespace
            try:
                index.delete(namespace=namespace, delete_all=True)
                logger.info(f"Deleted namespace: {namespace}")
                return True
            except Exception as e:
                logger.error(f"Error deleting namespace: {str(e)}")
                return False
        except Exception as e:
            logger.error(f"Error deleting knowledge base: {str(e)}")
            return False


# ============================================================================
# ДОКУМЕНТЫ БАЗЫ ЗНАНИЙ v2 (ветка 1410-kb-v2)
# ============================================================================
#
# Отличие от create_or_update_knowledge_base: там любое изменение стирало
# namespace целиком и заливало всё заново — поменять один файл значило
# перезалить всю базу. Здесь вектора каждого документа живут под своими id
# (doc-<entry_id>-<i>), поэтому документ можно обновить или удалить, не
# трогая соседние.

def smart_chunks(text: str, chunk_size: int = 1100, overlap: int = 150) -> List[str]:
    """
    Нарезка, которая старается не рвать смысл: сначала по заголовкам и
    абзацам, и только слишком длинный абзац — по предложениям. Старая нарезка
    резала строго по 1000 символов и могла разорвать таблицу цен пополам,
    из-за чего агент видел половину прайса.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    # Куски между заголовками (markdown-подобными или строками в ВЕРХНЕМ регистре).
    blocks: List[str] = []
    current: List[str] = []
    for line in text.split("\n"):
        is_heading = line.startswith("#") or (
            len(line.strip()) > 3 and len(line.strip()) < 80 and line.strip().isupper()
        )
        if is_heading and current:
            blocks.append("\n".join(current).strip())
            current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())

    chunks: List[str] = []
    buf = ""
    for block in [b for b in blocks if b]:
        for para in [p.strip() for p in block.split("\n\n") if p.strip()]:
            if len(para) > chunk_size:
                # Длинный абзац — по предложениям.
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sentence in sentences:
                    if len(buf) + len(sentence) + 1 > chunk_size and buf:
                        chunks.append(buf.strip())
                        buf = buf[-overlap:] if overlap else ""
                    buf += sentence + " "
                continue
            if len(buf) + len(para) + 2 > chunk_size and buf:
                chunks.append(buf.strip())
                buf = buf[-overlap:] if overlap else ""
            buf += para + "\n\n"
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if c]


async def upsert_document(namespace: str, entry_id: str, text: str, api_key: str,
                          title: str = "", old_chunk_count: int = 0) -> int:
    """
    Залить один документ в namespace агента. Возвращает число кусков.
    Старые куски этого же документа удаляются — повторная загрузка не плодит
    дубликаты в выдаче.
    """
    chunks = smart_chunks(text)
    if not chunks:
        return 0

    pc = await PineconeService.initialize()
    index = pc.Index(PINECONE_INDEX_NAME)

    if old_chunk_count:
        try:
            index.delete(ids=[f"doc-{entry_id}-{i}" for i in range(old_chunk_count)],
                         namespace=namespace)
        except Exception as e:
            logger.warning(f"[KB] старые куски документа {entry_id} не удалены: {e}")

    vectors = []
    for i, chunk in enumerate(chunks):
        try:
            embedding = await PineconeService.create_embeddings(chunk, api_key)
        except Exception as e:
            logger.error(f"[KB] эмбеддинг куска {i} документа {entry_id}: {e}")
            continue
        vectors.append({
            "id": f"doc-{entry_id}-{i}",
            "values": embedding,
            "metadata": {"text": chunk, "entry_id": str(entry_id),
                         "title": title or "", "chunk_id": i},
        })
        if len(vectors) >= 50:
            index.upsert(vectors=vectors, namespace=namespace)
            vectors = []
    if vectors:
        index.upsert(vectors=vectors, namespace=namespace)
    logger.info(f"[KB] документ {entry_id} → {len(chunks)} кусков в {namespace}")
    return len(chunks)


async def delete_document(namespace: str, entry_id: str, chunk_count: int) -> bool:
    """Убрать из поиска один документ, не трогая остальные."""
    if not namespace or not chunk_count:
        return True
    try:
        pc = await PineconeService.initialize()
        index = pc.Index(PINECONE_INDEX_NAME)
        index.delete(ids=[f"doc-{entry_id}-{i}" for i in range(chunk_count)],
                     namespace=namespace)
        return True
    except Exception as e:
        logger.warning(f"[KB] документ {entry_id} не удалён из Pinecone: {e}")
        return False
