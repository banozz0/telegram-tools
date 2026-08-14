from __future__ import annotations

import argparse
from typing import Any

from telethon.errors import ChannelForumMissingError, RPCError

from telegram_tools import cli
from telegram_tools.bots import get_bot_profile, list_bots, resolve_bot
from telegram_tools.client import create_client
from telegram_tools.config import ConfigError, load_config
from telegram_tools.discovery import list_dialog_choices
from telegram_tools.prompts import BACK, after_action, ask_text, choose
from telegram_tools.resolver import resolve_chat
from telegram_tools.topics import get_forum_topics

# What the menu turns into a printed line instead of an exit. EntityResolutionError
# is a ValueError and PermissionError is an OSError, so both are already covered;
# anything not named here is a bug and should still be loud.
MENU_ERRORS = (ConfigError, ValueError, OSError, RPCError)

ROOT_TITLE = "telegram-tools"
ROOT_ITEMS = (
    "Chats & topics (find IDs)",
    "Search / export messages",
    "Clear topic messages",
    "My bots",
    "Check setup",
)


class MenuSession:
    """One Telegram connection and its caches, for the life of one menu run.

    Everything is lazy: the menu itself opens without credentials, and `doctor`
    never needs any. The caches are never refreshed — restarting the tool is the
    refresh.
    """

    def __init__(self, config=None) -> None:
        self._config = config
        self._client = None
        self._chats: list[Any] | None = None
        self._bots: list[Any] | None = None

    @property
    def config(self):
        if self._config is None:
            self._config = load_config()
        return self._config

    async def client(self):
        if self._client is None:
            client = create_client(self.config)
            await client.start()
            self._client = client
        return self._client

    async def chats(self):
        if self._chats is None:
            self._chats = await list_dialog_choices(await self.client())
        return self._chats

    async def topics(self, reference: str):
        client = await self.client()
        resolved = await resolve_chat(client, reference)
        try:
            return await get_forum_topics(client, resolved.input_entity)
        except ChannelForumMissingError:
            # "This chat has no topics" is an answer, not a failure: Telegram
            # rejects the request outright for a non-forum chat. Anything else
            # is a real error and must surface.
            return []

    async def bots(self):
        if self._bots is None:
            self._bots = await list_bots(await self.client())
        return self._bots

    async def bot_profile(self, reference: str):
        client = await self.client()
        return await get_bot_profile(client, await resolve_bot(client, reference))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _namespace(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


async def _call(args, *, session, runner, write) -> bool:
    """Run one action. False means it errored and the message is already printed."""
    try:
        client = await session.client() if session is not None else None
        config = session.config if session is not None else None
        await runner(args, client=client, config=config)
        return True
    except MENU_ERRORS as exc:
        write(f"error: {exc}")
        return False


async def _act(args, *, session, runner, read, write) -> bool:
    """Run one action, then ask. False means exit the menu."""
    await _call(args, session=session, runner=runner, write=write)
    return after_action(read=read, write=write)


async def _flow_discover(*, session, runner, read, write) -> bool:
    scope = choose(["Chats I manage", "Every chat"], title="Chats & topics", read=read, write=write)
    if scope is BACK:
        return True

    where = choose(["Print it here", "Write a JSON file"], title="Where should it go?", read=read, write=write)
    if where is BACK:
        return True

    json_output = None
    if where == 1:
        path = ask_text("JSON file path", read=read, write=write)
        if path is BACK:
            return True
        json_output = path

    all_chats = scope == 1
    args = _namespace(command="discover", json_output=json_output, all_chats=all_chats, admin_only=not all_chats)
    return await _act(args, session=session, runner=runner, read=read, write=write)


async def _flow_doctor(*, session, runner, read, write) -> bool:
    # No session: doctor never opens a connection, which is the point of it.
    return await _act(_namespace(command="doctor"), session=None, runner=runner, read=read, write=write)


async def _flow_search(*, session, runner, read, write) -> bool:
    raise NotImplementedError("Task 6")


async def _flow_clear(*, session, runner, read, write) -> bool:
    raise NotImplementedError("Task 7")


async def _flow_bots(*, session, runner, read, write) -> bool:
    raise NotImplementedError("Task 8")


async def run_menu(*, read=input, write=print, session=None, runner=None) -> int:
    """The looping menu. Returns 0 on a normal exit.

    The exit code belongs to the session, not to any one action inside it: a
    session can run a dozen actions and there is no honest way to fold their
    codes into one number.
    """
    session = session if session is not None else MenuSession()
    runner = runner if runner is not None else cli.run
    flows = (_flow_discover, _flow_search, _flow_clear, _flow_bots, _flow_doctor)

    try:
        while True:
            choice = choose(list(ROOT_ITEMS), title=ROOT_TITLE, read=read, write=write, back_label="Exit")
            if choice is BACK:
                return 0
            if not await flows[choice](session=session, runner=runner, read=read, write=write):
                return 0
    finally:
        await session.close()
