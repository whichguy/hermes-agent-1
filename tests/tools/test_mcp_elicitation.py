"""Tests for the MCP elicitation handler in tools.mcp_tool.

These tests exercise ElicitationHandler in isolation -- the underlying
approval system and the MCP transport layer are mocked, so no real MCP
server or user input is required.

Tests skip cleanly if the optional `mcp` SDK is not installed (it is an
optional dependency under the `[mcp]` extra).
"""

import asyncio
from unittest.mock import patch

import pytest


pytest.importorskip("mcp.types")

from mcp.types import ElicitResult  # noqa: E402  -- after importorskip

from tools.mcp_tool import (  # noqa: E402
    ElicitationHandler,
    _format_elicitation_schema_summary,
    _sanitize_elicitation_url,
)


def _form_params(message="please confirm", schema=None):
    """Build a stand-in for ElicitRequestFormParams.

    We use a plain object (not the SDK type directly) so the test doesn't
    couple to optional Pydantic validation -- the handler reads fields via
    getattr() and tolerates duck-typed inputs.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        mode="form",
        message=message,
        requested_schema=schema or {},
    )


def _url_params(message="open this url", url="https://example.com/auth", elicitationId="e1"):
    from types import SimpleNamespace
    return SimpleNamespace(
        mode="url",
        message=message,
        url=url,
        elicitationId=elicitationId,
    )


class TestSchemaSummary:
    def test_empty_schema_falls_back_to_generic_message(self):
        out = _format_elicitation_schema_summary({}, "pay")
        assert "pay" in out
        assert "Approval requested" in out

    def test_properties_render_with_type_and_description(self):
        schema = {
            "type": "object",
            "properties": {
                "amount": {"type": "string", "description": "USD amount"},
                "recipient": {"type": "string"},
            },
        }
        out = _format_elicitation_schema_summary(schema, "pay")
        assert "amount (string): USD amount" in out
        assert "recipient (string)" in out


class TestElicitationHandlerFormMode:
    def test_user_accepts_once_returns_accept(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params(
            "authorize a payment of $0.50",
            {"properties": {"approved": {"type": "boolean"}}},
        )

        with patch("tools.approval.request_elicitation_consent", return_value="accept"):
            result = asyncio.run(handler(context=None, params=params))

        assert isinstance(result, ElicitResult)
        assert result.action == "accept"
        assert result.content == {}
        assert handler.metrics["accepted"] == 1
        assert handler.metrics["declined"] == 0

    def test_user_denies_returns_decline(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params()

        with patch("tools.approval.request_elicitation_consent", return_value="decline"):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["declined"] == 1
        assert handler.metrics["accepted"] == 0

    def test_cancel_propagates_through(self):
        """request_elicitation_consent returns 'cancel' when the gateway
        wait times out (resolved=False). The handler should propagate
        that as ElicitResult(action='cancel') so the server can
        distinguish 'no answer' from 'no'."""
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params()

        with patch("tools.approval.request_elicitation_consent", return_value="cancel"):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "cancel"
        assert handler.metrics["errors"] == 1


class TestElicitationHandlerURLMode:
    """URL-mode elicitations surface the server-provided URL through the
    existing consent flow. The URL is sanitized (http/https, bounded length)
    before being rendered as a markdown link in the description slot, and
    the user's accept/decline/cancel choice is collected via the same
    request_elicitation_consent router form-mode uses."""

    def test_user_accepts_url_mode(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(
            "authorize a payment",
            url="https://payments.example.com/authorize",
        )

        with patch(
            "tools.approval.request_elicitation_consent",
            return_value="accept",
        ) as m:
            result = asyncio.run(handler(context=None, params=params))

        assert isinstance(result, ElicitResult)
        assert result.action == "accept"
        assert result.content is None
        assert handler.metrics["accepted"] == 1
        assert handler.metrics["declined"] == 0
        # The server message goes in the message slot.
        assert m.call_args.args[0] == "authorize a payment"
        # The URL is rendered as a markdown link in the description slot.
        assert "[Open URL](https://payments.example.com/authorize)" == m.call_args.args[1]

    def test_user_declines_url_mode(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params()

        with patch(
            "tools.approval.request_elicitation_consent",
            return_value="decline",
        ) as m:
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["declined"] == 1
        # The consent router must still be invoked -- URL-mode is now real.
        assert m.call_count == 1

    def test_cancel_propagates_through_url_mode(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params()

        with patch(
            "tools.approval.request_elicitation_consent",
            return_value="cancel",
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "cancel"
        assert handler.metrics["errors"] == 1

    def test_exception_in_approval_fails_closed_to_decline_url_mode(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params()

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=RuntimeError("approval system blew up"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_timeout_returns_cancel_url_mode(self, monkeypatch):
        monkeypatch.setattr(
            ElicitationHandler, "_OUTER_TIMEOUT_GRACE_SECONDS", 0
        )
        handler = ElicitationHandler("pay", {"timeout": 0.05})
        params = _url_params()

        def stall(*_args, **_kwargs):
            import time as _t
            _t.sleep(2)
            return "accept"

        with patch("tools.approval.request_elicitation_consent", side_effect=stall):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "cancel"
        assert handler.metrics["errors"] == 1

    def test_server_message_passes_through_unchanged(self):
        """The server-provided message goes in the message slot unchanged,
        even when it contains special characters or is long."""
        handler = ElicitationHandler("pay", {"timeout": 5})
        msg = "Please authorize $1,234.56 to merchant 'Acme Corp'"
        params = _url_params(msg, url="https://pay.example.com/o")

        with patch(
            "tools.approval.request_elicitation_consent",
            return_value="accept",
        ) as m:
            asyncio.run(handler(context=None, params=params))

        assert m.call_args.args[0] == msg

    def test_empty_message_falls_back_to_default(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(message="", url="https://pay.example.com/o")

        with patch(
            "tools.approval.request_elicitation_consent",
            return_value="accept",
        ) as m:
            asyncio.run(handler(context=None, params=params))

        assert "pay" in m.call_args.args[0]


class TestElicitationHandlerURLModeSanitization:
    """The server-provided URL must be sanitized before being surfaced.
    Dangerous schemes (javascript:, file:, data:) are rejected with a
    decline and never reach the consent router."""

    def test_javascript_scheme_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(
            "click me",
            url="javascript:alert(document.cookie)",
        )

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=AssertionError("unsafe URL must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1
        assert handler.metrics["declined"] == 0

    def test_file_scheme_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(url="file:///etc/passwd")

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=AssertionError("unsafe URL must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_data_scheme_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(url="data:text/html,<script>alert(1)</script>")

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=AssertionError("unsafe URL must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_empty_url_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(url="")

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=AssertionError("empty URL must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_non_string_url_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(url=None)

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=AssertionError("non-string URL must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_missing_host_declined_without_prompting(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(url="https:///path-only")

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=AssertionError("missing-host URL must not prompt"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_http_url_accepted(self):
        """http:// URLs are valid (not just https://)."""
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _url_params(url="http://localhost:8080/auth")

        with patch(
            "tools.approval.request_elicitation_consent",
            return_value="accept",
        ) as m:
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "accept"
        assert "[Open URL](http://localhost:8080/auth)" == m.call_args.args[1]


