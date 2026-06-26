"""Helpers for email-client-safe Hermes outgoing email formatting."""

from __future__ import annotations

import html
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


_DEFAULT_TITLE = "Hermes Agent"


def _inline_markup(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" style="color:#0f4c81;text-decoration:underline">\1</a>',
        escaped,
    )
    return escaped


def render_polished_email_html(body: str, *, title: str | None = None) -> str:
    """Render plain Hermes text/Markdown-ish output as safe, polished HTML.

    The renderer intentionally supports a small email-safe subset rather than
    arbitrary Markdown: paragraphs, headings, bullets, code blocks, emphasis,
    and bare links. Input is escaped before inline formatting is applied.
    """
    title_text = title or _DEFAULT_TITLE
    lines = (body or "").splitlines()
    chunks: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            chunks.append("</ul>")
            in_list = False

    def close_code() -> None:
        nonlocal in_code, code_lines
        if in_code:
            code = html.escape("\n".join(code_lines), quote=False)
            chunks.append(
                "<pre style=\"background:#f6f8fb;border:1px solid #d9e2ec;"
                "border-radius:8px;padding:12px;overflow:auto;white-space:pre-wrap;"
                "font-family:SFMono-Regular,Consolas,'Liberation Mono',monospace;font-size:13px\">"
                f"{code}</pre>"
            )
            in_code = False
            code_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                close_code()
            else:
                close_list()
                in_code = True
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            close_list()
            continue

        stripped = line.strip()
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 1, 4)
            chunks.append(
                f"<h{level} style=\"margin:20px 0 8px;color:#0f2942;line-height:1.25\">"
                f"{_inline_markup(heading.group(2))}</h{level}>"
            )
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            if not in_list:
                chunks.append("<ul style=\"margin:8px 0 16px 22px;padding:0\">")
                in_list = True
            chunks.append(f"<li style=\"margin:6px 0\">{_inline_markup(bullet.group(1))}</li>")
            continue

        close_list()
        chunks.append(f"<p style=\"margin:0 0 14px\">{_inline_markup(stripped)}</p>")

    close_code()
    close_list()
    if not chunks:
        chunks.append("<p style=\"margin:0 0 14px\">(empty message)</p>")

    safe_title = html.escape(title_text, quote=False)
    content = "\n".join(chunks)
    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#f4f7fb;padding:24px;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;color:#172033;line-height:1.5">
    <div style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #dfe7f0;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(15,41,66,0.08)">
      <div style="background:linear-gradient(135deg,#0f4c81,#2a7ab0);color:#ffffff;padding:18px 22px">
        <div style="font-size:18px;font-weight:700;letter-spacing:.2px">{safe_title}</div>
      </div>
      <div style="padding:24px 22px;font-size:15px">
        {content}
      </div>
      <div style="padding:14px 22px;background:#f8fafc;color:#64748b;font-size:12px;border-top:1px solid #e6edf5">
        Sent by Hermes Agent
      </div>
    </div>
  </body>
</html>"""


def make_alternative_email_part(body: str, *, title: str | None = None) -> MIMEMultipart:
    """Return a multipart/alternative part with plain text + polished HTML."""
    alternative = MIMEMultipart("alternative")
    plain = body or ""
    alternative.attach(MIMEText(plain, "plain", "utf-8"))
    alternative.attach(MIMEText(render_polished_email_html(plain, title=title), "html", "utf-8"))
    return alternative
