"""
ChatGPT Streaming Client.

Handles asynchronous streaming requests to OpenAI ChatGPT API.
Optimized for low-latency real-time responses.
"""

import openai
import time
from typing import List, Dict, AsyncIterator
from backend.core.logging import get_logger

logger = get_logger(__name__)


class ChatGPTStreamingClient:
    """
    Async streaming client for OpenAI ChatGPT.
    
    Features:
    - Real-time token streaming
    - Low latency optimization
    - Error handling and retries
    - Performance monitoring
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2000,
        temperature: float = 0.7
    ):
        """
        Initialize streaming client.
        
        Args:
            api_key: OpenAI API key
            model: Model name (gpt-4o-mini, gpt-4o, etc.)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0-1)
        """
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        logger.info(f"[STREAMING-CLIENT] Initialized with model: {model}")
    
    async def stream_response(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncIterator[str]:
        """
        Stream ChatGPT response in real-time.
        
        Args:
            messages: List of conversation messages
            
        Yields:
            str: Content chunks as they arrive
            
        Example:
            async for chunk in client.stream_response(messages):
                print(chunk, end='', flush=True)
        """
        
        start_time = time.time()
        first_chunk_time = None
        chunk_count = 0
        total_content = ""
        
        try:
            logger.info(f"[STREAMING-CLIENT] 📤 Sending request to OpenAI...")
            
            # Create streaming request
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True
            )
            
            # Stream chunks
            async for chunk in response:
                # Extract content
                delta = chunk.choices[0].delta
                
                if delta.content:
                    content = delta.content
                    chunk_count += 1
                    total_content += content
                    
                    # Measure first chunk latency
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        latency = first_chunk_time - start_time
                        logger.info(f"[STREAMING-CLIENT] ⚡ First chunk in {latency:.2f}s")
                    
                    yield content
            
            # Final stats
            total_time = time.time() - start_time
            chars_per_sec = len(total_content) / total_time if total_time > 0 else 0
            
            logger.info(
                f"[STREAMING-CLIENT] ✅ Complete: "
                f"{chunk_count} chunks, "
                f"{len(total_content)} chars, "
                f"{total_time:.2f}s, "
                f"{chars_per_sec:.0f} chars/sec"
            )
            
        except openai.APIError as e:
            logger.error(f"[STREAMING-CLIENT] ❌ OpenAI API Error: {str(e)}")
            raise
            
        except openai.APIConnectionError as e:
            logger.error(f"[STREAMING-CLIENT] ❌ Connection Error: {str(e)}")
            raise
            
        except Exception as e:
            logger.error(f"[STREAMING-CLIENT] ❌ Unexpected Error: {str(e)}")
            raise
    
    async def count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """
        Estimate token count for messages.
        
        Args:
            messages: List of messages
            
        Returns:
            Estimated token count
            
        Note:
            Rough estimate: 1 token ≈ 4 characters
        """
        total_chars = sum(len(msg.get("content", "")) for msg in messages)
        estimated_tokens = total_chars // 4
        
        return estimated_tokens
    
    def validate_messages(self, messages: List[Dict[str, str]]) -> bool:
        """
        Validate message format.
        
        Args:
            messages: List of messages to validate
            
        Returns:
            True if valid, raises exception otherwise
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")
        
        for msg in messages:
            if "role" not in msg or "content" not in msg:
                raise ValueError("Each message must have 'role' and 'content'")
            
            if msg["role"] not in ["system", "user", "assistant"]:
                raise ValueError(f"Invalid role: {msg['role']}")
        
        return True
