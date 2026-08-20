"""
Audio utilities for WellcomeAI application.
"""

import base64
import struct
import numpy as np
from typing import Union, Optional
import io

from backend.core.logging import get_logger

logger = get_logger(__name__)

def audio_buffer_to_base64(buffer: Union[bytes, bytearray, memoryview]) -> str:
    """
    Convert audio buffer to base64 string
    
    Args:
        buffer: Audio buffer as bytes, bytearray, or memoryview
        
    Returns:
        Base64 encoded string
    """
    try:
        if isinstance(buffer, memoryview):
            buffer = buffer.tobytes()
            
        return base64.b64encode(buffer).decode('utf-8')
    except Exception as e:
        logger.error(f"Error converting audio buffer to base64: {str(e)}")
        raise

def base64_to_audio_buffer(base64_str: str) -> bytes:
    """
    Convert base64 string to audio buffer
    
    Args:
        base64_str: Base64 encoded string
        
    Returns:
        Audio buffer as bytes
    """
    try:
        return base64.b64decode(base64_str)
    except Exception as e:
        logger.error(f"Error converting base64 to audio buffer: {str(e)}")
        raise

def create_wav_from_pcm(
    pcm_data: bytes, 
    sample_rate: int = 24000, 
    sample_width: int = 2, 
    channels: int = 1
) -> bytes:
    """
    Create a WAV file from PCM audio data
    
    Args:
        pcm_data: PCM audio data
        sample_rate: Sample rate in Hz (default: 24000)
        sample_width: Sample width in bytes (default: 2 for 16-bit)
        channels: Number of channels (default: 1 for mono)
        
    Returns:
        WAV file as bytes
    """
    try:
        # Calculate sizes
        data_size = len(pcm_data)
        file_size = 36 + data_size
        
        # Create header
        header = bytearray()
        
        # RIFF chunk descriptor
        header.extend(b'RIFF')
        header.extend(struct.pack('<I', file_size))
        header.extend(b'WAVE')
        
        # fmt sub-chunk
        header.extend(b'fmt ')
        header.extend(struct.pack('<I', 16))  # Subchunk1Size (16 for PCM)
        header.extend(struct.pack('<H', 1))   # AudioFormat (1 for PCM)
        header.extend(struct.pack('<H', channels))  # NumChannels
        header.extend(struct.pack('<I', sample_rate))  # SampleRate
        header.extend(struct.pack('<I', sample_rate * channels * sample_width))  # ByteRate
        header.extend(struct.pack('<H', channels * sample_width))  # BlockAlign
        header.extend(struct.pack('<H', sample_width * 8))  # BitsPerSample
        
        # data sub-chunk
        header.extend(b'data')
        header.extend(struct.pack('<I', data_size))
        
        # Combine header and PCM data
        wav_data = header + pcm_data
        
        return wav_data
    except Exception as e:
        logger.error(f"Error creating WAV from PCM: {str(e)}")
        raise

def float32_to_int16(
    float32_array: np.ndarray
) -> np.ndarray:
    """
    Convert float32 numpy array to int16 numpy array
    
    Args:
        float32_array: Float32 numpy array in range [-1.0, 1.0]
        
    Returns:
        Int16 numpy array
    """
    try:
        # Ensure input is float32 array
        float32_array = np.asarray(float32_array, dtype=np.float32)
        
        # Clip to [-1.0, 1.0] range to avoid overflow
        float32_array = np.clip(float32_array, -1.0, 1.0)
        
        # Scale to int16 range and convert
        return (float32_array * 32767.0).astype(np.int16)
    except Exception as e:
        logger.error(f"Error converting float32 to int16: {str(e)}")
        raise

def int16_to_float32(
    int16_array: np.ndarray
) -> np.ndarray:
    """
    Convert int16 numpy array to float32 numpy array
    
    Args:
        int16_array: Int16 numpy array
        
    Returns:
        Float32 numpy array in range [-1.0, 1.0]
    """
    try:
        # Ensure input is int16 array
        int16_array = np.asarray(int16_array, dtype=np.int16)
        
        # Scale to [-1.0, 1.0] range
        return int16_array.astype(np.float32) / 32767.0
    except Exception as e:
        logger.error(f"Error converting int16 to float32: {str(e)}")
        raise

def resample_audio(
    audio_data: np.ndarray,
    original_sample_rate: int,
    target_sample_rate: int,
    channels: int = 1
) -> np.ndarray:
    """
    Resample audio to a different sample rate
    
    Args:
        audio_data: Audio data as numpy array
        original_sample_rate: Original sample rate in Hz
        target_sample_rate: Target sample rate in Hz
        channels: Number of channels (default: 1)
        
    Returns:
        Resampled audio data
    
    Note:
        Requires scipy to be installed
    """
    try:
        from scipy import signal
        
        # Calculate resampling factor
        resampling_factor = target_sample_rate / original_sample_rate
        
        # Calculate new length
        new_length = int(len(audio_data) * resampling_factor)
        
        # Resample
        resampled_audio = signal.resample(audio_data, new_length)
        
        return resampled_audio
    except ImportError:
        logger.warning("scipy is not installed. Audio resampling is not available.")
        raise ImportError("scipy is required for audio resampling")
    except Exception as e:
        logger.error(f"Error resampling audio: {str(e)}")
        raise

def detect_silence(
    audio_data: np.ndarray,
    threshold: float = 0.01,
    min_silence_duration: int = 500,
    sample_rate: int = 24000
) -> list:
    """
    Detect silence in audio data
    
    Args:
        audio_data: Audio data as numpy array (float32 in range [-1.0, 1.0])
        threshold: Silence threshold (default: 0.01)
        min_silence_duration: Minimum silence duration in milliseconds (default: 500)
        sample_rate: Sample rate in Hz (default: 24000)
        
    Returns:
        List of silence intervals as (start_ms, end_ms) tuples
    """
    try:
        # Calculate sample threshold for min_silence_duration
        min_samples = int(min_silence_duration * sample_rate / 1000)
        
        # Calculate absolute amplitude
        amplitude = np.abs(audio_data)
        
        # Find where amplitude is below threshold
        is_silence = amplitude < threshold
        
        # Find silence intervals
        silence_intervals = []
        silence_start = None
        
        for i, silent in enumerate(is_silence):
            if silent and silence_start is None:
                silence_start = i
            elif not silent and silence_start is not None:
                if i - silence_start >= min_samples:
                    # Convert to milliseconds
                    start_ms = int(silence_start * 1000 / sample_rate)
                    end_ms = int(i * 1000 / sample_rate)
                    silence_intervals.append((start_ms, end_ms))
                silence_start = None
        
        # Check if audio ends with silence
        if silence_start is not None and len(audio_data) - silence_start >= min_samples:
            start_ms = int(silence_start * 1000 / sample_rate)
            end_ms = int(len(audio_data) * 1000 / sample_rate)
            silence_intervals.append((start_ms, end_ms))
        
        return silence_intervals
    except Exception as e:
        logger.error(f"Error detecting silence: {str(e)}")
        raise
