import asyncio

from telegram_tools import menu
from telegram_tools.config import ConfigError
from telegram_tools.models import BotCommandInfo, BotInfo, ChatChoice, TopicInfo


def reader(*answers):
    values = iter(answers)
    return lambda _prompt: next(values)


def screens(output):
    return "\n".join(output)


CHATS = [
    ChatChoice(id=-100111, title="Hermes", username="hermes", type="forum_group"),
    ChatChoice(id=-100222, title="Alerts", username=None, type="channel"),
    ChatChoice(id=333, title="Mum", username=None, type="user"),
]

TOPICS = [
    TopicInfo(id=141, title="Deploys", top_message=900),
    TopicInfo(id=217, title="Support", top_message=901),
]

BOTS = [BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)]

PROFILE = BotInfo(
    id=12345,
    username="harrybot",
    name="Harry",
    bio="Runs the agency",
    description="Ask me things",
    is_owned=True,
    has_photo=True,
    commands=[BotCommandInfo(command="start", description="Start")],
    group_rights=["post_messages"],
    channel_rights=[],
)


class FakeSession:
    """Stands in for MenuSession: same methods, canned data, no network."""

    def __init__(self, *, chats=CHATS, topics=TOPICS, bots=BOTS, profile=PROFILE, bot_tokens=None):
        self._chats = list(chats)
        self._topics = list(topics)
        self._bots = list(bots)
        self._profile = profile
        self.config = type("Config", (), {"bot_tokens": bot_tokens or {}})()
        self.closed = False
        self.topic_calls = []

    async def client(self):
        return "CLIENT"

    async def chats(self):
        return self._chats

    async def topics(self, reference):
        self.topic_calls.append(reference)
        return self._topics

    async def bots(self):
        return self._bots

    async def bot_profile(self, reference):
        return self._profile

    async def close(self):
        self.closed = True


def recorder(result=0, error=None):
    calls = []

    async def runner(args, *, client=None, config=None):
        calls.append(args)
        if error is not None:
            raise error
        return result

    return calls, runner


def run_menu(answers, *, session=None, runner=None, output=None):
    output = [] if output is None else output
    calls = []
    if runner is None:
        calls, runner = recorder()
    code = asyncio.run(
        menu.run_menu(
            read=reader(*answers),
            write=output.append,
            session=session or FakeSession(),
            runner=runner,
        )
    )
    return code, calls, output


def test_root_menu_lists_the_five_commands_and_exits_on_zero():
    code, _calls, output = run_menu(["0"])

    text = screens(output)
    assert code == 0
    assert "1. Chats & topics (find IDs)" in text
    assert "2. Search / export messages" in text
    assert "3. Clear topic messages" in text
    assert "4. My bots" in text
    assert "5. Check setup" in text
    assert "0. Exit" in text


def test_the_session_is_closed_on_exit():
    session = FakeSession()
    run_menu(["0"], session=session)
    assert session.closed is True


def test_doctor_runs_without_a_client_and_returns_to_the_menu():
    calls = []

    async def runner(args, *, client=None, config=None):
        calls.append((args, client, config))
        return 0

    code, _unused, _output = run_menu(["5", "", "0"], runner=runner)

    assert code == 0
    assert calls[0][0].command == "doctor"
    assert calls[0][1] is None
    assert calls[0][2] is None


def test_discover_builds_the_admin_only_namespace_and_loops():
    # 1 = chats & topics, 1 = chats I manage, 1 = print here, Enter = menu, 0 = exit
    code, calls, _output = run_menu(["1", "1", "1", "", "0"])

    assert code == 0
    assert len(calls) == 1
    assert calls[0].command == "discover"
    assert calls[0].all_chats is False
    assert calls[0].admin_only is True
    assert calls[0].json_output is None


def test_discover_all_chats_to_a_json_file():
    code, calls, _output = run_menu(["1", "2", "2", "/tmp/out.json", "0"])

    assert code == 0
    assert calls[0].all_chats is True
    assert calls[0].admin_only is False
    assert calls[0].json_output == "/tmp/out.json"


def test_zero_at_the_first_flow_screen_returns_to_the_root_menu():
    code, calls, output = run_menu(["1", "0", "0"])

    assert code == 0
    assert calls == []
    assert screens(output).count("1. Chats & topics (find IDs)") == 2


