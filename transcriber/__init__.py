"""
Transcription backends for B.L.A.D.E. (Brister's Linguistic Audio Dictation Engine).
"""
from .base import TranscriptionBackend
from .local_backend import LocalWhisperBackend

__all__ = ['TranscriptionBackend', 'LocalWhisperBackend'] 