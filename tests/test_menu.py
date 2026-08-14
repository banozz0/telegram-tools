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
