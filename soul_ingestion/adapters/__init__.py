"""Acquisition adapters. Importing adapters never performs acquisition."""

from .chat import ChatAdapter, ChatMessage
from .email import EmailAdapter
from .pdf import PdfAdapter
from .text import TextAdapter
from .youtube import YouTubeAdapter

__all__ = ["ChatAdapter", "ChatMessage", "EmailAdapter", "PdfAdapter", "TextAdapter", "YouTubeAdapter"]
