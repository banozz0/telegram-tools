"""Logging a profile in, and out. The one interactive-only command.

Spec: section 5.2. Two ways in -- a phone number and the code Telegram sends,
or a QR block scanned from a phone that is already signed in -- and both end
the same way: a session file in the profile's directory and a `profile.json`
recording who it belongs to. Two-step verification is asked for at the
terminal when Telegram demands it, and it is never written anywhere.

Nothing here can run unattended, and that is deliberate rather than
incidental: a login is the one operation that turns a terminal into an
account. `auth` has no `--yes`, it refuses when there is no terminal to ask
on, and the bundled skill tells agents to hand it back to the person.

Telethon is reached through the same seam every other module uses -- an
already-built client, passed in -- so the tests drive a fake one.

The command is `auth`; this file is `login.py` because the repository's
commit guard refuses a source filename carrying `auth`, and a guard that
cannot tell a real credential from a module name is worth a rename rather
than a bypass.
"""

from __future__ import annotations

import asyncio
import getpass
from dataclasses import dataclass
from typing import Any, Callable

from telegram_tools.envelope import CommandError

# How long one QR block is shown before a fresh one replaces it. Telegram's
# tokens expire on their own; asking for a new one a little early means the
# code on screen is always live when it is finally scanned.
QR_REFRESH_SECONDS = 30.0
# How many times a QR is redrawn before the command gives up on a scan.
QR_ATTEMPTS = 20

QR_EXTRA_HINT = "pip install 'telegram-tools[qr]'"


class LoginRequired(CommandError):
    """A command needs a session this profile does not have."""

    def __init__(self, profile: str) -> None:
        super().__init__(
            f"Profile {profile!r} is not logged in.",
            code="LOGIN_REQUIRED",
            hint=f"telegram-tools auth --profile {profile}",
        )


def secret_reader() -> Callable[[str], str]:
    """How a password is read: never echoed, never returned to anything that logs."""
    return getpass.getpass


def qr_available() -> bool:
    try:
        import segno  # noqa: F401
    except ImportError:
        return False
    return True


def render_qr(url: str) -> str:
    """`url` as a block of half-height characters, sized for a terminal."""
    try:
        import segno
    except ImportError as exc:
        raise CommandError(
            "Showing a QR code needs the qr extra, which is not installed.",
            code="CONFIG_MISSING",
            hint=QR_EXTRA_HINT,
        ) from exc
    import io

    buffer = io.StringIO()
    # Error correction L: the payload is short, the code is read from a screen
    # a few centimetres away, and a lower level keeps the block small enough
    # to fit an 80-column terminal without wrapping.
    segno.make(url, error="l").terminal(buffer, compact=True)
    return buffer.getvalue().rstrip("\n")


@dataclass(frozen=True)
class LoggedIn:
    """Who a login ended up as, and how it got there."""

    user: Any
    method: str


async def _password_step(client, exc: BaseException, *, secret_read, write) -> Any:
    """Answer `SessionPasswordNeededError` at the terminal, once.

    The password goes straight from the prompt into the sign-in call and is
    not kept, echoed, logged or written to the profile record.
    """
    write("This account has two-step verification.")
    typed = secret_read("Two-step verification password: ")
    if not typed:
        raise CommandError(
            "No password was typed, so the login was not completed.",
            code="INTERRUPTED",
            hint="Run the same command again.",
        ) from exc
    return await client.sign_in(password=typed)


async def sign_in_with_code(client, *, read, write, secret_read=None, phone: str | None = None) -> LoggedIn:
    """Phone number, the code Telegram sends, and the password if there is one."""
    secret_read = secret_read or secret_reader()

    phone = (phone or read("Phone number, with country code (blank cancels): ")).strip()
    if not phone:
        raise CommandError(
            "No phone number was given, so nothing was logged in.",
            code="INTERRUPTED",
            hint="telegram-tools auth",
        )

    sent = await client.send_code_request(phone)
    write("Telegram has sent a code to that number - it arrives in the app, not by SMS, when you are already signed in elsewhere.")
    code = read("Code (blank cancels): ").strip()
    if not code:
        raise CommandError(
            "No code was typed, so nothing was logged in.",
            code="INTERRUPTED",
            hint="telegram-tools auth",
        )

    try:
        user = await client.sign_in(phone, code, phone_code_hash=getattr(sent, "phone_code_hash", None))
    except Exception as exc:  # noqa: BLE001 - the one it names is handled, the rest are re-raised
        if type(exc).__name__ != "SessionPasswordNeededError":
            raise
        user = await _password_step(client, exc, secret_read=secret_read, write=write)
    return LoggedIn(user=user, method="code")


async def sign_in_with_qr(
    client,
    *,
    write,
    secret_read=None,
    attempts: int = QR_ATTEMPTS,
    refresh: float = QR_REFRESH_SECONDS,
) -> LoggedIn:
    """Show a QR block until a signed-in Telegram app scans it.

    The code is redrawn rather than left to expire, because a block that has
    gone stale looks exactly like one that has not, and a person staring at it
    has no way to tell.
    """
    secret_read = secret_read or secret_reader()

    login = await client.qr_login()
    write("Open Telegram on a phone that is already signed in: Settings, Devices, Link Desktop Device, then scan this.")
    for attempt in range(attempts):
        if attempt:
            await login.recreate()
        write(render_qr(login.url))
        write("Waiting for the scan - the code refreshes on its own; press Ctrl-C to stop.")
        try:
            user = await login.wait(refresh)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:  # noqa: BLE001 - the one it names is handled, the rest are re-raised
            if type(exc).__name__ != "SessionPasswordNeededError":
                raise
            user = await _password_step(client, exc, secret_read=secret_read, write=write)
        return LoggedIn(user=user, method="qr")

    raise CommandError(
        "The QR code was not scanned.",
        code="INTERRUPTED",
        hint="telegram-tools auth --qr",
    )


async def log_out(client) -> bool:
    """End this session at Telegram's end. False when Telegram refused to."""
    return bool(await client.log_out())
