"""Tests for the markdown stream state machine."""

import pytest
from gateway.markdown_state import MarkdownStreamState, MarkdownState


class TestMarkdownScan:
    """Tests for MarkdownStreamState.scan()."""

    def test_empty_text(self):
        state = MarkdownStreamState.scan("")
        assert not state.in_code_block
        assert not state.in_inline_code
        assert state.fence_marker == ""

    def test_plain_text(self):
        state = MarkdownStreamState.scan("Hello, world!")
        assert not state.in_code_block
        assert not state.in_inline_code

    def test_closed_code_block(self):
        text = "```python\nprint('hi')\n```"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block
        assert not state.in_inline_code

    def test_open_code_block_backtick(self):
        text = "```python\nprint('hi')\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "`"
        assert state.fence_length == 3
        assert not state.in_inline_code

    def test_open_code_block_tilde(self):
        text = "~~~python\nprint('hi')\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~"
        assert state.fence_length == 3

    def test_closed_inline_code(self):
        text = "Use `print()` to output."
        state = MarkdownStreamState.scan(text)
        assert not state.in_inline_code
        assert not state.in_code_block

    def test_open_inline_code(self):
        text = "Use `print() to output."
        state = MarkdownStreamState.scan(text)
        assert state.in_inline_code
        assert not state.in_code_block

    def test_backticks_inside_code_block_are_literal(self):
        text = "```\nHere is a `backtick` inside code\nstill in block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "`"
        assert not state.in_inline_code

    def test_multiple_code_blocks(self):
        text = "```py\ncode1\n```\nText between\n```\ncode2\n```"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block

    def test_multiple_code_blocks_last_open(self):
        text = "```py\ncode1\n```\nText between\n```\ncode2\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "`"

    def test_inline_code_then_code_block(self):
        text = "Use `func()` then:\n```python\nx = 1\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert not state.in_inline_code

    def test_code_block_then_inline_code(self):
        text = "```\ncode\n```\nThen use `inline"
        state = MarkdownStreamState.scan(text)
        assert state.in_inline_code
        assert not state.in_code_block

    def test_fence_with_language_hint(self):
        text = "```javascript\nconst x = 1;\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "`"
        assert state.fence_length == 3

    def test_double_backtick_inline_code(self):
        """Double backtick inline code like `` `code` ``."""
        text = "Use `` `code` `` for inline."
        # With char-by-char: first ` enters inline, second ` closes,
        # space, ` enters, code, ` closes, space, `` enters then closes.
        state = MarkdownStreamState.scan(text)
        assert not state.in_inline_code

    def test_double_backtick_inline_open(self):
        """Double backtick inline code, unclosed."""
        text = "Use `` `code` `` for ``inline"
        # `` = in→out, then ` enters: in, code, ` closes, space, ``
        # = in→out. So inline code is not open.
        state = MarkdownStreamState.scan(text)
        assert not state.in_inline_code

    def test_tilde_fence_inside_backtick_block(self):
        """~~~ inside a ``` block should be literal (not a closing fence)."""
        text = "```\n~~~\nstill in backtick block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "`"

    def test_backtick_fence_inside_tilde_block(self):
        """``` inside a ~~~ block should be literal (not a closing fence)."""
        text = "~~~\n```\nstill in tilde block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~"

    def test_nested_backticks_in_code_block(self):
        """Multiple backtick patterns inside code block are all literal."""
        text = "```\n```python\nx = 1\n```\n"
        # First ``` opens. Then ```python on line 2 — this is at line start
        # so it would be a closing fence check: same char (``), length 3 >= 3,
        # but rest is "python" which is non-empty, so NOT a closing fence.
        # So we stay in the code block. Then x = 1, then ``` at line start
        # with no rest — this IS a closing fence. So we exit.
        # Then trailing \n — we're in normal text.
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block

    def test_plain_text_with_backtick_at_end(self):
        text = "This ends with `"
        state = MarkdownStreamState.scan(text)
        assert state.in_inline_code

    def test_code_fence_at_end_of_text(self):
        text = "Some text\n```"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block

    def test_only_triple_backticks(self):
        text = "```"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block

    def test_text_after_closing_fence(self):
        text = "```\ncode\n```\nDone!"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block
        assert not state.in_inline_code

    def test_tilde_fence_closed(self):
        text = "~~~\ncode\n~~~"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block

    def test_tilde_fence_open(self):
        text = "~~~\ncode\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~"

    def test_mixed_fences_alternating(self):
        text = "```py\na\n```\n~~~\nb\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~"

    def test_suggestion_marker_not_interfered(self):
        """SUGGESTION: markers should pass through as plain text."""
        text = "Here is the answer.\n\nSUGGESTION:{\"next\": \"test\", \"reason\": \"x\", \"can_do\": false}"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block
        assert not state.in_inline_code

    # ── CommonMark fence matching tests (from Kimi review) ───────────

    def test_four_backtick_fence(self):
        """4+ backtick fences per CommonMark."""
        text = "````\ncode\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "`"
        assert state.fence_length == 4

    def test_four_backtick_fence_closed_by_four(self):
        """A 4-backtick opener requires 4+ to close."""
        text = "````\ncode\n````"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block

    def test_four_backtick_fence_not_closed_by_three(self):
        """3 backticks inside a 4-backtick block are literal."""
        text = "````\n```\nstill in block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_length == 4

    def test_four_tilde_fence(self):
        """4+ tilde fences per CommonMark."""
        text = "~~~~\ncode\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~"
        assert state.fence_length == 4

    def test_indented_fence_three_spaces(self):
        """Up to 3 spaces of indentation before a fence is allowed."""
        text = "   ```\ncode\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block

    def test_inline_triple_backticks_dont_open_code_block(self):
        """``` not at line start should not open a code block."""
        text = "Some text ``` more text\n"
        state = MarkdownStreamState.scan(text)
        # The ``` is on a line but not at the start (after "Some text "),
        # so it should not open a code block. It may trigger inline code
        # (first ` opens, second ` closes, third ` opens).
        assert not state.in_code_block
        # The third backtick opens inline code
        assert state.in_inline_code

    def test_closing_fence_with_content_after_not_closing(self):
        """A line starting with ``` but having content after is an opener,
        not a closer (it's an info string / language hint on opening, and
        non-empty rest on a closing attempt means it's not a closer)."""
        text = "```\ncode\n```python\nmore code\n"
        state = MarkdownStreamState.scan(text)
        # First ``` opens. Then ```python — same char, length 3 >= 3,
        # but rest is "python" which is non-empty → NOT a closing fence.
        # So we stay in the code block.
        assert state.in_code_block

    def test_closing_fence_longer_than_opener(self):
        """A closing fence can be longer than the opener per CommonMark."""
        text = "```\ncode\n````"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block

    def test_backtick_in_code_block_on_same_line_not_closing(self):
        """Backticks mid-line inside a code block should not close it."""
        text = "```\nUse `x` here\nstill in block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block


