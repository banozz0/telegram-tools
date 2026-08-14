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


def test_pick_chat_zero_in_a_filtered_list_drops_the_filter():
    chats = [
        ChatChoice(id=1, title="Red Group", username=None, type="supergroup"),
        ChatChoice(id=2, title="Blue Group", username=None, type="supergroup"),
        ChatChoice(id=3, title="Red Alert", username=None, type="supergroup"),
    ]
    session = FakeSession(chats=chats)
    # 1 = Groups, 4 = Filter by name, "Red" = the needle (matches 2 of 3), 0 = drop
    # the filter, 1 = pick the first chat from the full (unfiltered) list.
    picked, output = pick_chat(["1", "4", "Red", "0", "1"], session=session)

    text = screens(output)
    assert text.count("Blue Group") == 2
    assert picked.reference == "1"
    assert picked.title == "Red Group"


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


def test_search_runs_with_no_filters():
    # 2 = search, 1 = forum groups, 1 = Hermes, 7 = run it, Enter = menu, 0 = exit
    code, calls, _output = run_menu(["2", "1", "1", "7", "", "0"])

    assert code == 0
    args = calls[0]
    assert args.command == "search"
    assert args.chat == "-100111"
    assert args.topic is None
    assert args.keyword is None
    assert args.from_user is None
    assert args.since is None
    assert args.until is None
    assert args.limit is None
    assert args.format == "json"
    assert args.output is None


def test_search_stages_every_filter_then_runs():
    answers = [
        "2", "1", "1",          # search > forum groups > Hermes
        "1", "1",               # Topic > Deploys (a picker, not keep/change/clear)
        "2", "2", "deploy",     # Contains > change > "deploy"
        "3", "2",               # From > Me (a three-way list, not keep/change/clear)
        "4", "2", "2026-08-01", # Since > change
        "5", "2", "2026-08-14", # Until > change
        "6", "2", "50",         # Limit > change
        "7", "0",               # Run it, then exit
    ]
    code, calls, _output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert args.topic == 141
    assert args.keyword == "deploy"
    assert args.from_user == "me"
    assert args.since == "2026-08-01"
    assert args.until == "2026-08-14"
    assert args.limit == 50


def test_search_shows_staged_values_and_clears_one():
    answers = [
        "2", "1", "1",
        "2", "2", "deploy",   # Contains = deploy
        "2", "3",             # Contains > clear
        "7", "0",
    ]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].keyword is None
    # Assert on the bracketed value, never on the column padding — a one-space
    # change to the row format is not a behaviour change.
    assert "[deploy]" in screens(output)
    assert "[(anything)]" in screens(output)


def test_search_export_asks_for_a_path_and_a_format():
    # ... 8 = export, path, 2 = csv
    answers = ["2", "1", "1", "8", "/tmp/out.csv", "2", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].output == "/tmp/out.csv"
    assert calls[0].format == "csv"


def test_search_hides_the_topic_row_for_a_non_forum_chat():
    # 2 = search, 2 = Channels, 1 = Alerts (a channel, so no topics)
    answers = ["2", "2", "1", "6", "0"]
    code, calls, output = run_menu(answers)

    text = screens(output)
    assert "Topic" not in text
    assert "1. Contains" in text
    assert calls[0].command == "search"
    assert calls[0].chat == "-100222"


def test_search_topic_picker_offers_all_topics():
    # Topic > "All topics" is the row after the two topics
    answers = ["2", "1", "1", "1", "3", "7", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].topic is None
