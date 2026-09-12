"""Versioned prompt constants.

Prompts live here as named constants rather than inline f-strings so they can be
diffed, reviewed, and versioned like the rest of the contract (CLAUDE.md, Claude
API Integration).
"""

from app.services.prompts.content_generation import (
    CONTENT_SYSTEM_PROMPT,
    CONTENT_SYSTEM_PROMPT_V1,
)
from app.services.prompts.templates import TEMPLATES, template_for

__all__ = [
    "CONTENT_SYSTEM_PROMPT",
    "CONTENT_SYSTEM_PROMPT_V1",
    "TEMPLATES",
    "template_for",
]