class TestCloseOpenConstructs:
    """Tests for MarkdownStreamState.close_open_constructs()."""

    def test_plain_text_unchanged(self):
        text = "Hello, world!"
        assert MarkdownStreamState.close_open_constructs(text) == text

    def test_closed_code_block_unchanged(self):
        text = "```python\nprint('hi')\n```"
        assert MarkdownStreamState.close_open_constructs(text) == text

    def test_open_code_block_gets_closing_fence(self):
        text = "```python\nprint('hi')\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "```"
        # The closing fence should make the full text balanced
        state = MarkdownStreamState.scan(result)
        assert not state.in_code_block

    def test_open_four_backtick_block_gets_four_backtick_close(self):
        text = "````\ncode\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "````"
        state = MarkdownStreamState.scan(result)
        assert not state.in_code_block

    def test_open_tilde_code_block_gets_closing_fence(self):
        text = "~~~python\nprint('hi')\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "~~~"
        state = MarkdownStreamState.scan(result)
        assert not state.in_code_block

    def test_open_inline_code_gets_closing_backtick(self):
        text = "Use `print() to output."
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "`"
        state = MarkdownStreamState.scan(result)
        assert not state.in_inline_code

    def test_empty_text_unchanged(self):
        assert MarkdownStreamState.close_open_constructs("") == ""

    def test_multiple_code_blocks_last_open(self):
        text = "```py\ncode1\n```\nText\n```\ncode2\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "```"

    def test_streaming_progression(self):
        """Simulate text arriving in chunks during streaming."""
        # Chunk 1: opening fence arrives
        chunk1 = "```python\n"
        r1 = MarkdownStreamState.close_open_constructs(chunk1)
        assert r1 == "```python\n```"
        assert r1.endswith("```")

        # Chunk 2: more code arrives, fence still open
        chunk2 = "```python\nimport os\nimport sys\n"
        r2 = MarkdownStreamState.close_open_constructs(chunk2)
        assert r2 == chunk2 + "```"

        # Chunk 3: closing fence arrives
        chunk3 = "```python\nimport os\nimport sys\nprint('hello')\n```"
        r3 = MarkdownStreamState.close_open_constructs(chunk3)
        assert r3 == chunk3  # unchanged

    def test_backtick_in_code_block_preserved(self):
        """A single backtick inside a code block should not trigger inline code."""
        text = "```\nUse the `backtick` character\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "```"
        # Verify the backtick inside the code block wasn't treated as inline code
        state = MarkdownStreamState.scan(result)
        assert not state.in_inline_code

    def test_idempotent_when_already_closed(self):
        # ``` at line start opens a fence; ``` mid-line is inline code.
        # This text has a code block that opens on line 1 and closes on
        # line 3, so it's balanced.
        text = "```\nblock\n```"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text

    def test_strips_trailing_whitespace_before_closing_fence(self):
        """Trailing whitespace should not create extra blank lines."""
        text = "```python\nprint('hi')\n  \n"
        result = MarkdownStreamState.close_open_constructs(text)
        # Should strip trailing whitespace, then add newline + fence
        assert result == "```python\nprint('hi')\n```"

    def test_inline_triple_backticks_not_treated_as_fence(self):
        """``` not at line start should be handled as inline code, not a fence."""
        text = "Here is some `code` and then ``` more"
        result = MarkdownStreamState.close_open_constructs(text)
        # The ``` is not at line start so it's not a fence.
        # The third ` opens inline code, so we need a closing backtick.
        state = MarkdownStreamState.scan(result)
        assert not state.in_code_block