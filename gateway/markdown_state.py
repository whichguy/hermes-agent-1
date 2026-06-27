"""Markdown stream state machine — tracks markdown constructs during streaming.

The stream consumer delivers partial messages to the platform as the LLM
generates text.  When a partial message contains an opening ```` ``` ````
fence but the closing fence hasn't arrived yet, the platform renders the
raw backticks as visible text.  This module provides a proper state machine
that scans accumulated text to determine whether we're inside a code block
or inline code at the end, so the consumer can append closing markers before
display.

Replaces the simpler ``_balance_code_fences()`` odd/even count with a
line-oriented scanner that handles:

- Triple-backtick fences (```` ``` ````) with optional language hint
- Tilde fences (``~~~``)
- Fences with 4+ markers (```` ```` ````, ``~~~~``) per CommonMark
- Single-backtick inline code
- Backticks inside code blocks (treated as literal text, not inline code)
- Closing fences only recognized at the start of a line (per CommonMark)

The scanner is O(n) per call where n is the accumulated text length.
For typical streaming messages (<4,000 chars, edited every ~1.5s) this
is negligible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MarkdownState:
    """Snapshot of markdown parsing state at the end of a text buffer.

    Attributes:
        in_code_block: True if the text ends inside a fenced code block.
        fence_marker: The fence character that opened the code block
            (```` ` ```` or ``~``); empty when not in a code block.
        fence_length: The number of fence characters in the opener (>=3).
            A closing fence must have at least this many characters.
        in_inline_code: True if the text ends inside backtick-delimited
            inline code.
    """
    in_code_block: bool = False
    fence_marker: str = ""
    fence_length: int = 0
    in_inline_code: bool = False


# Regex to find fence lines: a line that starts with optional whitespace,
# then 3+ backticks or tildes, optionally followed by a language hint.
# We use this to detect fence openings and closings line-by-line.
_FENCE_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<fence>(`{3,}|~{3,}))(?P<rest>.*)$"
)


class MarkdownStreamState:
    """State machine for tracking markdown constructs in streaming text.

    Usage::

        state = MarkdownStreamState.scan(accumulated_text)
        if state.in_code_block:
            display = accumulated_text + "\\n" + state.fence_marker * state.fence_length
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

        Processes the text line-by-line for fenced code blocks (per
        CommonMark: a fence is 3+ backticks or tildes at the start of a
        line, optionally preceded by up to 3 spaces of indentation).
        Within a code block, only a closing fence (same character, >= same
        length, at line start) exits the block.

        For inline code, processes character-by-character to track
        single-backtick `` `code` `` spans.  Inline code scanning is
        paused inside code blocks.
        """
        state = MarkdownState()
        lines = text.split("\n")

        for line in lines:
            if state.in_code_block:
                # Inside a fenced code block — only a matching closing
                # fence at the start of a line (with optional indentation)
                # can close it.  Everything else is literal.
                m = _FENCE_LINE_RE.match(line)
                if m:
                    fence_chars = m.group("fence")
                    fence_char = fence_chars[0]  # ` or ~
                    fence_len = len(fence_chars)
                    rest = m.group("rest").strip()
                    # Closing fence: same character, length >= opener,
                    # and nothing meaningful after it (just whitespace).
                    if (
                        fence_char == state.fence_marker
                        and fence_len >= state.fence_length
                        and not rest
                    ):
                        state.in_code_block = False
                        state.fence_marker = ""
                        state.fence_length = 0
                        continue
                # Not a closing fence — line is literal code block content
                continue

            # Not in a code block — check for fence opening.
            # A fence at the start of a line is a block-level construct and
            # takes precedence over any open inline code state (which is
            # inline and can't span across a fence boundary).
            m = _FENCE_LINE_RE.match(line)
            if m:
                fence_chars = m.group("fence")
                fence_char = fence_chars[0]
                fence_len = len(fence_chars)
                # Opening fence: 3+ backticks or tildes at line start.
                # The rest of the line is the optional language hint.
                # Close any open inline code first (block > inline).
                state.in_inline_code = False
                state.in_code_block = True
                state.fence_marker = fence_char
                state.fence_length = fence_len
                continue

            # No fence on this line — scan for inline code (single backticks).
            # We scan the line character-by-character for inline code spans.
            # Note: inline code state can persist across lines during
            # streaming (the model may not have closed it yet), but a
            # fence line (checked above) always resets it.
            for ch in line:
                if state.in_inline_code:
                    if ch == "`":
                        state.in_inline_code = False
                    # Other chars are literal inside inline code
                else:
                    if ch == "`":
                        state.in_inline_code = True
                    # Other chars are normal text

        return state

    @staticmethod
    def close_open_constructs(text: str) -> str:
        """Return *text* with closing markers appended for open constructs.

        If the text ends inside a code block, appends a closing fence
        (same character, same length as the opener).
        If it ends inside inline code, appends a closing backtick.
        Otherwise returns *text* unchanged.
        """
        state = MarkdownStreamState.scan(text)
        if state.in_code_block:
            # Append a closing fence matching the opener's character and length
            closing = state.fence_marker * state.fence_length
            # Strip trailing whitespace to avoid extra blank lines
            stripped = text.rstrip()
            return stripped + "\n" + closing
        if state.in_inline_code:
            return text + "`"
        return text