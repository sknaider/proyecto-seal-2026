"""SEAL terminal UI — minimal native TUI built on Python's curses module.

No third-party TUI library. Provides a chat-style transcript pane plus an
input pane, with hooks for the runtime to push agent responses asynchronously.
"""

from .app import TUIApp, TUIMessage, TUIInputCallback

__all__ = ["TUIApp", "TUIMessage", "TUIInputCallback"]
