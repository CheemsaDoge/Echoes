"""Gemini module alias re-exporting chat reasoning engine."""

from .chat import (
    AVAILABLE_TOOLS,
    TOOL_SEARCH_MEMORY,
    TOOL_SEARCH_SECTIONS,
    assemble_agent_system_instruction,
    execute_tool_call,
    SyntheticGeminiAgent,
    GeminiChatAgent,
)

__all__ = [
    "AVAILABLE_TOOLS",
    "TOOL_SEARCH_MEMORY",
    "TOOL_SEARCH_SECTIONS",
    "assemble_agent_system_instruction",
    "execute_tool_call",
    "SyntheticGeminiAgent",
    "GeminiChatAgent",
]
