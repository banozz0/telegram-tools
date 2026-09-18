"""How a preflight refusal reads: the chat it checked, and a way out that exists.

`require_rights` is the one place a write says no before it calls Telegram, so
the chat it names has to be the chat whose rights were probed -- `forward` and
`copy` check the destination and the plan's first target is the source -- and
the fix it suggests has to be one the reader can act on. In a direct chat there
is no admin to ask (card agent-bo-95422318).
"""

from __future__ import annotations

import pytest

from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.plan import Mutation
from telegram_tools.adapters.account import Rights
from telegram_tools.envelope import CommandError
from telegram_tools.writes import build_plan, require_rights

IDENTITY = Identity(platform="telegram", mode="account", label="Sven (@sven)", id="tg:user:42", profile="default")


def chat(rid: str, title: str, kind: str = "supergroup") -> Target:
    return Target(rid=rid, kind="chat", title=title, path=(title,), platform="telegram", ids={"chat": rid.split(":")[-1]}, type=kind)


SOURCE = chat("tg:chat:-1001", "Team Hermes")
DESTINATION = chat("tg:chat:-1002", "Alerts")
PERSON = chat("tg:chat:777", "Harry", kind="user")


def plan_over(targets, required, rights):
    return build_plan(
        identity=IDENTITY,
        command="message forward",
        targets=targets,
        mutations=[Mutation("message.forward", targets[0].rid, {})],
        approval="prompt_y",
        rights=rights,
        required=required,
    )[0]


def test_a_refusal_names_the_chat_whose_rights_were_checked():
    """`forward` and `copy` land in `--to`, and that is the chat that said no."""
    nothing = Rights(frozenset(), frozenset({"send_messages"}))
    plan = plan_over([SOURCE, DESTINATION], ("send_messages",), nothing)

    with pytest.raises(CommandError) as refused:
        require_rights(plan, nothing, ("send_messages",), where=DESTINATION)

    assert "Alerts" in str(refused.value) and "Team Hermes" not in str(refused.value)
    assert "Alerts" in refused.value.hint


def test_a_refusal_names_the_only_target_when_a_write_has_one():
    nothing = Rights(frozenset(), frozenset({"pin_messages"}))
    plan = plan_over([SOURCE], ("pin_messages",), nothing)

    with pytest.raises(CommandError) as refused:
        require_rights(plan, nothing, ("pin_messages",))

    assert "Team Hermes" in str(refused.value)


def test_a_direct_chat_is_not_told_to_ask_its_admin():
    """Nobody administers a direct chat, so "ask an admin of Harry" is nonsense."""
    nothing = Rights(frozenset(), frozenset({"edit_messages"}))
    plan = plan_over([PERSON], ("edit_messages",), nothing)

    with pytest.raises(CommandError) as refused:
        require_rights(plan, nothing, ("edit_messages",))

    assert "Ask an admin" not in refused.value.hint
    assert "edit_messages" in str(refused.value) and "Harry" in str(refused.value)
