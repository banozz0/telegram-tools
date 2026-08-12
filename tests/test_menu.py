import asyncio
from types import SimpleNamespace

import telegram_tools.cli as cli


def test_interactive_menu_prints_expected_options():
    output = []

    result = asyncio.run(
        cli.run_interactive_menu(
            read=lambda _prompt: "0",
            write=output.append,
        )
    )

    text = "\n".join(output)
    assert result == 0
    assert "1. Discover chats/topics" in text
    assert "2. Search messages" in text
    assert "3. Export messages" in text
    assert "4. Clear topic messages" in text
    assert "6. Clear all topic messages" in text
    assert "Add bot" not in text
    assert "Bot inventory" not in text
    assert "0. Exit" in text


def test_interactive_menu_builds_clear_all_topic_messages_args(monkeypatch):
    calls = {}

    async def fake_run(args):
        calls["args"] = args
        return 0

    monkeypatch.setattr(cli, "run", fake_run)

    responses = iter(["6", "@group", ""])
    result = asyncio.run(
        cli.run_interactive_menu(
            read=lambda _prompt: next(responses),
            write=lambda _message: None,
        )
    )

    assert result == 0
    assert calls["args"].command == "clear-messages"
    assert calls["args"].chat == "@group"
    assert calls["args"].all_topics is True
    assert calls["args"].execute is False