def test_two_actions_in_one_session():
    code, calls, _output = run_menu(["5", "", "5", "", "0"])

    assert code == 0
    assert [call.command for call in calls] == ["doctor", "doctor"]


def test_an_action_error_prints_and_returns_to_the_menu():
    _calls, runner = recorder(error=ValueError("Cannot resolve chat 'nope'."))
    code, _unused, output = run_menu(["5", "", "0"], runner=runner)

    assert code == 0
    assert "error: Cannot resolve chat 'nope'." in screens(output)
    assert screens(output).count("0. Exit") == 2


def test_a_session_acquisition_error_is_caught_and_returns_to_the_menu():
    # client() raising before the runner is ever called must still print and
    # loop, not escape run_menu — this covers _call's try around session.client()
    # and session.config, not just around the runner call.
    class BrokenSession(FakeSession):
        async def client(self):
            raise ConfigError("TELEGRAM_API_ID is required.")

    code, calls, output = run_menu(["1", "1", "1", "", "0"], session=BrokenSession())

    assert code == 0
    assert calls == []
    assert "error: TELEGRAM_API_ID is required." in screens(output)
    assert screens(output).count("0. Exit") == 2


def test_zero_after_an_action_exits():
    code, calls, _output = run_menu(["5", "0"])

    assert code == 0
    assert len(calls) == 1


def pick_chat(answers, *, session=None, forums_only=False):
    output = []
    picked = asyncio.run(
        menu._pick_chat(
            session=session or FakeSession(),
            read=reader(*answers),
            write=output.append,
            forums_only=forums_only,
        )
    )
    return picked, output


def test_pick_chat_groups_by_kind_then_picks():
    # 1 = Forum groups, 1 = Hermes
    picked, output = pick_chat(["1", "1"])

    assert picked.reference == "-100111"
    assert picked.title == "Hermes"
    assert picked.is_forum is True
    assert "1. Forum groups (1)" in screens(output)
    assert "2. Channels (1)" in screens(output)
    assert "3. Direct chats (1)" in screens(output)


def test_pick_chat_hides_empty_groups():
    session = FakeSession(chats=[CHATS[1]])
    _picked, output = pick_chat(["0"], session=session)

    text = screens(output)
    assert "1. Channels (1)" in text
    assert "Forum groups" not in text


def test_pick_chat_back_from_a_group_returns_to_the_group_list():
    # 1 = Forum groups, 0 = back to groups, 0 = back out of the picker
    picked, output = pick_chat(["1", "0", "0"])

    assert picked is menu.BACK
    assert screens(output).count("1. Forum groups (1)") == 2


def test_pick_chat_typed_reference_is_not_assumed_to_be_a_forum():
    # 4 = "Type an ID or @username" (three groups, so it is row 4)
    picked, _output = pick_chat(["4", "@somewhere"])

    assert picked.reference == "@somewhere"
    assert picked.title == "@somewhere"
    assert picked.is_forum is None


def test_pick_chat_filters_by_name():
    chats = [ChatChoice(id=index, title=f"Group {index}", username=None, type="supergroup") for index in range(12)]
    session = FakeSession(chats=chats)
    # 1 = Groups, then 9 chat rows + "Next page" (10) + "Filter by name" (11) + manual (12).
    picked, _output = pick_chat(["1", "11", "Group 11", "1"], session=session)

    assert picked.reference == "11"


def test_pick_chat_says_when_a_filter_matches_nothing():
    chats = [ChatChoice(id=1, title="Hermes", username=None, type="supergroup")]
    session = FakeSession(chats=chats)
    # 1 = Groups, then 1 chat row + "Filter by name" (2) + manual (3). Filter twice.
    picked, output = pick_chat(["1", "2", "zzz", "2", "Herm", "1"], session=session)

    assert picked.reference == "1"
    assert "Nothing matches 'zzz'." in screens(output)


def test_pick_chat_forums_only_skips_the_group_screen():
    picked, output = pick_chat(["1"], forums_only=True)

    assert picked.reference == "-100111"
    assert "Forum groups (1)" not in screens(output)
    assert "Pick a forum group" in screens(output)
