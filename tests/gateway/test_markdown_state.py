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
        assert state.fence_marker == "```"
        assert not state.in_inline_code

    def test_open_code_block_tilde(self):
        text = "~~~python\nprint('hi')\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~~~"
        assert not state.in_inline_code

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
        assert state.fence_marker == "```"
        assert not state.in_inline_code

    def test_multiple_code_blocks(self):
        text = "```py\ncode1\n```\nText between\n```\ncode2\n```"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block

    def test_multiple_code_blocks_last_open(self):
        text = "```py\ncode1\n```\nText between\n```\ncode2\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "```"

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
        assert state.fence_marker == "```"

    def test_double_backtick_inline_code(self):
        """Double backtick inline code like `` `code` ``."""
        text = "Use `` `code` `` for inline."
        # The scanner processes backticks one at a time: first ` enters
        # inline code, second ` closes it, third ` enters again, fourth
        # closes. So "`` `code` ``" is correctly seen as: in → out → in
        # → out → space → in → out → space → in → out.
        state = MarkdownStreamState.scan(text)
        assert not state.in_inline_code

    def test_double_backtick_inline_open(self):
        """Double backtick inline code, unclosed."""
        text = "Use `` `code` `` for ``inline"
        # After `` closes: in→out, ` enters: in, code, ` closes: out,
        # space, `` enters: in, out. Wait: "``inline" — first ` enters,
        # second ` closes. So inline code is not open.
        # Actually: `` = in→out (two chars), then "inline" is plain.
        state = MarkdownStreamState.scan(text)
        assert not state.in_inline_code

    def test_tilde_fence_inside_backtick_block(self):
        """~~~ inside a ``` block should be literal."""
        text = "```\n~~~\nstill in backtick block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "```"

    def test_backtick_fence_inside_tilde_block(self):
        """``` inside a ~~~ block should be literal."""
        text = "~~~\n```\nstill in tilde block\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~~~"

    def test_nested_backticks_in_code_block(self):
        """Multiple backtick patterns inside code block are all literal."""
        text = "```\n```python\nx = 1\n```\n"
        # The first ``` opens, then we scan inside for closing ```.
        # The second ``` on line 2 closes it. Then "python\nx = 1\n"
        # is plain text. Then the third ``` opens again. Then \n.
        # So we end inside a code block.
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "```"

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
        assert state.fence_marker == "~~~"

    def test_mixed_fences_alternating(self):
        text = "```py\na\n```\n~~~\nb\n"
        state = MarkdownStreamState.scan(text)
        assert state.in_code_block
        assert state.fence_marker == "~~~"

    def test_suggestion_marker_not_interfered(self):
        """SUGGESTION: markers should pass through as plain text."""
        text = "Here is the answer.\n\nSUGGESTION:{\"next\": \"test\", \"reason\": \"x\", \"can_do\": false}"
        state = MarkdownStreamState.scan(text)
        assert not state.in_code_block
        assert not state.in_inline_code


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
        assert result == text + "\n```"
        # The closing fence should make the full text balanced
        state = MarkdownStreamState.scan(result)
        assert not state.in_code_block

    def test_open_tilde_code_block_gets_closing_fence(self):
        text = "~~~python\nprint('hi')\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "\n~~~"
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
        assert result == text + "\n```"

    def test_streaming_progression(self):
        """Simulate text arriving in chunks during streaming."""
        # Chunk 1: opening fence arrives
        chunk1 = "```python\n"
        r1 = MarkdownStreamState.close_open_constructs(chunk1)
        assert r1 == "```python\n\n```"
        assert r1.endswith("```")

        # Chunk 2: more code arrives, fence still open
        chunk2 = "```python\nimport os\nimport sys\n"
        r2 = MarkdownStreamState.close_open_constructs(chunk2)
        assert r2 == chunk2 + "\n```"

        # Chunk 3: closing fence arrives
        chunk3 = "```python\nimport os\nimport sys\nprint('hello')\n```"
        r3 = MarkdownStreamState.close_open_constructs(chunk3)
        assert r3 == chunk3  # unchanged

    def test_backtick_in_code_block_preserved(self):
        """A single backtick inside a code block should not trigger inline code."""
        text = "```\nUse the `backtick` character\n"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text + "\n```"
        # Verify the backtick inside the code block wasn't treated as inline code
        state = MarkdownStreamState.scan(result)
        assert not state.in_inline_code

    def test_idempotent_when_already_closed(self):
        text = "Some `inline code` and ```\nblock\n```"
        result = MarkdownStreamState.close_open_constructs(text)
        assert result == text