class TestSanitizeElicitationURL:
    """Unit tests for the _sanitize_elicitation_url helper."""

    def test_https_url_returned(self):
        assert _sanitize_elicitation_url("https://example.com/path") == "https://example.com/path"

    def test_http_url_returned(self):
        assert _sanitize_elicitation_url("http://localhost:8080") == "http://localhost:8080"

    def test_whitespace_stripped(self):
        assert _sanitize_elicitation_url("  https://example.com  ") == "https://example.com"

    def test_javascript_rejected(self):
        assert _sanitize_elicitation_url("javascript:alert(1)") is None

    def test_file_rejected(self):
        assert _sanitize_elicitation_url("file:///etc/passwd") is None

    def test_data_rejected(self):
        assert _sanitize_elicitation_url("data:text/html,hello") is None

    def test_ftp_rejected(self):
        assert _sanitize_elicitation_url("ftp://example.com/file") is None

    def test_empty_string_rejected(self):
        assert _sanitize_elicitation_url("") is None

    def test_whitespace_only_rejected(self):
        assert _sanitize_elicitation_url("   ") is None

    def test_non_string_rejected(self):
        assert _sanitize_elicitation_url(None) is None
        assert _sanitize_elicitation_url(123) is None
        assert _sanitize_elicitation_url(["https://x.com"]) is None

    def test_missing_host_rejected(self):
        assert _sanitize_elicitation_url("https:///path") is None

    def test_missing_scheme_rejected(self):
        assert _sanitize_elicitation_url("example.com/path") is None

    def test_uppercase_scheme_accepted(self):
        assert _sanitize_elicitation_url("HTTPS://EXAMPLE.COM") == "HTTPS://EXAMPLE.COM"

    def test_oversized_url_rejected(self):
        long_url = "https://example.com/" + "a" * 2100
        assert _sanitize_elicitation_url(long_url) is None


class TestElicitationHandlerFailureModes:

    def test_exception_in_approval_fails_closed_to_decline(self):
        handler = ElicitationHandler("pay", {"timeout": 5})
        params = _form_params()

        with patch(
            "tools.approval.request_elicitation_consent",
            side_effect=RuntimeError("approval system blew up"),
        ):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
        assert handler.metrics["errors"] == 1

    def test_timeout_returns_cancel(self, monkeypatch):
        # Shrink the outer grace window so the test budget is just the
        # handler timeout. Default grace is 5s, which makes stall durations
        # tight and the test flaky.
        monkeypatch.setattr(
            ElicitationHandler, "_OUTER_TIMEOUT_GRACE_SECONDS", 0
        )
        # _safe_numeric clamps `timeout` to a minimum of 1s, so the
        # effective wait_for budget is 1s here. Stall longer than that
        # so the wait_for reliably fires TimeoutError.
        handler = ElicitationHandler("pay", {"timeout": 0.05})
        params = _form_params()

        def stall(*_args, **_kwargs):
            import time as _t
            _t.sleep(2)
            return "accept"

        with patch("tools.approval.request_elicitation_consent", side_effect=stall):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "cancel"
        assert handler.metrics["errors"] == 1


