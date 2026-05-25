"""Re-export for ``from app.executors.base import Executor, ActionResult``.

Keeps the ABC out of ``__init__.py`` so we can put package-wide helpers there
later without circular imports.
"""
from __future__ import annotations

# Re-export from __init__ so existing imports keep working but base.py is the
# canonical home for the protocol.
from app.executors import ActionResult, Executor  # noqa: F401
