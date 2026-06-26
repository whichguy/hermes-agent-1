"""Markdown stream state machine — tracks markdown constructs during streaming.

The stream consumer delivers partial messages to the platform as the LLM
generates text.  When a partial message contains an opening ```` ``` ````
fence but the closing fence hasn't arrived yet, the platform renders the
raw backticks as visible text.  This module provides a proper state machine
that scans accumulated text to determine whether we're inside a code block
or inline code at the end, so the consumer can append closing markers before
display.

Replaces the simpler ``_balance_code_fences()`` odd/even count with a
character-level scanner that handles:

- Triple-backtick fences (```` ``` ````) with optional language hint
- Tilde fences (``~~~``)
- Single-backtick inline code
- Backticks inside code blocks (treated as literal text, not inline code)
- Fences inside inline code (reasonable handling of edge case)

The scanner is O(n) per call where n is the accumulated text length.
For typical streaming messages (<4,000 chars, edited every ~1.5s) this
is negligible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarkdownState:
    """Snapshot of markdown parsing state at the end of a text buffer.

    Attributes:
        in_code_block: True if the text ends inside a fenced code block.
        fence_marker: The fence string that opened the code block
            (```` ``` ```` or ``~~~``); empty when not in a code block.
        in_inline_code: True if the text ends inside backtick-delimited
            inline code.
    """
    in_code_block: bool = False
    fence_marker: str = ""
    in_inline_code: bool = False


class MarkdownStreamState:
    """State machine for tracking markdown constructs in streaming text.

    Usage::

        state = MarkdownStreamState.scan(accumulated_text)
        if state.in_code_block:
            display = accumulated_text + "\\n" + state.fence_marker
        elif state.in_inline_code:
            display = accumulated_text + "`"
        else:
            display = accumulated_text

    Or use the convenience method::

        display = MarkdownStreamState.close_open_constructs(text)
    """

    @staticmethod
    def scan(text: str) -> MarkdownState:
        """Scan *text* and return the markdown state at the end.

        Processes the text character-by-character, tracking transitions
        between normal text, fenced code blocks, and inline code.

        Handles:
            - ```` ``` ```` and ``~~~`` fenced code blocks
            - Single-backtick inline code (`` `code` ``)
            - Backticks inside code blocks (literal, not state transitions)
            - Fence language hints (```` ```python ````)
        """
        state = MarkdownState()
        i = 0
        n = len(text)

        while i < n:
            if state.in_code_block:
                # Inside a fenced code block — only look for the matching
                # closing fence.  Everything else (including backticks) is
                # literal text inside the code block.
                if text[i:i + 3] == state.fence_marker:
                    state.in_code_block = False
                    state.fence_marker = ""
                    i += 3
                else:
                    i += 1
            elif state.in_inline_code:
                # Inside inline code — look for the closing backtick.
                if text[i] == "`":
                    state.in_inline_code = False
                    i += 1
                else:
                    i += 1
            else:
                # Normal text — check for fence openings and inline code.
                # Check triple-backtick fence first (before single backtick)
                # so that ``` is recognized as a fence, not inline code.
                if text[i:i + 3] == "```":
                    state.in_code_block = True
                    state.fence_marker = "```"
                    i += 3
                elif text[i:i + 3] == "~~~":
                    state.in_code_block = True
                    state.fence_marker = "~~~"
                    i += 3
                elif text[i] == "`":
                    state.in_inline_code = True
                    i += 1
                else:
                    i += 1

        return state

    @staticmethod
    def close_open_constructs(text: str) -> str:
        """Return *text* with closing markers appended for open constructs.

        If the text ends inside a code block, appends a closing fence.
        If it ends inside inline code, appends a closing backtick.
        Otherwise returns *text* unchanged.
        """
        state = MarkdownStreamState.scan(text)
        if state.in_code_block:
            return text + "\n" + state.fence_marker
        if state.in_inline_code:
            return text + "`"
        return text