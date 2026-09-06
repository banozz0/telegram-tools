#!/usr/bin/env python3
"""Record the menu into `docs/transcripts/`, in a real terminal, against a canned account.

The site replays these files rather than a hand-written mock-up, so what a
visitor sees is what the menu actually prints. Two rules make that true:

* **A real pty.** The menu paints only when stdout is a terminal, and a pipe
  would record the plain fallback. The child runs under `pty.openpty`, so the
  escape sequences in the `.ansi` file are the ones a person sees.
* **A canned account, never a real one.** The chats, topics, bots and message
  rows below are invented; no session is opened and no request is made. Nothing
  in a recording can leak a real chat, a real id or a real name.

    python docs/transcripts/record.py            # writes both files for this version
    python docs/transcripts/record.py --check    # re-records and diffs, changing nothing

The `.ansi` file is what `src/lib/transcript.ts` parses on the site; the `.txt`
beside it is the same session with `NO_COLOR=1`, for reading in a diff.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pty
import select
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# The journey the site slices its terminal out of: the root, the Read screen,
# then a live search built up field by field, run, tweaked and discarded, then
# the Identity screen. One entry per prompt, in order.
KEYSTROKES = [
    "2",        # Read (search live, archive, export)
    "1",        # Search live (asks Telegram)
    "1",        # Forum groups
    "1",        # Team Hermes
    "2",        # Contains
    "deploy",
    "6",        # Limit
    "20",
    "7",        # Run it (print here)
    "2",        # Tweak it
    "0",        # Back (discards)
    "0",        # Discard it and go back
    "0",        # Back to the Read screen
    "0",        # Back to the root
    "8",        # Identity (profiles, my bots)
    "1",        # Profiles on this machine
    "",         # Enter = back to the main menu
    "0",        # Exit
]


# -- the canned account ----------------------------------------------------


def _canned():
    """A `MenuSession` and a runner that answer from data invented here."""
    from types import SimpleNamespace

    from telegram_tools._core.identity import Identity, banner
    from telegram_tools.models import BotInfo, ChatChoice, TopicInfo

    chats = [
        ChatChoice(id=-1001000000001, title="Team Hermes", username="teamhermes", type="forum_group"),
        ChatChoice(id=-1001000000002, title="Ops Room", username=None, type="forum_group"),
        ChatChoice(id=-1001000000003, title="Alerts", username="agencyalerts", type="channel"),
        ChatChoice(id=-1001000000004, title="Releases", username=None, type="channel"),
        ChatChoice(id=-1001000000005, title="Agency", username=None, type="supergroup"),
        ChatChoice(id=700000001, title="Mum", username=None, type="user"),
    ]
    topics = [
        TopicInfo(id=141, title="Deploys", top_message=4812),
        TopicInfo(id=217, title="Support", top_message=4790),
    ]
    bots = [BotInfo(id=7000000001, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)]

    identity = Identity(
        platform="telegram", mode="account", label="Sven (@sven)", id="tg:user:4242", profile="default"
    )

    class Session:
        def __init__(self) -> None:
            self.config = SimpleNamespace(bot_tokens={}, profile="default")
            self.banner = None
            self.closed = False

        async def client(self):
            # The banner is learned by connecting, which is why the root screen
            # has none and every screen after the first action does.
            self.banner = banner(identity)
            return "CLIENT"

        async def chats(self):
            await self.client()
            return chats

        async def topics(self, _reference):
            await self.client()
            return topics

        async def bots(self):
            await self.client()
            return bots

        async def bot_profile(self, _reference):
            await self.client()
            return bots[0]

        def archive_scopes(self):
            # The archive's own scope list, invented like everything else here.
            return [("tg:topic:-1001000000001:141", "Deploys"), ("tg:chat:-1001000000003", "Alerts")]

        def structure_applies(self):
            # The remap picker's rows, invented like everything else here.
            return [("3f9c2a1b7d4e6f80", "3f9c2a1b7d4e6f80  2026-09-06T10:00:00Z  blueprint 5d41402abc4b2a76")]

        async def close(self) -> None:
            self.closed = True

        async def release(self) -> None:
            await self.close()
            self.banner = None

    rows = [
        "date        id     from       text",
        "2026-08-30  4812   sam        Deploy went out at 14:02, all green",
        "2026-08-29  4790   hermes     Deploying 3.4.1 now",
    ]
    profiles = [
        "default      Sven (@sven)",
        "work         Sven at work (@svenworks)",
    ]

    async def runner(args, *, client=None, config=None):
        if args.command == "search":
            print("\n".join(rows))
        elif args.command == "profiles":
            print("\n".join(profiles))
        elif args.command == "doctor":
            print("OK   Python version is supported")
            print("OK   profile default: session present")
        return 0

    return Session(), runner


def _run_menu() -> int:
    """The child: the real menu, the canned account, keystrokes on stdin."""
    sys.path.insert(0, str(ROOT / "src"))
    from telegram_tools.menu import run_menu

    session, runner = _canned()
    return asyncio.run(run_menu(session=session, runner=runner))


# -- the recording ---------------------------------------------------------


def record(*, colour: bool) -> str:
    """Run the child under a pty and give back everything it printed."""
    primary, secondary = pty.openpty()
    env = dict(os.environ, PYTHONUNBUFFERED="1", COLUMNS="80", LINES="40", TERM="xterm-256color")
    if not colour:
        env["NO_COLOR"] = "1"
    env.pop("TELEGRAM_TOOLS_PROFILE", None)

    child = subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--child"],
        stdin=secondary,
        stdout=secondary,
        stderr=secondary,
        env=env,
        close_fds=True,
    )
    os.close(secondary)

    # One keystroke at a time, each after the screen that asked for it has been
    # read. Writing them all at once makes the pty echo the whole script above
    # the first screen, and the site's parser reads an answer as the tail of the
    # prompt line it belongs to.
    chunks: list[bytes] = []
    _drain(primary, chunks)
    used = 0
    for key in KEYSTROKES:
        if child.poll() is not None:
            break
        try:
            os.write(primary, f"{key}\n".encode())
        except OSError:
            # The menu exited before this answer was asked for, which means the
            # script below no longer matches the screens. Say so rather than
            # writing half a recording.
            break
        used += 1
        _drain(primary, chunks)
    child.wait()
    os.close(primary)
    if used != len(KEYSTROKES):
        raise SystemExit(
            f"the menu exited after {used} of {len(KEYSTROKES)} keystrokes; "
            "KEYSTROKES no longer matches the screens it is driving"
        )
    return b"".join(chunks).decode("utf-8", "replace")


def _drain(fd: int, chunks: list[bytes], settle: float = 0.4) -> None:
    """Read until the child has been quiet for `settle` seconds."""
    while True:
        ready, _, _ = select.select([fd], [], [], settle)
        if not ready:
            return
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        chunks.append(chunk)


def version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from telegram_tools import __version__

    return __version__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true", help="Re-record and report differences without writing")
    args = parser.parse_args()

    if args.child:
        return _run_menu()

    stem = f"telegram-tools-{version()}-menu"
    written = []
    for suffix, colour in ((".ansi", True), (".txt", False)):
        path = HERE / f"{stem}{suffix}"
        text = record(colour=colour)
        if args.check:
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            status = "same" if before == text else "DIFFERS"
            print(f"{status}  {path.relative_to(ROOT)}")
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path.relative_to(ROOT))
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
