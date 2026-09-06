"""What a preview says about the `@mentions` in outgoing text.

Telegram has no mass-mention control beyond the text itself (section 11), so
the one thing a tool can do is make sure the person approving a send or a
reply has seen who it pings. `send` and every posting `message` verb put the
line this builds above the body of their preview.
"""

from __future__ import annotations

import re

# Telegram's own tokens for "everyone here", which a preview must not let pass
# as an ordinary mention.
BROADCAST_MENTIONS = ("@all", "@everyone", "@channel", "@here")
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z][\w]{2,31})\b")


def mentions_in(text: str | None) -> list[str]:
    """Every `@name` in outgoing text, the everyone-tokens first, each once."""
    if not text:
        return []
    found: list[str] = []
    lowered = text.lower()
    for token in BROADCAST_MENTIONS:
        if re.search(re.escape(token) + r"\b", lowered) and token not in found:
            found.append(token)
    for match in _MENTION.finditer(text):
        handle = "@" + match.group(1)
        if handle.lower() in BROADCAST_MENTIONS or handle in found:
            continue
        found.append(handle)
    return found


def mentions_line(text: str | None) -> str | None:
    """`Mentions @harry, @all (everyone in the chat)`, or None when the text names nobody."""
    found = mentions_in(text)
    if not found:
        return None
    shown = [f"{item} (everyone in the chat)" if item in BROADCAST_MENTIONS else item for item in found]
    return "Mentions " + ", ".join(shown)
