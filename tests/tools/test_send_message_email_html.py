"""Email delivery from send_message should use rich HTML with plain fallback."""

from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from tools import send_message_tool


@pytest.mark.asyncio
async def test_send_message_email_uses_multipart_alternative_html(monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "hermes@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")

    with patch("smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server

        result = await send_message_tool._send_email(
            {},
            "recipient@example.com",
            "**Daily Brief**\n\n- One useful thing",
        )

    assert result == {"success": True, "platform": "email", "chat_id": "recipient@example.com"}
    sent: Message = mock_server.send_message.call_args[0][0]
    assert sent["Subject"] == "Hermes Agent"
    assert sent.is_multipart()
    alternatives = [part for part in sent.walk() if part.get_content_type() in {"text/plain", "text/html"}]
    assert [part.get_content_type() for part in alternatives] == ["text/plain", "text/html"]
    plain = alternatives[0].get_payload(decode=True).decode(alternatives[0].get_content_charset())
    html = alternatives[1].get_payload(decode=True).decode(alternatives[1].get_content_charset())
    assert "**Daily Brief**" in plain
    assert "font-family" in html
    assert "<strong>Daily Brief</strong>" in html
    assert "One useful thing</li>" in html