class TestElicitationHandlerWiring:
    def test_session_kwargs_returns_callback(self):
        handler = ElicitationHandler("pay", {})
        kwargs = handler.session_kwargs()
        assert kwargs == {"elicitation_callback": handler}

    def test_default_timeout_is_300_seconds(self):
        handler = ElicitationHandler("pay", {})
        assert handler.timeout == 300

    def test_disabled_config_does_not_construct_handler(self):
        """The server task initializer checks ``elicitation.enabled`` --
        an explicit ``False`` should suppress handler creation. The unit
        of that decision lives in MCPServerTask, but the handler itself
        must remain harmless to instantiate with arbitrary config."""
        handler = ElicitationHandler("pay", {"enabled": False, "timeout": 10})
        # Just confirm it instantiates and reads timeout; the gate lives
        # at the higher layer.
        assert handler.timeout == 10


class TestElicitationHandlerContextBridge:
    """The MCP recv-loop task that fires elicitation callbacks does NOT
    inherit the agent's contextvars (HERMES_SESSION_PLATFORM etc.). The
    handler reads ``owner._pending_call_context`` -- a snapshot captured
    by the MCP tool wrapper around ``session.call_tool`` -- and replays
    it before invoking the approval router so gateway-session detection
    survives the task hop. Regression tests for that bridge."""

    def test_captured_context_is_replayed_in_consent_call(self):
        """The captured context's contextvar values must be observable
        when ``request_elicitation_consent`` runs -- otherwise the
        gateway-platform detection in approval.py sees an empty platform
        string and falls back to the CLI path (the bug this fixes)."""
        import contextvars
        from types import SimpleNamespace

        probe: contextvars.ContextVar[str] = contextvars.ContextVar(
            "elicitation_test_probe", default=""
        )
        seen: list[str] = []

        def fake_consent(*_args, **_kwargs):
            seen.append(probe.get())
            return "accept"

        token = probe.set("gateway:telegram")
        try:
            captured = contextvars.copy_context()
        finally:
            probe.reset(token)
        assert probe.get() == "", (
            "Sanity check: the probe must be empty outside the captured "
            "context, otherwise the test would pass even without replay."
        )

        owner = SimpleNamespace(_pending_call_context=captured)
        handler = ElicitationHandler("pay", {"timeout": 5}, owner=owner)
        params = _form_params()

        with patch("tools.approval.request_elicitation_consent", side_effect=fake_consent):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "accept"
        assert seen == ["gateway:telegram"], (
            f"Expected the captured contextvar to be visible inside the "
            f"consent call; got {seen!r}"
        )

    def test_missing_captured_context_falls_back_to_direct_call(self):
        """Without an owner (or with an owner that hasn't entered a tool
        call) the handler must still invoke the consent router -- just
        without the contextvar replay. Otherwise CLI/TUI sessions, which
        don't set HERMES_SESSION_PLATFORM, would break."""
        handler = ElicitationHandler("pay", {"timeout": 5}, owner=None)
        params = _form_params()

        with patch("tools.approval.request_elicitation_consent", return_value="accept") as m:
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "accept"
        assert m.call_count == 1

    def test_captured_context_can_be_replayed_multiple_times(self):
        """A single tool call may trigger more than one elicitation
        (e.g. the agent retries an MCP call within the same wrapper).
        ``Context.run`` raises if a context is re-entered, so the handler
        must ``.copy()`` before each run."""
        import contextvars
        from types import SimpleNamespace

        probe: contextvars.ContextVar[str] = contextvars.ContextVar(
            "elicitation_test_probe_multi", default=""
        )
        seen: list[str] = []

        def fake_consent(*_args, **_kwargs):
            seen.append(probe.get())
            return "accept"

        token = probe.set("gateway:slack")
        try:
            captured = contextvars.copy_context()
        finally:
            probe.reset(token)

        owner = SimpleNamespace(_pending_call_context=captured)
        handler = ElicitationHandler("pay", {"timeout": 5}, owner=owner)
        params = _form_params()

        with patch("tools.approval.request_elicitation_consent", side_effect=fake_consent):
            for _ in range(3):
                asyncio.run(handler(context=None, params=params))

        assert seen == ["gateway:slack"] * 3

    def test_pending_call_context_none_does_not_crash(self):
        """``owner._pending_call_context`` is set to None between tool
        calls. An elicitation arriving in that window must not crash."""
        from types import SimpleNamespace

        owner = SimpleNamespace(_pending_call_context=None)
        handler = ElicitationHandler("pay", {"timeout": 5}, owner=owner)
        params = _form_params()

        with patch("tools.approval.request_elicitation_consent", return_value="decline"):
            result = asyncio.run(handler(context=None, params=params))

        assert result.action == "decline"
