import asyncio
from pathlib import Path

from telegram_tools import menu
from telegram_tools._core.columns import width
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

ICON_TOPICS = [
    TopicInfo(id=141, title="Dobby", top_message=900, icon_emoji="\U0001f4bb"),
    TopicInfo(id=217, title="Support", top_message=901),
]


BOTS = [BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)]

# What the local archive holds, as its pickers list it: (rid, title).
SCOPES = [("tg:topic:-100111:141", "Deploys"), ("tg:chat:-100222", "Alerts")]

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

    def __init__(self, *, chats=CHATS, topics=TOPICS, bots=BOTS, profile=PROFILE, bot_tokens=None, scopes=None):
        self._chats = list(chats)
        self._scopes = list(SCOPES if scopes is None else scopes)
        self._topics = list(topics)
        self._bots = list(bots)
        self._profile = profile
        self.config = type("Config", (), {"bot_tokens": bot_tokens or {}, "profile": "default"})()
        self.closed = False
        self.released = 0
        self.topic_calls = []
        self.review_states = []
        # The identity line the real session learns when it connects. Screens
        # below the root carry it; a test that wants the bare screen sets None.
        self.banner = "Acting as: Sven (@sven) \u00b7 account"

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

    def archive_scopes(self):
        return list(self._scopes)

    def review_candidates(self, states):
        self.review_states.append(tuple(states))
        return [row for row in getattr(self, "_review", REVIEW) if row[1].split("  ")[2] in states]

    def structure_applies(self):
        return list(getattr(self, "_applies", APPLIES))

    async def close(self):
        self.closed = True

    async def release(self):
        self.released += 1
        self.closed = True
        self.banner = None


# What the remap picker sees: `(apply id, label)`.
APPLIES = [("a1b2c3d4e5f60718", "a1b2c3d4e5f60718  2026-09-06T10:00:00Z  blueprint 0123456789abcdef")]

# What the review pickers see: `(manifest id, label)`, the label's third field the state.
REVIEW = [
    ("bbbb000000000001", "bbbb000000000001  link  queued  https://x.example/a"),
    ("bbbb000000000002", "bbbb000000000002  media  failed  report.bin"),
]


def recorder(result=0, error=None):
    calls = []

    async def runner(args, *, client=None, config=None):
        calls.append(args)
        if error is not None:
            raise error
        return result

    return calls, runner


# Root rows of the nine-row menu (spec section 14), so a test says which screen
# it is on instead of a bare number. Three of them are two keystrokes now: Create
# and Delete live under Build, and My bots under Identity. `run_menu` flattens a
# tuple, so an answer list still reads as one row per entry.
DISCOVER = "1"
READ = "2"
SEARCH = ("2", "1")
ARCHIVE_SYNC = ("2", "2")
ARCHIVE_STATUS = ("2", "3")
ARCHIVE_QUERY = ("2", "4")
ARCHIVE_RETENTION = ("2", "5")
ARCHIVE_FORGET = ("2", "6")
SEND = ("3", "1")
BUILD = "4"
CREATE = ("4", "1")
DELETE = ("4", "2")
CLEAR = "5"
MANAGE = "6"
WATCH = "7"
IDENTITY = "8"
PROFILES = ("8", "1")
LOG_IN = ("8", "2")
LOG_IN_QR = ("8", "3")
LOG_OUT = ("8", "4")
MIGRATE = ("8", "5")
BOTS_ROW = ("8", "6")
DOCTOR = "9"


def run_menu(answers, *, session=None, runner=None, output=None):
    output = [] if output is None else output
    calls = []
    if runner is None:
        calls, runner = recorder()
    keystrokes = [key for answer in answers for key in (answer if isinstance(answer, tuple) else (answer,))]
    code = asyncio.run(
        menu.run_menu(
            read=reader(*keystrokes),
            write=output.append,
            session=session or FakeSession(),
            runner=runner,
        )
    )
    return code, calls, output


ROOT_ROWS = (
    "1. Find IDs (chats, topics)",
    "2. Read (search live, archive, export)",
    "3. Write (send, reply, message tools)",
    "4. Build (create, delete, structure)",
    "5. Clear messages",
    "6. Manage (admins, members, invites, settings)",
    "7. Watch (rules, runner, review queue)",
    "8. Identity (profiles, my bots)",
    "9. Check setup",
)


def test_root_menu_lists_every_command_and_exits_on_zero():
    code, _calls, output = run_menu(["0"])

    text = screens(output)
    assert code == 0
    for row in ROOT_ROWS:
        assert row in text, row
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

    code, _unused, _output = run_menu([DOCTOR, "", "0"], runner=runner)

    assert code == 0
    assert calls[0][0].command == "doctor"
    assert calls[0][1] is None
    assert calls[0][2] is None


def test_discover_builds_the_managed_chats_namespace_and_loops():
    # 1 = chats & topics, 1 = chats I manage, 1 = print here, Enter = menu, 0 = exit
    code, calls, _output = run_menu([DISCOVER, "1", "1", "", "0"])

    assert code == 0
    assert len(calls) == 1
    assert calls[0].command == "discover"
    assert calls[0].all_chats is False
    assert not hasattr(calls[0], "admin_only")
    assert calls[0].json_output is None


def test_discover_all_chats_to_a_json_file():
    code, calls, _output = run_menu([DISCOVER, "2", "2", "/tmp/out.json", "0"])

    assert code == 0
    assert calls[0].all_chats is True
    assert calls[0].json_output == "/tmp/out.json"


def test_zero_at_the_first_flow_screen_returns_to_the_root_menu():
    code, calls, output = run_menu([DISCOVER, "0", "0"])

    assert code == 0
    assert calls == []
    assert screens(output).count(ROOT_ROWS[0]) == 2


def test_discover_zero_at_where_screen_returns_to_scope_not_root():
    # 1 = discover, 1 = chats I manage, 0 = "where" back -> scope screen again,
    # 2 = every chat this time, 1 = print here, Enter = menu, 0 = exit
    answers = [DISCOVER, "1", "0", "2", "1", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    # Proves the second pass through the scope screen (not the root) is what
    # produced the call: an "every chat" answer only reaches here if 0 at
    # "Where should it go?" landed back on the scope screen.
    assert calls[0].all_chats is True
    assert screens(output).count("1. Chats I manage") == 2


def test_discover_blank_json_path_returns_to_where_screen_not_root():
    # 1 = discover, 1 = chats I manage, 2 = write a JSON file, "" = blank
    # cancels the path prompt -> back to "Where should it go?" (not root),
    # 1 = print here this time, Enter = menu, 0 = exit.
    answers = [DISCOVER, "1", "2", "", "1", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].json_output is None
    assert screens(output).count("Where should it go?") == 2


def test_two_actions_in_one_session():
    code, calls, _output = run_menu([DOCTOR, "", DOCTOR, "", "0"])

    assert code == 0
    assert [call.command for call in calls] == ["doctor", "doctor"]


def test_an_action_error_prints_and_returns_to_the_menu():
    _calls, runner = recorder(error=ValueError("Cannot resolve chat 'nope'."))
    code, _unused, output = run_menu([DOCTOR, "", "0"], runner=runner)

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

    code, calls, output = run_menu([DISCOVER, "1", "1", "", "0"], session=BrokenSession())

    assert code == 0
    assert calls == []
    assert "error: TELEGRAM_API_ID is required." in screens(output)
    assert "Failed" in screens(output)
    assert screens(output).count(ROOT_ROWS[0]) == 2


def test_zero_after_an_action_exits():
    code, calls, _output = run_menu([DOCTOR, "0"])

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
    # 1 = Groups, then 12 chats numbered across both pages, so "Filter by name"
    # is 13 (and manual 14) on page 1 and page 2 alike.
    picked, _output = pick_chat(["1", "13", "Group 11", "1"], session=session)

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


def test_pick_chat_lines_the_id_column_up_after_an_emoji_title():
    # len() ranks these two titles one way and the terminal draws them the
    # other, so a len()-padded label puts their IDs two columns apart.
    chats = [
        ChatChoice(id=-100111, title="📚 Vaults", username=None, type="supergroup"),
        ChatChoice(id=-100222, title="⚙️ Alerts", username=None, type="supergroup"),
    ]
    session = FakeSession(chats=chats)
    _picked, output = pick_chat(["1", "0", "0"], session=session)

    rows = [line for line in screens(output).splitlines() if "-100" in line]
    assert len(rows) == 2
    # "1. " + a 32-column name + two spaces: both IDs start at column 37.
    assert [width(row[: row.index("-100")]) for row in rows] == [37, 37]


def test_search_runs_with_no_filters():
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 7 = run it, Enter = menu, 0 = exit
    code, calls, _output = run_menu([SEARCH, "1", "1", "7", "", "0"])

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
        SEARCH, "1", "1",       # search > forum groups > Hermes
        "1", "1",               # Topic > Deploys (a picker, not keep/change/clear)
        "2", "deploy",          # Contains: unset -> straight to the value prompt
        "3", "2",               # From > Me (a three-way list, not keep/change/clear)
        "4", "2026-08-01",      # Since: unset -> straight to the value prompt
        "5", "2026-08-14",      # Until: unset -> straight to the value prompt
        "6", "50",               # Limit: unset -> straight to the value prompt
        "7", "0",               # Run it, then exit
    ]
    code, calls, output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert args.topic == 141
    assert args.keyword == "deploy"
    assert args.from_user == "me"
    assert args.since == "2026-08-01"
    assert args.until == "2026-08-14"
    assert args.limit == 50
    # The Topic row shows the title too, not just the raw id.
    assert "[141 Deploys]" in screens(output)


def test_search_shows_staged_values_and_clears_one():
    answers = [
        SEARCH, "1", "1",
        "2", "deploy",         # Contains: unset -> straight to the value prompt = deploy
        "2", "3",              # Contains is set now -> keep/change/clear > clear
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
    answers = [SEARCH, "1", "1", "8", "/tmp/out.csv", "2", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].output == "/tmp/out.csv"
    assert calls[0].format == "csv"


def test_search_hides_the_topic_row_for_a_non_forum_chat():
    # 2 = search, 2 = Channels, 1 = Alerts (a channel, so no topics)
    answers = [SEARCH, "2", "1", "6", "0"]
    code, calls, output = run_menu(answers)

    text = screens(output)
    assert "Topic" not in text
    assert "1. Contains" in text
    assert calls[0].command == "search"
    assert calls[0].chat == "-100222"


def test_search_topic_picker_offers_all_topics():
    # Topic > "All topics" is the row after the two topics
    answers = [SEARCH, "1", "1", "1", "3", "7", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].topic is None


def test_search_topic_picker_says_the_chat_has_no_topics():
    session = FakeSession(topics=[])
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 1 = Topic row (no topics), 0 = back to
    # the staging screen, 0 = back to the chat picker, 0 = back out of the picker to
    # root, 0 = exit
    answers = [SEARCH, "1", "1", "1", "0", "0", "0", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls == []
    text = screens(output)
    assert "That chat has no topics." in text
    # It returned to the staging screen rather than crashing or exiting: the
    # screen renders once before the Topic row is chosen, once again after.
    assert text.count("Main › Read › Search › Hermes\n") == 2


def test_the_topic_picker_shows_the_emoji_telegram_draws_and_leaves_a_bare_topic_bare():
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 1 = the Topic row, then back out.
    answers = [SEARCH, "1", "1", "1", "0", "0", "0", "0", "0"]
    code, _calls, output = run_menu(answers, session=FakeSession(topics=ICON_TOPICS))

    assert code == 0
    text = screens(output)
    assert "1. 141     \U0001f4bb Dobby" in text
    assert "2. 217     Support" in text


def test_the_clear_ticker_shows_the_topic_emoji_too():
    # 5 = clear, 1 = Hermes, 0 = back out of the ticker, 0 = back out of the picker,
    # 0 = exit. Nothing is ticked, so no dry-run and no DELETE gate is reached.
    answers = [CLEAR, "1", "0", "0", "0"]
    code, calls, output = run_menu(answers, session=FakeSession(topics=ICON_TOPICS))

    assert code == 0
    assert calls == []
    text = screens(output)
    assert "1. [ ] 141     \U0001f4bb Dobby" in text
    assert "2. [ ] 217     Support" in text


def test_search_zero_at_staging_returns_to_the_chat_picker_not_root():
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 0 = staging back -> chat picker,
    # 4 = type an ID/username this time, a new chat, 7 = run it (topic row is
    # shown since the typed chat's forum-ness is unknown), Enter, 0 = exit
    answers = [SEARCH, "1", "1", "0", "4", "@newchat", "7", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    # Proves the chat picker ran again (not root): "@newchat" only ends up as
    # the search target if 0 at the staging screen landed on the chat picker.
    assert calls[0].command == "search"
    assert calls[0].chat == "@newchat"


def test_search_staging_back_discards_and_says_so():
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 2 = Contains (unset -> straight to
    # the value prompt), "deploy" = the value, 0 = staging back -> asks first,
    # 0 = discard (says so) -> chat picker, 0 = chat picker back, 0 = exit.
    answers = [SEARCH, "1", "1", "2", "deploy", "0", "0", "0", "0", "0"]
    code, calls, output = run_menu(answers)

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "0. Back (discards)" in text
    assert "Main › Read › Search › Hermes › 1 staged change\n" in text
    assert "Discarded 1 staged change." in text


def test_clear_dry_runs_first_then_executes():
    # 5 = clear, 1 = Hermes, 1 = tick Deploys, 5 = continue (3 is All topics,
    # 4 Select all), 1 = for real, Enter = main menu, 0 = exit
    answers = [CLEAR, "1", "1", "5", "1", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert len(calls) == 2
    assert calls[0].command == "clear-messages"
    assert calls[0].chat == "-100111"
    assert calls[0].topics == [141]
    assert calls[0].all_topics is False
    assert calls[0].execute is False
    assert calls[0].batch_size == 100
    assert calls[1].execute is True
    assert calls[1].topics == [141]


def test_clear_stops_at_the_dry_run_when_you_go_back():
    # 5 = clear, 1 = Hermes, 1 = tick Deploys, 5 = continue, dry-run runs, 0 = back to
    # the ticker, 0 = back to the chat picker, 0 = back out of the picker to root, 0 = exit
    answers = [CLEAR, "1", "1", "5", "0", "0", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert len(calls) == 1
    assert calls[0].execute is False


def test_clear_zero_at_dry_run_returns_to_the_ticker_with_ticks_preserved():
    # 5 = clear, 1 = Hermes, 1 = tick Deploys, 5 = continue, dry-run runs, 0 = back
    # to the ticker (Deploys should still be ticked), 5 = continue again with no
    # further ticking, 1 = for real, Enter, 0 = exit
    answers = [CLEAR, "1", "1", "5", "0", "5", "1", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    # One dry run, not two: the same ticks on the same chat were scanned a
    # moment ago, so the second Continue goes straight to the dry-run screen.
    assert len(calls) == 2
    assert calls[0].topics == [141]
    assert calls[0].execute is False
    assert calls[1].execute is True
    assert calls[1].topics == [141]
    assert "Same topics as the last dry-run; its count still stands." in screens(output)
    assert screens(output).count("[x] 141") == 2


def test_clear_ticker_accepts_several_numbers_in_one_answer():
    # 3 = clear, 1 = Hermes, "1 2" ticks both topics in a single answer (item rows
    # only, so this is legal), 4 = continue, dry-run covers both, 0 = decline the
    # real pass, 0 = chat picker back, 0 = out of the picker to root, 0 = exit.
    answers = [CLEAR, "1", "1 2", "5", "0", "0", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert len(calls) == 1
    assert calls[0].command == "clear-messages"
    assert calls[0].all_topics is True
    assert calls[0].topics is None
    assert calls[0].execute is False


def test_clear_every_topic_uses_all_topics():
    # 3 = select all (two topics + select all), 4 = continue
    answers = [CLEAR, "1", "3", "4", "1", "", "0"]
    code, calls, _output = run_menu(answers)

    assert calls[0].all_topics is True
    assert calls[0].topics is None
    assert calls[1].all_topics is True


def test_clear_does_not_offer_the_real_pass_when_the_dry_run_errors():
    _calls, runner = recorder(error=PermissionError("Current user lacks Telegram delete_messages permission in this chat."))
    answers = [CLEAR, "1", "1", "5", "0"]
    code, _unused, output = run_menu(answers, runner=runner)

    assert code == 0
    text = screens(output)
    assert "error: Current user lacks Telegram delete_messages permission in this chat." in text
    assert "Clear them for real" not in text


def test_clear_says_when_a_chat_has_no_topics():
    session = FakeSession(topics=[])
    # 5 = clear, 1 = Hermes has no topics -> back to the forum picker (one screen
    # back, not the root), 0 = out of the picker, 0 = exit
    answers = [CLEAR, "1", "0", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls == []
    assert "That chat has no topics." in screens(output)
    assert screens(output).count("Pick a forum group") == 2


def test_clear_offers_manual_entry_when_there_are_no_forum_groups():
    # No forum groups at all: the picker's list is empty, so `pick` would
    # normally bail out before ever showing the manual escape hatch.
    session = FakeSession(chats=[], topics=[])
    # 5 = clear topic messages, 2 = "Type an ID or @username" (the only rows
    # on an empty list are the two extras), type an id, no topics -> the picker
    # again, 0 = out of it, 0 = exit.
    code, calls, output = run_menu([CLEAR, "2", "-100999", "0", "0"], session=session)

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "Type an ID or @username" in text
    assert "That chat has no topics." in text


def test_bots_lists_and_prints_a_profile():
    # 6 = my bots, 1 = harrybot, 0 out of the bot screen to the list, 0 = root, 0 = exit
    code, calls, output = run_menu([BOTS_ROW, "1", "0", "0", "0", "0"])

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "1. @harrybot  Harry" in text
    assert "Bio: Runs the agency" in text
    assert "1. Edit this bot" in text


def test_bots_with_no_username_matches_the_existing_formatters():
    # bots.py's own formatters say "(no username)" in a table row and "bot
    # <id>" in a heading; the menu must follow those, not invent "@<id>".
    bot = BotInfo(id=99999, username=None, name="Nameless", bio=None, description=None, is_owned=True)
    session = FakeSession(bots=[bot], profile=bot)
    # 6 = my bots, 1 = the only bot, 0 = back to the list, 0 = root, 0 = exit
    code, calls, output = run_menu([BOTS_ROW, "1", "0", "0", "0", "0"], session=session)

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "1. (no username)  Nameless" in text
    assert "bot 99999" in text
    assert "@99999" not in text


def test_bots_saves_a_profile_to_json():
    code, calls, _output = run_menu([BOTS_ROW, "1", "2", "/tmp/bot.json", "0", "0"])

    assert calls[0].command == "bots"
    assert calls[0].bot == "12345"
    assert calls[0].json_output == "/tmp/bot.json"


def test_bot_edit_stages_a_name_and_applies_without_yes():
    # 4, 1 = bot, 1 = edit, 1 = Name, 2 = change, text, 8 = review & apply
    answers = [BOTS_ROW, "1", "1", "1", "2", "Harry Two", "8", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    args = calls[0]
    assert args.command == "bots"
    assert args.bot == "12345"
    assert args.name == "Harry Two"
    assert args.bio is None
    assert args.yes is False


def test_bot_edit_clears_a_bio_with_an_empty_string():
    answers = [BOTS_ROW, "1", "1", "2", "3", "8", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert calls[0].bio == ""


def test_bot_edit_unset_field_skips_the_keep_change_clear_screen():
    # A bot with no bio at all: PROFILE (used elsewhere) always has one set, so this
    # uses its own profile to reach the truly-unset case.
    profile = BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)
    session = FakeSession(profile=profile)
    # 4 = my bots, 1 = harrybot, 1 = edit, 2 = Bio (unset -> straight to the value
    # prompt, no keep/change/clear screen), "hello" = the typed value, 8 = apply,
    # Enter, 0 = exit.
    answers = [BOTS_ROW, "1", "1", "2", "hello", "8", "", "0", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls[0].bio == "hello"
    assert "Keep it as (not set)" not in screens(output)


def test_bot_edit_shows_current_values_and_staged_changes():
    # ... 0 = field list back -> asks first, 0 = discard (to the bot's own screen),
    # 0 = the list, 0 = root, 0 = exit
    answers = [BOTS_ROW, "1", "1", "1", "2", "Harry Two", "0", "0", "0", "0", "0", "0"]
    _code, _calls, output = run_menu(answers)

    text = screens(output)
    # The bracketed value, not the column padding.
    assert "[Harry]" in text
    assert "[Harry -> Harry Two]" in text
    assert "Discarded 1 staged change." in text


def test_bot_edit_staging_the_name_none_is_not_shown_as_cleared():
    # "none" is the sentinel for clearing rights, not an ordinary staged value.
    # Typed as a *name* it must render as the literal value, not "(cleared)".
    answers = [BOTS_ROW, "1", "1", "1", "2", "none", "0", "0", "0", "0", "0", "0"]
    _code, _calls, output = run_menu(answers)

    text = screens(output)
    assert "[Harry -> none]" in text
    assert "(cleared)" not in text


def test_bot_edit_refuses_token_fields_without_a_token():
    # ... 0 = field list back (to the bot's own screen), 0 = the list, 0 = root, 0 = exit
    answers = [BOTS_ROW, "1", "1", "4", "0", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)

    text = screens(output)
    assert calls == []
    assert "[/start]  (needs this bot's token)" in text
    assert "Set TELEGRAM_BOT_TOKENS" in text
    # Photo is the exception: setting one never needs a token, only clearing
    # does, so its row carries the clearing-specific text, not the blanket one.
    assert "[set]  (clearing needs this bot's token)" in text


def test_bot_edit_rights_toggle_with_a_token():
    session = FakeSession(bot_tokens={"harry": "12345:AAtoken"})
    # 6 = group rights, 2 = change, then the toggle: post_messages is preselected
    # (row 2 of page 1), tick change_info (row 1), Continue is numbered after
    # every right (16 of them) on every page.
    answers = [BOTS_ROW, "1", "1", "6", "2", "1", "18", "8", "", "0", "0"]
    _code, calls, _output = run_menu(answers, session=session)

    assert calls[0].group_rights == "change_info,post_messages"


def test_bot_edit_clears_rights_with_none():
    session = FakeSession(bot_tokens={"harry": "12345:AAtoken"})
    answers = [BOTS_ROW, "1", "1", "6", "3", "8", "", "0", "0"]
    _code, calls, _output = run_menu(answers, session=session)

    assert calls[0].group_rights == "none"


def test_bot_edit_apply_with_nothing_staged_says_so():
    # ... 0 = field list back (to the bot's own screen), 0 = the list, 0 = root, 0 = exit
    answers = [BOTS_ROW, "1", "1", "8", "0", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)

    assert calls == []
    assert "Nothing staged yet." in screens(output)


def test_bot_edit_zero_at_field_list_returns_to_the_bots_own_screen_not_root():
    # 4 = bots, 1 = harrybot, 1 = edit, 0 = field list back (nothing staged) ->
    # the bot's own screen, 2 = save profile (proves we landed there, not root),
    # path, Enter, 0 = exit
    answers = [BOTS_ROW, "1", "1", "0", "2", "/tmp/bot.json", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    # Proves the bot's own screen ran again (not root): "2" only saves this
    # bot's profile if 0 at the field list landed on that screen.
    assert calls[0].command == "bots"
    assert calls[0].json_output == "/tmp/bot.json"


def test_a_picker_error_prints_and_returns_to_the_menu():
    class ExplodingSession(FakeSession):
        async def chats(self):
            raise ValueError("Cannot resolve chat.")

    code, calls, output = run_menu([SEARCH, "", "0"], session=ExplodingSession())

    assert code == 0
    assert calls == []
    assert "error: Cannot resolve chat." in screens(output)


# --- send -------------------------------------------------------------------


def test_send_stages_a_topic_and_a_message():
    # 3 1 = write > send, 1 = forum groups, 1 = Hermes, 1 = Topic row, 1 = Deploys,
    # 2 = Message, 5 = Send it (4 is Reply to)
    answers = [SEND, "1", "1", "1", "1", "2", "ship it", ".", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert args.command == "send"
    assert args.chat == "-100111"
    assert args.topic == 141
    assert args.text == "ship it"
    # The menu never skips the preview the flags would have shown.
    assert args.yes is False
    assert "[141 Deploys]" in screens(output)


def test_send_without_choosing_a_topic_goes_to_the_chat_itself():
    answers = [SEND, "1", "1", "2", "hi", ".", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].topic is None
    assert "[(the chat itself)]" in screens(output)


def test_send_topic_picker_offers_the_chat_itself():
    # Topic > row 3 is the extra after the two topics
    answers = [SEND, "1", "1", "1", "3", "2", "hi", ".", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].topic is None
    assert "The chat itself (no topic)" in screens(output)


def test_send_hides_the_topic_row_for_a_non_forum_chat():
    # 3 1 = write > send, 2 = Channels, 1 = Alerts, 1 = Message, 4 = Send it
    answers = [SEND, "2", "1", "1", "hi", ".", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert "Topic" not in screens(output)
    assert calls[0].chat == "-100222"


def test_send_says_a_chat_with_no_topics_goes_to_the_chat():
    session = FakeSession(topics=[])
    answers = [SEND, "1", "1", "1", "2", "hi", ".", "5", "", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls[0].topic is None
    assert "no topics" in screens(output)


def test_send_refuses_to_run_without_a_message():
    # 5 = Send it with nothing staged, then 0 back out of each screen to the root.
    answers = [SEND, "1", "1", "5", "0", "0", "0", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls == []
    assert "Type a message or attach a file first." in screens(output)


def test_send_shows_a_long_message_on_one_line():
    body = "line one\nline two that keeps going well past the width of the row"
    answers = [SEND, "1", "1", "2", body, ".", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].text == body
    text = screens(output)
    assert "line one / line two" in text
    assert "…" in text


# --- create -----------------------------------------------------------------


def test_create_group_asks_for_a_title_and_description():
    # 4 = create, 1 = Group
    answers = [CREATE, "1", "Hermes", "the agency", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert args.command == "create"
    assert args.create_kind == "group"
    assert args.title == "Hermes"
    assert args.about == "the agency"
    assert args.forum is False
    assert args.yes is False


def test_create_forum_group_sets_forum():
    # 2 = Forum group; a blank description means none
    answers = [CREATE, "2", "Hermes", "", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].forum is True
    assert calls[0].about is None


def test_create_channel_asks_for_a_broadcast():
    answers = [CREATE, "3", "Alerts", "", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].create_kind == "channel"
    assert calls[0].title == "Alerts"


def test_create_topic_picks_a_forum_group_first():
    # 4 = Topic in a forum group, 1 = Hermes (the only forum group)
    answers = [CREATE, "4", "1", "Deploys", "", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert args.create_kind == "topic"
    assert args.chat == "-100111"
    assert args.title == "Deploys"


def test_create_cancelling_the_title_returns_to_the_kind_list():
    answers = [CREATE, "1", "", "0", "0", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls == []
    assert screens(output).count("1. Group") == 2


def test_send_reoffers_the_staged_message_in_the_header():
    # 2 = Message twice: the second header must carry what was already typed, so
    # keeping it does not mean typing it again. A blank first line keeps it.
    answers = [SEND, "1", "1", "2", "hiiiii", ".", "2", "", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "hiiiii"
    assert "Message [hiiiii] (blank cancels, . on its own line ends it):" in screens(output)


def test_send_message_header_is_bare_before_anything_is_typed():
    answers = [SEND, "1", "1", "2", "hi", ".", "5", "", "0"]
    _code, _calls, output = run_menu(answers)

    assert "Message (blank cancels, . on its own line ends it):" in screens(output)


def test_send_shows_a_long_staged_message_cut_in_the_header():
    body = "line one\nline two that keeps going well past the width of the row"
    answers = [SEND, "1", "1", "2", body, ".", "2", "", "5", "", "0"]
    _code, calls, output = run_menu(answers)

    assert calls[0].text == body
    reoffer = [line for line in output if line.startswith("Message [")][0]
    assert "line one / line two" in reoffer
    assert "\n" not in reoffer


def test_send_takes_a_multi_line_message_from_the_menu():
    answers = [SEND, "1", "1", "2", "deploy is green", "all 300 tests pass", ".", "5", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "deploy is green\nall 300 tests pass"


def test_send_pasted_lines_become_body_not_menu_answers():
    # The hazard this replaced: line two used to be read as the next menu choice.
    answers = [SEND, "1", "1", "2", "one", "2", "3", ".", "5", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "one\n2\n3"


def test_send_attaches_a_file_from_the_menu():
    # 3 = Files row, then a path; 5 = Send it
    answers = [SEND, "1", "1", "3", "/tmp/shot.png", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].files == ["/tmp/shot.png"]
    # A file alone is a valid send: no message body required.
    assert calls[0].text is None
    assert "[shot.png]" in screens(output)


def test_send_attaches_several_files():
    # Files row a second time offers Add another / Remove them all
    answers = [SEND, "1", "1", "3", "/tmp/a.png", "3", "1", "/tmp/b.pdf", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].files == ["/tmp/a.png", "/tmp/b.pdf"]
    assert "[a.png +1 more]" in screens(output)


def test_send_can_clear_the_attachments():
    answers = [SEND, "1", "1", "3", "/tmp/a.png", "3", "2", "2", "hi", ".", "5", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].files is None
    assert calls[0].text == "hi"
    assert "[(none)]" in screens(output)


def test_send_a_caption_with_a_file_sends_both():
    answers = [SEND, "1", "1", "2", "look at this", ".", "3", "/tmp/a.png", "5", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "look at this"
    assert calls[0].files == ["/tmp/a.png"]


def test_send_cancelling_the_file_path_stages_nothing():
    answers = [SEND, "1", "1", "3", "", "2", "hi", ".", "5", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].files is None


# --- after an action: Run it again / Tweak it / Main menu ---------------------


def test_after_run_runs_the_same_search_again():
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 7 = run, 1 = run it again, Enter, 0
    code, calls, output = run_menu([SEARCH, "1", "1", "7", "1", "", "0"])

    assert code == 0
    assert [call.chat for call in calls] == ["-100111", "-100111"]
    assert "1. Run it again" in screens(output)
    assert "2. Tweak it" in screens(output)
    assert "3. Main menu" in screens(output)


def test_search_tweak_returns_to_the_form_with_the_filters_kept():
    # ... 2 = contains (unset, so straight to the prompt), "hello", 7 = run,
    # 2 = tweak, 6 = limit, 5, 7 = run again, Enter, 0
    answers = [SEARCH, "1", "1", "2", "hello", "7", "2", "6", "5", "7", "", "0"]
    _code, calls, output = run_menu(answers)

    assert len(calls) == 2
    assert calls[0].keyword == "hello"
    assert calls[0].limit is None
    assert calls[1].keyword == "hello"
    assert calls[1].limit == 5
    assert "Contains       [hello]" in screens(output)


def test_after_run_says_not_done_when_the_action_was_declined_and_failed_on_an_error():
    _calls, declined = recorder(result=1)
    _code, _unused, output = run_menu([SEARCH, "1", "1", "7", "", "0"], runner=declined)
    assert "Not done" in screens(output)

    _calls, broken = recorder(error=ValueError("no"))
    _code, _unused, output = run_menu([SEARCH, "1", "1", "7", "", "0"], runner=broken)
    assert "Failed" in screens(output)


def test_discover_offers_run_again_but_no_tweak():
    # 1 = discover, 1 = managed, 1 = print, 1 = run it again, Enter = menu, 0
    _code, calls, output = run_menu([DISCOVER, "1", "1", "1", "", "0"])

    assert [call.all_chats for call in calls] == [False, False]
    assert "1. Run it again" in screens(output)
    assert "Tweak it" not in screens(output)


def test_doctor_keeps_the_plain_enter_or_zero_prompt():
    _code, calls, output = run_menu([DOCTOR, "", "0"])

    assert [call.command for call in calls] == ["doctor"]
    assert "Run it again" not in screens(output)
    assert "Main menu" not in screens(output)


def test_send_tweak_keeps_the_message_files_and_topic():
    # 3 1 = write > send, 1 = forum groups, 1 = Hermes, 2 = message, "hi", ".", 5 = send,
    # 2 = tweak, 5 = send again, Enter, 0
    answers = [SEND, "1", "1", "2", "hi", ".", "5", "2", "5", "", "0"]
    _code, calls, output = run_menu(answers)

    assert [call.text for call in calls] == ["hi", "hi"]
    assert screens(output).count("Message   [hi]") >= 2


def test_create_offers_another_instead_of_a_rerun():
    # 4 = create, 1 = group, "Team", blank description, 1 = create another -> the
    # kind list, 3 = channel, "News", blank description, Enter, 0
    answers = [CREATE, "1", "Team", "", "1", "3", "News", "", "", "0", "0"]
    _code, calls, output = run_menu(answers)

    assert [(call.create_kind, call.title) for call in calls] == [("group", "Team"), ("channel", "News")]
    assert "1. Create another" in screens(output)
    assert "Run it again" not in screens(output)


# --- send: back asks before discarding ---------------------------------------


def test_send_back_with_a_message_asks_before_discarding():
    # ... 2 = message, "hi", ".", 0 = back -> asks, 1 = keep editing, 0 = back -> asks
    # again, 0 = discard -> the chat picker, 0 = Write, 0 = root, 0 = exit
    answers = [SEND, "1", "1", "2", "hi", ".", "0", "1", "0", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)

    text = screens(output)
    assert calls == []
    assert text.count("Unsent message") == 2
    assert "1. Keep editing" in text
    assert "0. Discard it and go back" in text
    # Keep editing showed the form again with the message still staged.
    assert text.count("Message   [hi]") == 2
    assert "Discarded the unsent message." in text


def test_send_back_with_nothing_staged_does_not_ask():
    _code, calls, output = run_menu([SEND, "1", "1", "0", "0", "0", "0"])

    assert calls == []
    assert "Unsent message" not in screens(output)


# --- clear: the audit gaps and what it remembers ------------------------------


def test_clear_all_topics_row_is_the_all_topics_flag():
    # 5 = clear, 1 = Hermes, 3 = All topics (the row after the two topics), dry-run,
    # 1 = for real, Enter, 0
    _code, calls, output = run_menu([CLEAR, "1", "3", "1", "", "0"])

    assert "3. All topics (no need to tick)" in screens(output)
    assert calls[0].all_topics is True
    assert calls[0].topics is None
    assert calls[1].execute is True
    assert calls[1].all_topics is True


def test_clear_batch_size_is_an_advanced_row_on_the_dry_run_screen():
    # ... 5 = continue, dry-run, 2 = batch size, 25, 1 = for real, Enter, 0
    _code, calls, output = run_menu([CLEAR, "1", "1", "5", "2", "25", "1", "", "0"])

    text = screens(output)
    assert "2. Batch size [100]" in text
    assert "2. Batch size [25]" in text
    assert calls[0].batch_size == 100
    assert calls[1].execute is True
    assert calls[1].batch_size == 25


def test_clear_remembers_the_ticks_when_the_same_chat_is_picked_again():
    # 5 = clear, 1 = Hermes, 1 = tick Deploys, 5 = continue (dry-run), 0 = back to
    # the ticker, 0 = back to the picker, 1 = Hermes again (still ticked),
    # 5 = continue (same ticks: no second scan), 0 = ticker, 0 = picker, 0 = root, 0
    answers = [CLEAR, "1", "1", "5", "0", "0", "1", "5", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)

    assert len(calls) == 1
    assert calls[0].topics == [141]
    # Ticked once, shown ticked four times: after the tick, back from the dry-run
    # screen, after re-picking the chat, and back from the dry-run screen again.
    assert screens(output).count("[x] 141") == 4
    assert "Same topics as the last dry-run; its count still stands." in screens(output)


def test_clear_more_topics_after_the_real_pass_starts_from_a_clean_ticker():
    # ... 1 = for real, 1 = clear more topics -> the ticker with nothing ticked, 0, 0, 0
    _code, calls, output = run_menu([CLEAR, "1", "1", "5", "1", "1", "0", "0", "0", "0"])

    text = screens(output)
    assert calls[1].execute is True
    assert "1. Clear more topics" in text
    assert text.count("[x] 141") == 1
    assert text.count("[ ] 141") == 2


# --- bots: the audit gaps ------------------------------------------------------


def test_bots_saves_the_whole_bot_list_to_json():
    # 6 = my bots, 2 = save the list (row after the one bot), path, Enter, 0
    _code, calls, output = run_menu([BOTS_ROW, "2", "/tmp/bots.json", "", "0", "0"])

    assert "2. Save the bot list to a JSON file" in screens(output)
    assert calls[0].command == "bots"
    assert calls[0].bot is None
    assert calls[0].json_output == "/tmp/bots.json"


def test_bots_typed_username_shows_a_bot_you_do_not_own_read_only():
    other = BotInfo(id=777, username="otherbot", name="Other", bio="Not mine", description=None, is_owned=False)
    session = FakeSession(profile=other)
    # 6 = my bots, 3 = type a bot, "@otherbot", 0 = back to the list, 0 = root, 0 = exit
    _code, calls, output = run_menu([BOTS_ROW, "3", "@otherbot", "0", "0", "0", "0"], session=session)

    text = screens(output)
    assert calls == []
    assert "3. Type a bot @username, ID or nickname" in text
    assert "Bio: Not mine" in text
    assert "Note: not owned by you - read-only." in text
    assert "Edit this bot" not in text
    assert "1. Save this profile to a JSON file" in text


def test_bots_typo_in_a_typed_username_returns_to_the_list_not_the_root():
    class Session(FakeSession):
        async def bot_profile(self, reference):
            raise ValueError(f"{reference!r} is not a bot.")

    _code, calls, output = run_menu([BOTS_ROW, "3", "nope", "0", "0", "0"], session=Session())

    text = screens(output)
    assert calls == []
    assert "error: 'nope' is not a bot." in text
    assert text.count("1. @harrybot  Harry") == 2


def test_bots_with_none_of_your_own_still_offers_the_lookup():
    session = FakeSession(bots=[])
    _code, calls, output = run_menu([BOTS_ROW, "0", "0", "0"], session=session)

    text = screens(output)
    assert calls == []
    assert "No bots of your own" in text
    assert "1. Type a bot @username, ID or nickname" in text


def test_bots_typed_nickname_resolves_through_the_token_like_the_flags_do():
    class Session(FakeSession):
        def __init__(self):
            super().__init__(bot_tokens={"harry": "12345:AAtoken"})
            self.references = []

        async def bot_profile(self, reference):
            self.references.append(reference)
            return self._profile

    session = Session()
    _code, _calls, _output = run_menu([BOTS_ROW, "3", "harry", "0", "0", "0", "0"], session=session)

    # The nickname never reaches Telegram: the token's own bot id does.
    assert session.references == ["12345"]


def test_bot_edit_more_fetches_the_profile_again():
    class Session(FakeSession):
        def __init__(self):
            super().__init__()
            self.profile_calls = 0

        async def bot_profile(self, reference):
            self.profile_calls += 1
            return self._profile

    session = Session()
    # 6, 1 = harrybot, 1 = edit, 1 = name, 2 = change, "Harry Two", 8 = apply,
    # 1 = edit more (fresh profile), 0 = field list back, 0 = the list, 0 = root, 0
    answers = [BOTS_ROW, "1", "1", "1", "2", "Harry Two", "8", "1", "0", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers, session=session)

    assert calls[0].name == "Harry Two"
    assert "1. Edit more" in screens(output)
    assert session.profile_calls == 2


# --- breadcrumbs and the colour boundary ---------------------------------------


def test_every_screen_below_the_root_carries_its_trail():
    # 2 1 = read > search live, 1 = forum groups, 1 = Hermes, 3 = From, 0 = back to
    # the form, 0 = back to the picker, 0 = read, 0 = root, 0 = exit
    _code, _calls, output = run_menu([SEARCH, "1", "1", "3", "0", "0", "0", "0", "0"])
    text = screens(output)
    assert "Main › Read › Search › Pick a chat\n" in text
    assert "Main › Read › Search › Pick a chat › Forum groups\n" in text
    assert "Main › Read › Search › Hermes\n" in text
    assert "Main › Read › Search › Hermes › From\n" in text

    # 6 = my bots, 1 = harrybot, 1 = edit, 1 = name, then back out four times and exit
    _code, _calls, output = run_menu([BOTS_ROW, "1", "1", "1", "0", "0", "0", "0", "0", "0"])
    text = screens(output)
    assert "Main › My bots\n" in text
    assert "Main › My bots › @harrybot\n" in text
    assert "Main › My bots › @harrybot › Edit\n" in text
    assert "Main › My bots › @harrybot › Edit › Name\n" in text

    # 5 = clear, 1 = Hermes, 1 = tick, 5 = continue, 1 = for real, Enter, 0
    _code, _calls, output = run_menu([CLEAR, "1", "1", "5", "1", "", "0"])
    text = screens(output)
    assert "Main › Clear › Pick a forum group\n" in text
    assert "Main › Clear › Hermes › Tick what to clear\n" in text
    assert "Main › Clear › Hermes › Dry-run done\n" in text
    assert "Main › Clear › Hermes › Done\n" in text


def test_the_root_screen_keeps_the_tool_name_as_its_title():
    _code, _calls, output = run_menu(["0"])
    assert output[0].startswith("telegram-tools\n")


def test_run_menu_defaults_to_the_ui_reader_and_writer(monkeypatch):
    # Colour is applied by ui's reader/writer and nowhere else: the menu asks ui
    # for both only when the caller injects neither.
    answers = iter(["0"])
    seen = []
    monkeypatch.setattr(menu.ui, "reader", lambda: lambda _prompt: next(answers))
    monkeypatch.setattr(menu.ui, "writer", lambda: seen.append)

    code = asyncio.run(menu.run_menu(session=FakeSession(), runner=recorder()[1]))

    assert code == 0
    assert "0. Exit" in screens(seen)


# -- delete ---------------------------------------------------------------


def test_delete_flow_dry_runs_before_offering_execute():
    # Delete > a group or channel > Forum groups > Hermes > for real > exit
    _code, calls, _output = run_menu([DELETE, "1", "1", "1", "1", "0", "0"])

    assert [call.command for call in calls] == ["delete", "delete"]
    assert calls[0].execute is False
    assert calls[1].execute is True
    assert calls[1].delete_kind == "group"
    assert calls[1].chat == "-100111"


def test_delete_flow_knows_a_channel_from_a_group():
    # Delete > a group or channel > Channels > Alerts > for real > exit
    _code, calls, _output = run_menu([DELETE, "1", "2", "1", "1", "0", "0"])

    assert calls[1].delete_kind == "channel"
    assert calls[1].chat == "-100222"


def test_delete_flow_deletes_a_topic():
    # Delete > a topic > Hermes > Deploys > for real > exit
    _code, calls, _output = run_menu([DELETE, "2", "1", "1", "1", "0", "0"])

    assert calls[1].delete_kind == "topic"
    assert calls[1].topic == 141
    assert calls[1].chat == "-100111"


def test_delete_flow_backing_out_never_executes():
    _code, calls, _output = run_menu([DELETE, "1", "1", "1", "0", "0", "0", "0", "0"])

    executed = [call for call in calls if getattr(call, "execute", False)]
    assert executed == []


def test_delete_flow_says_the_title_prompt_is_on_the_next_screen():
    """The old label read as "type it here", and that is what a tester did."""
    _code, _calls, output = run_menu([DELETE, "1", "1", "1", "1", "0", "0"])

    assert "the next screen asks for its exact title" in screens(output)


def test_delete_flow_refuses_a_chat_create_could_not_make_back():
    # Direct chats > Mum: nothing this tool can create, so nothing it will delete.
    _code, calls, output = run_menu([DELETE, "1", "3", "1", "0", "0"])

    assert calls == []
    assert "does not delete" in screens(output)


def test_delete_flow_asks_which_kind_for_a_typed_reference():
    # A typed reference has not been looked up, so the menu asks instead of guessing.
    # Delete > group or channel > Type an ID (row 4) > @newchat > Channel > for real > exit
    _code, calls, _output = run_menu([DELETE, "1", "4", "@newchat", "2", "1", "0", "0"])

    assert calls[1].delete_kind == "channel"
    assert calls[1].chat == "@newchat"


# -- the nine-row root (spec section 14), landed on the profiles card ---------


def test_the_root_shows_all_nine_rows_including_the_two_a_later_version_fills():
    _code, _calls, output = run_menu(["0"])

    text = screens(output)
    # 6 and 7 hold their numbers now so that nothing above or below them moves
    # again when Manage and Watch arrive. Gate G6: this grouping, once.
    assert "6. Manage (admins, members, invites, settings)" in text
    assert "7. Watch (rules, runner, review queue)" in text


def test_manage_holds_the_five_administration_groups():
    _code, calls, output = run_menu([MANAGE, "0", "0"])

    text = screens(output)
    assert calls == []
    assert "1. Admins: list, promote, change rights, demote" in text
    assert "2. Members: list, ban, unban, mute, unmute, restrict" in text
    assert "3. Join requests: list, approve, decline" in text
    assert "4. Invite links: list, create, revoke" in text
    assert "5. Chat settings: show, set slow mode" in text


MANAGE_ADMINS = ("6", "1")
MANAGE_MEMBERS = ("6", "2")
MANAGE_JOIN = ("6", "3")
MANAGE_INVITES = ("6", "4")
MANAGE_SETTINGS = ("6", "5")


def test_admin_list_picks_a_chat_and_runs_with_no_form():
    # Admins (1) -> list (1) -> Forum groups (1) -> Hermes (1); Enter, 0.
    code, calls, _output = run_menu([MANAGE_ADMINS, "1", "1", "1", "", "0"])
    assert code == 0
    (args,) = calls
    assert (args.command, args.admin_kind, args.chat) == ("admin", "list", "-100111")


def test_admin_promote_stages_the_person_and_the_rights_and_never_sets_yes():
    # promote (2), Hermes, person (1), rights (2), rank (3), run (4).
    _code, calls, output = run_menu([MANAGE_ADMINS, "2", "1", "1", "1", "@harry", "2", "pin_messages", "3", "ops", "4", "", "0"])
    (args,) = calls
    assert (args.command, args.admin_kind, args.chat, args.user, args.rights, args.rank) == ("admin", "promote", "-100111", "@harry", "pin_messages", "ops")
    assert not hasattr(args, "yes")
    assert "Do it (shows the preview, then asks)" in screens(output)


def test_admin_promote_refuses_to_run_with_the_person_missing():
    _code, calls, output = run_menu([MANAGE_ADMINS, "2", "1", "1", "4", "0", "0", "0", "0", "0"])
    assert calls == []
    assert "Fill in first: Person (id or @username), Rights (comma-separated)." in screens(output)


def test_member_ban_dry_runs_first_and_the_label_is_typed_in_the_cli():
    # ban (2), Hermes, person (1), reason (2), run (3): the dry-run; then the one row for real; Enter, 0.
    code, calls, output = run_menu([MANAGE_MEMBERS, "2", "1", "1", "1", "@troll", "2", "spam", "3", "1", "", "0"])
    assert code == 0
    dry, real = calls
    assert (dry.command, dry.member_kind, dry.user, dry.reason, dry.execute) == ("member", "ban", "@troll", "spam", False)
    assert (real.user, real.reason, real.execute) == ("@troll", "spam", True)
    assert "Do it for real - the next screen asks for the person's exact label" in screens(output)


def test_member_ban_backing_out_after_the_dry_run_bans_nobody():
    _code, calls, _output = run_menu([MANAGE_MEMBERS, "2", "1", "1", "1", "@troll", "3", "0", "0", "0", "0", "0", "0"])
    assert [args.execute for args in calls] == [False]


def test_member_mute_needs_an_until_and_restrict_needs_rights_too():
    _code, calls, output = run_menu([MANAGE_MEMBERS, "4", "1", "1", "1", "@harry", "3", "0", "0", "0", "0", "0"])
    assert calls == []
    assert "Fill in first: Until (30m, 2h, 7d, 1w, or a date)." in screens(output)
    _code, calls, _output = run_menu([MANAGE_MEMBERS, "6", "1", "1", "1", "@harry", "2", "send_media", "3", "2h", "4", "", "0"])
    (args,) = calls
    assert (args.member_kind, args.rights, args.until) == ("restrict", "send_media", "2h")


def test_member_list_toggles_banned_and_keeps_the_default_limit():
    _code, calls, _output = run_menu([MANAGE_MEMBERS, "1", "1", "1", "3", "4", "", "0"])
    (args,) = calls
    assert (args.member_kind, args.banned, args.limit, args.query) == ("list", True, 200, None)


def test_join_requests_and_invites_and_settings_build_their_flags():
    _code, calls, _output = run_menu([MANAGE_JOIN, "2", "1", "1", "1", "@newbie", "2", "", "0"])
    (args,) = calls
    assert (args.command, args.join_kind, args.user) == ("join-requests", "approve", "@newbie")
    _code, calls, _output = run_menu([MANAGE_INVITES, "2", "1", "1", "1", "night", "2", "7d", "3", "5", "4", "5", "", "0"])
    (args,) = calls
    assert (args.command, args.invite_kind, args.title, args.expires, args.usage_limit, args.request_needed) == ("invite", "create", "night", "7d", 5, True)
    _code, calls, _output = run_menu([MANAGE_INVITES, "3", "1", "1", "1", "https://t.me/+abc", "2", "", "0"])
    (args,) = calls
    assert (args.invite_kind, args.link) == ("revoke", "https://t.me/+abc")
    _code, calls, _output = run_menu([MANAGE_SETTINGS, "2", "1", "1", "1", "60", "2", "", "0"])
    (args,) = calls
    assert (args.command, args.settings_kind, args.slow_mode) == ("settings", "set", 60)


WATCH_ROWS = (
    "1. Rules (list, add, edit, enable, disable, remove, test)",
    "2. Runner (run, status, stop, reload)",
    "3. Scheduled messages (list, post, cancel)",
    "4. Review queue (approve, accept, reject, retry, status)",
)
REVIEW_ROWS = (
    "1. The queue (what is waiting; fetches nothing)",
    "2. Approve downloads (pick, y/N, then the fetch runs into quarantine)",
    "3. Accept a quarantined download (shows the verdict, then y/N)",
    "4. Reject a candidate (deletes its quarantined bytes, after y/N)",
    "5. Retry a failed download (from where it stopped)",
    "6. Review status (counts, quarantine, scanner)",
)
REVIEW = ("7", "4")
REVIEW_LIST = (*REVIEW, "1")
REVIEW_APPROVE = (*REVIEW, "2")
REVIEW_ACCEPT = (*REVIEW, "3")
REVIEW_REJECT = (*REVIEW, "4")
REVIEW_RETRY = (*REVIEW, "5")
REVIEW_STATUS = (*REVIEW, "6")
RULES = ("7", "1")
RUNNER = ("7", "2")
SCHEDULED = ("7", "3")


def test_watch_lists_its_four_screens_and_runs_nothing_by_itself():
    _code, calls, output = run_menu([WATCH, "0", "0"])

    text = screens(output)
    assert calls == []
    assert "Main \u203a Watch\n" in text
    for row in WATCH_ROWS:
        assert row in text, row


def test_the_review_queue_keeps_its_six_rows_one_screen_deeper():
    _code, calls, output = run_menu([*REVIEW, "0", "0", "0"])

    text = screens(output)
    assert calls == []
    assert "Main \u203a Watch \u203a Review queue\n" in text
    for row in REVIEW_ROWS:
        assert row in text, row


def test_review_list_stages_a_kind_and_a_state_then_runs_offline():
    # 1 = kind, 2 = link; 2 = state, 1 = queued; 3 = show; Enter = menu; 0 0 = out
    code, calls, output = run_menu([REVIEW_LIST, "1", "2", "2", "1", "3", "", "0", "0"])
    assert code == 0
    (args,) = calls
    assert (args.command, args.review_kind, args.kind, args.state) == ("review", "list", "link", "queued")
    text = screens(output)
    assert "Kind   [link]" in text and "State  [queued]" in text
    assert "Main › Watch › Review queue › The queue › Kind\n" in text


def test_review_approve_passes_no_ids_and_no_answer_so_the_cli_asks():
    # The menu builds `review approve` with nothing filled in: the pick and the
    # y/N happen in the CLI on this terminal, exactly as a flag user sees them.
    code, calls, _output = run_menu([REVIEW_APPROVE, "", "0", "0"])
    assert code == 0
    (args,) = calls
    assert (args.command, args.review_kind, args.ids) == ("review", "approve", None)
    assert not getattr(args, "yes", False) and not getattr(args, "execute", False)


def test_accept_reject_and_retry_tick_candidates_from_the_queue_and_never_answer_for_you():
    session = FakeSession()
    session._review = [
        ("aaaa000000000001", "aaaa000000000001  link  quarantined  https://x.example/a  verdict=UNSCANNED"),
        ("aaaa000000000002", "aaaa000000000002  media  quarantined  report.bin  verdict=CLEAN"),
    ]
    # 1 = tick the first, 4 = Continue (two rows, Select all, Continue), Enter = menu
    code, calls, output = run_menu([REVIEW_ACCEPT, "1", "4", "", "0", "0"], session=session)
    assert code == 0
    (args,) = calls
    assert (args.command, args.review_kind, args.ids) == ("review", "accept", ["aaaa000000000001"])
    assert not getattr(args, "yes", False)
    assert session.review_states[-1] == ("quarantined",)
    assert "Main › Watch › Review queue › Accept\n" in screens(output)

    code, calls, _output = run_menu([REVIEW_REJECT, "2", "4", "", "0", "0"], session=session)
    (args,) = calls
    assert (args.command, args.review_kind, args.ids) == ("review", "reject", ["aaaa000000000002"])
    assert "queued" in session.review_states[-1] and "quarantined" in session.review_states[-1]

    session._review = [("aaaa000000000003", "aaaa000000000003  media  failed  report.bin")]
    # One row: 1 = tick it, 3 = Continue (Select all is 2)
    code, calls, _output = run_menu([REVIEW_RETRY, "1", "3", "", "0", "0"], session=session)
    (args,) = calls
    assert (args.command, args.review_kind, args.ids) == ("review", "retry", ["aaaa000000000003"])
    assert session.review_states[-1] == ("failed",)


def test_a_review_move_with_nothing_to_pick_says_so_and_comes_back():
    session = FakeSession()
    session._review = []
    _code, calls, output = run_menu([REVIEW_ACCEPT, "", "0", "0", "0"], session=session)
    assert calls == []
    assert "No candidate is quarantined." in screens(output)


def test_review_status_runs_offline_and_comes_straight_back():
    code, calls, _output = run_menu([REVIEW_STATUS, "", "0"])
    assert code == 0
    (args,) = calls
    assert (args.command, args.review_kind) == ("review", "status")


def test_build_holds_create_delete_and_the_structure_rows():
    _code, _calls, output = run_menu([BUILD, "0", "0"])

    text = screens(output)
    assert "1. Create a group, channel, or topic" in text
    assert "2. Delete a group, channel, or topic" in text
    assert "3. Export a chat's blueprint (topics and settings)" in text
    assert "4. Diff a blueprint against a chat" in text
    assert "5. Apply a blueprint (dry-run first, then its exact title)" in text
    assert "6. Show the remap table of an apply" in text


STRUCTURE_EXPORT = ("4", "3")
STRUCTURE_DIFF = ("4", "4")
STRUCTURE_APPLY = ("4", "5")
STRUCTURE_REMAP = ("4", "6")


def test_structure_export_picks_a_chat_and_takes_an_output_path():
    # Forum groups (1) -> Hermes (1), then a path, then Enter (main menu), 0 (exit).
    code, calls, _output = run_menu([STRUCTURE_EXPORT, "1", "1", "/tmp/hermes.json", "", "0"])
    assert code == 0
    (args,) = calls
    assert (args.command, args.structure_kind, args.chat, args.output) == ("structure", "export", "-100111", "/tmp/hermes.json")


def test_structure_export_with_a_blank_path_prints_the_blueprint():
    _code, calls, _output = run_menu([STRUCTURE_EXPORT, "1", "1", "", "", "0"])
    (args,) = calls
    assert args.output is None


def test_structure_diff_asks_for_the_file_then_the_chat():
    _code, calls, _output = run_menu([STRUCTURE_DIFF, "/tmp/hermes.json", "2", "1", "", "0"])
    (args,) = calls
    assert (args.command, args.structure_kind, args.blueprint, args.chat) == ("structure", "diff", "/tmp/hermes.json", "-100222")


def test_structure_apply_dry_runs_first_and_the_title_is_typed_in_the_cli():
    # File, existing chat (1), Forum groups (1) -> Hermes (1): the dry-run runs;
    # then the one row applies for real, Enter, 0.
    code, calls, output = run_menu([STRUCTURE_APPLY, "/tmp/hermes.json", "1", "1", "1", "1", "", "0"])
    assert code == 0
    dry, real = calls
    assert (dry.structure_kind, dry.chat, dry.create, dry.execute) == ("apply", "-100111", False, False)
    assert (real.chat, real.create, real.execute) == ("-100111", False, True)
    assert "Apply it for real - the next screen asks for the chat's exact title" in screens(output)


def test_structure_apply_to_a_new_chat_sets_create():
    _code, calls, _output = run_menu([STRUCTURE_APPLY, "/tmp/hermes.json", "2", "1", "", "0"])
    dry, real = calls
    assert (dry.create, dry.chat, dry.execute) == (True, None, False)
    assert (real.create, real.execute) == (True, True)


def test_structure_apply_backing_out_after_the_dry_run_applies_nothing():
    _code, calls, _output = run_menu([STRUCTURE_APPLY, "/tmp/hermes.json", "2", "0", "", "0", "0"])
    assert [args.execute for args in calls] == [False]


def test_structure_remap_picks_an_apply_from_the_archive_and_runs_offline():
    code, calls, output = run_menu([STRUCTURE_REMAP, "1", "", "0"])
    assert code == 0
    (args,) = calls
    assert (args.command, args.structure_kind, args.apply_id) == ("structure", "remap", "a1b2c3d4e5f60718")
    assert "a1b2c3d4e5f60718  2026-09-06T10:00:00Z  blueprint 0123456789abcdef" in screens(output)


def test_structure_remap_can_take_a_typed_apply_id():
    _code, calls, _output = run_menu([STRUCTURE_REMAP, "2", "feedfacefeedface", "", "0"])
    (args,) = calls
    assert args.apply_id == "feedfacefeedface"


def test_structure_remap_with_no_applies_asks_for_an_id():
    session = FakeSession()
    session._applies = []
    _code, calls, _output = run_menu([STRUCTURE_REMAP, "feedfacefeedface", "", "0"], session=session)
    (args,) = calls
    assert args.apply_id == "feedfacefeedface"


def test_identity_lists_the_profile_rows_and_my_bots():
    _code, _calls, output = run_menu([IDENTITY, "0", "0"])

    text = screens(output)
    assert "1. Profiles on this machine" in text
    assert "2. Log in (phone and code)" in text
    assert "3. Log in by scanning a QR code" in text
    assert "4. Log out" in text
    assert "5. Move the pre-profile session into a profile" in text
    assert "6. My bots" in text


def test_identity_runs_profiles_without_the_menus_connection():
    session = FakeSession()

    _code, calls, _output = run_menu([PROFILES, "", "0"], session=session)

    assert calls[0].command == "profiles"
    # It reads the store and opens nothing, so the menu keeps its connection --
    # and with it the identity line every screen after this one still carries.
    assert session.released == 0
    assert session.banner is not None


def test_identity_hands_the_session_file_back_before_a_login():
    # `auth` opens its own client on the file the menu is holding, and one
    # session file is one connection. Afterwards the caches go too: a login can
    # change which account this is.
    session = FakeSession()

    _code, calls, _output = run_menu([LOG_IN, "", "0"], session=session)

    assert calls[0].command == "auth"
    assert session.released == 2
    assert session.banner is None


def test_identity_log_in_asks_for_neither_qr_nor_logout():
    _code, calls, _output = run_menu([LOG_IN, "", "0"])

    args = calls[0]
    assert args.command == "auth"
    assert (args.qr, args.logout, args.migrate) == (False, False, False)


def test_identity_qr_row_sets_the_qr_flag():
    _code, calls, _output = run_menu([LOG_IN_QR, "", "0"])

    assert (calls[0].qr, calls[0].logout, calls[0].migrate) == (True, False, False)


def test_identity_log_out_row_sets_the_logout_flag():
    _code, calls, _output = run_menu([LOG_OUT, "", "0"])

    assert (calls[0].qr, calls[0].logout, calls[0].migrate) == (False, True, False)


def test_identity_migrate_row_sets_the_migrate_flag():
    _code, calls, _output = run_menu([MIGRATE, "", "0"])

    assert (calls[0].qr, calls[0].logout, calls[0].migrate) == (False, False, True)


def test_the_menu_never_asks_auth_to_skip_a_gate():
    # Section 5.2 and the repo's own rule: the menu is not a shorter path past a
    # gate. `auth` has no --yes, and nothing the menu builds may invent one.
    for row in (LOG_IN, LOG_IN_QR, LOG_OUT, MIGRATE):
        _code, calls, _output = run_menu([row, "", "0"])
        assert not getattr(calls[0], "yes", False), row
        assert not getattr(calls[0], "execute", False), row


# -- the banner (section 5.1, gate answer 2a) --------------------------------


def test_every_screen_carries_the_acting_identity_once_there_is_a_connection():
    _code, _calls, output = run_menu([DISCOVER, "0", "0"])

    for screen in output:
        if "\n" + menu.RULE in screen:
            lines = screen.split("\n")
            assert lines[1] == "Acting as: Sven (@sven) · account", screen


def test_the_root_shows_no_identity_before_anything_has_connected():
    # A bare `telegram-tools` opens without credentials, and there is nothing to
    # name until something logs in. That is answer 2a to the card's gate.
    session = FakeSession()
    session.banner = None

    _code, _calls, output = run_menu(["0"], session=session)

    assert "Acting as:" not in screens(output)
    assert screens(output).split("\n")[1] == menu.RULE


def test_the_banner_goes_between_the_title_and_the_rule():
    session = FakeSession()

    _code, _calls, output = run_menu([DISCOVER, "0", "0"], session=session)

    first = output[0].split("\n")
    assert first[0] == "telegram-tools"
    assert first[1].startswith("Acting as: ")
    assert first[2] == menu.RULE


def test_a_command_s_own_output_is_not_treated_as_a_screen():
    # Only a title over the rule gets the line; anything a command printed goes
    # through untouched, rule of its own included.
    plain = "chat  -100111\n" + menu.RULE + "\nmore output"

    assert menu._screen_with_banner(plain, "Acting as: X · account") == plain


# -- the two laws the regroup must not break ---------------------------------

MENU_SOURCE = (Path(menu.__file__)).read_text(encoding="utf-8")

# A flag the menu deliberately has no row for, and why. Section 14: "every flag
# has a row" -- these are the named exceptions to it, not an escape hatch.
NO_ROW = {
    # Output modes belong to a script, not to a person at a menu.
    "json_envelope": "the envelope is for --json callers; the menu is the human path",
    "jsonl": "same",
    # A global that selects the login before the menu opens; `run_menu` takes it.
    "profile": "chosen before the menu starts, and named on every screen instead",
    # The menu is one account session; a bot is a second connection with two
    # rows' worth of reach (send, create topic). Bot mode is a flag for a
    # command, and a bare `--as-bot` is refused rather than given a menu.
    "as_bot": "bot mode runs one command on a client of its own; the menu is the account's session",
    # The whole point of the menu's safety story.
    "yes": "the menu never skips a confirm; that is the gate rule",
    "help": "argparse's own",
}


def _flag_dests():
    """Every flag every subcommand defines, by the attribute it sets."""
    from telegram_tools.cli import build_parser

    parser = build_parser()
    found: dict[str, str] = {}

    def walk(p, trail):
        for action in p._actions:
            if action.dest in ("==SUPPRESS==",):
                continue
            if action.choices and hasattr(action, "_name_parser_map"):
                for name, sub in action._name_parser_map.items():
                    walk(sub, f"{trail} {name}".strip())
                continue
            found.setdefault(action.dest, trail or "(root)")

    walk(parser, "")
    return found


def test_every_flag_has_a_row_in_the_menu_or_a_named_reason_not_to():
    """Section 14's rule, checked against the parser rather than remembered.

    A dest the menu never names is a flag no menu row can reach, which is how a
    capability quietly becomes flags-only. The exceptions are listed above with
    their reasons; anything else fails here.
    """
    missing = []
    for dest, where in sorted(_flag_dests().items()):
        if dest in NO_ROW or dest == "command":
            continue
        if dest not in MENU_SOURCE:
            missing.append(f"{dest} (from `{where}`)")

    assert not missing, (
        "the menu reaches no row for: " + ", ".join(missing) + ". Section 14: every flag has a row. "
        "A flag that deliberately has none belongs in NO_ROW with its reason."
    )


def test_the_menu_never_builds_a_skipped_confirm():
    """The gate rule, as a property of the source rather than of one journey.

    `--yes` skips a preview; the menu is not allowed to be the shorter path past
    one. Every namespace it builds leaves `yes` False, and the typed gates
    (`DELETE`, an exact title, a profile name) are answered in the CLI, on the
    same prompt a flag user sees.
    """
    assert "yes=True" not in MENU_SOURCE
    assert "yes = True" not in MENU_SOURCE


# --- the archive rows under Read ---------------------------------------------


def test_read_lists_the_live_search_and_every_archive_row():
    _code, _calls, output = run_menu([READ, "0", "0"])
    text = screens(output)
    assert "Main › Read\n" in text
    for row in (
        "1. Search live (asks Telegram)",
        "2. Sync the archive (everything, or one chat or topic)",
        "3. Archive status",
        "4. Search or export the archive",
        "5. Prune old rows (retention)",
        "6. Forget a scope or identity",
    ):
        assert row in text, row


def test_archive_sync_stages_a_scope_a_floor_and_start_over_then_runs():
    # 2 2 = read > sync, 1 = scope, 1 = forum groups, 1 = Hermes, 1 = Deploys, 2 = since,
    # a date, 3 = start over (toggles), 4 = sync now, Enter = menu, 0 = read back, 0 = exit
    answers = [ARCHIVE_SYNC, "1", "1", "1", "1", "2", "2026-09-01", "3", "4", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0
    args = calls[0]
    assert args.command == "archive" and args.archive_kind == "sync"
    assert args.scope == ["tg:topic:-100111:141"]
    assert args.since == "2026-09-01"
    assert args.full is True
    text = screens(output)
    assert "Main › Read › Sync the archive\n" in text
    assert "Start over     [yes]" in text
    assert "Main › Read › Sync the archive › Chat or topic › Pick a chat\n" in text, "a sync picks from the live chats, not the archive"
    assert "Main › Read › Sync the archive › Chat or topic › Hermes › Topic\n" in text
    assert "1. Chat or topic  [(every chat this account can read; press 1 to pick one)]" in text


def test_archive_sync_scope_takes_a_whole_forum_a_channel_or_is_cleared_again():
    # A forum, every topic: 1 = scope, 1 = forum groups, 1 = Hermes, 3 = every topic (two topics, then the extra)
    code, calls, _output = run_menu([ARCHIVE_SYNC, "1", "1", "1", "3", "4", "", "0"])
    assert code == 0 and calls[0].scope == ["tg:chat:-100111"]
    # A channel has no topic step: 2 = channels, 1 = Alerts
    code, calls, _output = run_menu([ARCHIVE_SYNC, "1", "2", "1", "4", "", "0"])
    assert code == 0 and calls[0].scope == ["tg:chat:-100222"]
    # Set, then cleared back to every chat: 1 = scope again, 2 = clear
    code, calls, _output = run_menu([ARCHIVE_SYNC, "1", "2", "1", "1", "2", "4", "", "0"])
    assert code == 0 and calls[0].scope is None


def test_archive_sync_with_nothing_staged_syncs_everything():
    code, calls, _output = run_menu([ARCHIVE_SYNC, "4", "", "0"])
    assert code == 0
    assert calls[0].scope is None and calls[0].since is None and calls[0].full is False


def test_the_archive_scope_picker_takes_a_typed_rid_and_says_when_the_archive_is_empty():
    # The archive's own picker, on retention: 3 = type a rid (two scopes, then the extra)
    code, calls, _output = run_menu([ARCHIVE_RETENTION, "3", "tg:chat:-100999", "90d", "0", "0", "0", "0"])
    assert code == 0 and calls[0].scope == "tg:chat:-100999"

    code, calls, output = run_menu([ARCHIVE_RETENTION, "1", "tg:chat:-100999", "90d", "0", "0", "0", "0"], session=FakeSession(scopes=[]))
    assert code == 0 and calls[0].scope == "tg:chat:-100999"
    assert "The archive holds no scopes yet." in screens(output)


def test_archive_status_runs_without_the_menus_connection():
    session = FakeSession()
    calls = []

    async def runner(args, *, client=None, config=None):
        calls.append((args, client))
        return 0

    # 2 3 = read > status, blank = every identity, Enter = back, 0 = read back, 0 = exit
    code, _unused, _output = run_menu([ARCHIVE_STATUS, "", "", "0"], session=session, runner=runner)
    assert code == 0
    args, client = calls[0]
    assert args.command == "archive" and args.archive_kind == "status" and args.identity is None
    assert client is None, "status reads the file; the menu's connection is not opened for it"


def test_archive_search_stages_every_field_then_searches_and_exports():
    answers = [
        ARCHIVE_QUERY,
        "1", "deploy",            # query
        "2", "\\d+",             # regex
        "3", "2",                 # scope: pick Alerts
        "4", "tg:user:4242",      # identity
        "5", "tg:user:777",       # from
        "6", "2026-08-01",        # since
        "7", "2026-09-01",        # until
        "8", "2",                 # context
        "9", "5",                 # limit
        "10",                     # search (print here)
        "2",                      # tweak it
        "11", "deploys", "5",     # export: file name, then HTML
        "",                       # Enter = main menu
        "0",                      # exit
    ]
    code, calls, output = run_menu(answers)
    assert code == 0
    searched, exported = calls
    assert searched.command == "archive" and searched.archive_kind == "search"
    assert searched.query == "deploy" and searched.regex == "\\d+"
    assert searched.scope == ["tg:chat:-100222"] and searched.identity == "tg:user:4242"
    assert searched.author == "tg:user:777" and searched.since == "2026-08-01" and searched.until == "2026-09-01"
    assert searched.context == 2 and searched.limit == 5
    assert exported.archive_kind == "export" and exported.format == "html" and exported.output == "deploys"
    assert exported.query == "deploy" and exported.scope == ["tg:chat:-100222"]
    assert "Main › Read › Search the archive\n" in screens(output)


def test_archive_search_refuses_to_run_without_a_query():
    code, calls, output = run_menu([ARCHIVE_QUERY, "10", "0", "0", "0"])
    assert code == 0 and calls == []
    assert "Type a query first." in screens(output)


def test_archive_retention_dry_runs_first_then_asks_before_the_real_pass():
    # 2 5 = read > prune, 1 = Deploys, keep, then 1 = for real, Enter, 0, 0
    code, calls, output = run_menu([ARCHIVE_RETENTION, "1", "90d", "1", "", "0"])
    assert code == 0
    dry_run, for_real = calls
    assert dry_run.archive_kind == "retention" and dry_run.scope == "tg:topic:-100111:141" and dry_run.keep == "90d"
    assert dry_run.execute is False and for_real.execute is True
    assert "Main › Read › Prune the archive › Dry-run done\n" in screens(output)

    # Backing out at the dry-run screen never reaches the real pass.
    code, calls, _output = run_menu([ARCHIVE_RETENTION, "1", "90d", "0", "0", "0", "0"])
    assert code == 0 and [args.execute for args in calls] == [False]


def test_archive_forget_a_scope_or_an_identity_dry_runs_first():
    code, calls, _output = run_menu([ARCHIVE_FORGET, "1", "2", "1", "", "0"])
    assert code == 0
    dry_run, for_real = calls
    assert dry_run.archive_kind == "forget" and dry_run.scope == "tg:chat:-100222" and dry_run.identity is None
    assert dry_run.execute is False and for_real.execute is True

    code, calls, _output = run_menu([ARCHIVE_FORGET, "2", "tg:user:4242", "1", "", "0"])
    assert code == 0
    assert calls[0].scope is None and calls[0].identity == "tg:user:4242"
    assert [args.execute for args in calls] == [False, True]


def test_archive_prunes_stop_at_a_failed_dry_run():
    calls, runner = recorder(error=ValueError("not a scope in this archive"))
    code, _unused, output = run_menu([ARCHIVE_FORGET, "1", "1", "", "0"], runner=runner)
    assert code == 0
    assert len(calls) == 1 and calls[0].execute is False
    assert "error: not a scope in this archive" in screens(output)


def test_the_live_search_export_offers_all_five_formats():
    # 2 1 = search live, 1, 1 = Hermes, 8 = export, path, 4 = Markdown, Enter, 0, 0
    code, calls, output = run_menu([SEARCH, "1", "1", "8", "out.md", "4", "", "0"])
    assert code == 0 and calls[0].format == "markdown" and calls[0].output == "out.md"
    text = screens(output)
    for row in ("1. JSON", "2. CSV", "3. JSON lines (one record per line)", "4. Markdown", "5. HTML (one self-contained page)"):
        assert row in text, row


def test_main_menu_after_an_action_is_the_root_not_the_group_screen():
    """Sven's try-it on 2026-09-06: Main menu from a search under Read landed on Read."""
    reader_ran_out = object()

    def journey(answers):
        output = []
        code, calls, out = run_menu(answers, output=output)
        return code, out

    # Read: search, Enter = main menu, 0 = exit. A group screen in between would need one more 0.
    _code, output = journey([SEARCH, "1", "1", "7", "", "0"])
    text = screens(output)
    assert text.count("Main › Read\n") == 1, "the Read screen is drawn once, on the way in, never on the way out"
    assert text.rstrip().endswith("0. Exit\nChoose: ") or "telegram-tools\n" in output[-1], "the last screen is the root"

    # Build: create a topic, Enter, 0.
    _code, output = journey([CREATE, "4", "1", "1", "Deploys", "", "0"])
    assert screens(output).count("Main › Build\n") == 1

    # Identity: profiles, Enter, 0.
    _code, output = journey([PROFILES, "", "0"])
    assert screens(output).count("Main › Identity\n") == 1

    # And 0 on the after-run screen still exits outright.
    code, output = journey([SEARCH, "1", "1", "7", "0"])
    assert code == 0 and "Main › Read › Search › Hermes › Done" in screens(output)


def test_a_date_prompt_takes_european_and_iso_and_asks_again_for_anything_else():
    # Sync: 2 = since, a European date; then run. The row shows ISO and the run gets ISO.
    code, calls, output = run_menu([ARCHIVE_SYNC, "2", "05/09/2026", "4", "", "0"])
    assert code == 0 and calls[0].since == "2026-09-05"
    assert "Since          [2026-09-05]" in screens(output)

    # Nonsense is asked again, with the hint, until a date or a blank.
    code, calls, output = run_menu([ARCHIVE_SYNC, "2", "yesterday", "2026-09-05T10:00", "4", "", "0"])
    assert code == 0 and calls[0].since == "2026-09-05T10:00"
    assert "Dates are YYYY-MM-DD" in screens(output)

    # The live search's Since and Until, and the archive query's, go through the same prompt.
    code, calls, _output = run_menu([SEARCH, "1", "1", "4", "1/2/2026", "5", "31/12/2026", "7", "", "0"])
    assert code == 0 and (calls[0].since, calls[0].until) == ("2026-02-01", "2026-12-31")
    code, calls, _output = run_menu([ARCHIVE_QUERY, "1", "deploy", "6", "05/09/2026", "10", "", "0"])
    assert code == 0 and calls[0].since == "2026-09-05"


# --- the message rows under Write --------------------------------------------

WRITE = "3"
REPLY = ("3", "2")
EDIT_MSG = ("3", "3")
DELETE_MSGS = ("3", "4")
FORWARD = ("3", "5")
REACT = ("3", "7")
POLL = ("3", "11")
MARK_READ = ("3", "13")


def test_write_lists_send_and_every_message_verb():
    _code, _calls, output = run_menu([WRITE, "0", "0"])
    text = screens(output)
    assert "Main › Write\n" in text
    for row in (
        "1. Send a message",
        "2. Reply to a message",
        "3. Edit a message",
        "4. Delete messages (dry-run first)",
        "5. Forward messages",
        "6. Copy messages (text and links, never the bytes)",
        "7. React to a message",
        "8. Remove a reaction",
        "9. Pin a message",
        "10. Unpin a message",
        "11. Post a poll",
        "12. Show typing",
        "13. Mark a chat read",
        "14. Mark a chat unread",
        "15. Bookmark a message (Saved Messages)",
        "16. Save a draft",
    ):
        assert row in text, row


def test_reply_stages_the_message_and_the_text_then_runs_without_yes():
    # 3 2 = write > reply, 1 = forum groups, 1 = Hermes, 1 = Reply to message, 4812,
    # 2 = Reply, body, ., 3 = Do it, Enter = menu, 0 = exit
    answers = [REPLY, "1", "1", "1", "4812", "2", "on it", ".", "3", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert (args.command, args.message_verb, args.chat, args.message_id, args.text, args.yes) == (
        "message", "reply", "-100111", 4812, "on it", False
    )
    text = screens(output)
    assert "Main › Write › Reply › Hermes\n" in text
    assert "Reply to message [4812]" in text
    assert "3. Do it (shows the preview, then asks)" in text


def test_reply_refuses_to_run_with_nothing_staged():
    answers = [REPLY, "1", "1", "3", "0", "0", "0", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0 and calls == []
    assert "Fill in first: Reply to message, Reply." in screens(output)


def test_delete_messages_dry_runs_unless_the_person_toggles_delete_for_real():
    # 1 = Message ids, "10, 11", 6 = Run it (dry-run)
    answers = [DELETE_MSGS, "1", "1", "1", "10, 11", "6", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0
    args = calls[0]
    assert (args.message_verb, args.ids, args.execute, args.from_search) == ("delete", ["10,11"], False, None)
    assert not hasattr(args, "yes") or args.yes is False
    text = screens(output)
    assert "Message ids  [10, 11]" in text
    assert "5. Delete for real (asks you to type DELETE) [no]" in text
    assert "6. Run it (dry-run unless 'Delete for real' is on)" in text

    # 2 = Archive query, 5 = toggle Delete for real, 6 = run: execute is on, the
    # DELETE prompt is the CLI's, and yes is still never set.
    answers = [DELETE_MSGS, "1", "1", "2", "deploy AND red", "5", "6", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0
    args = calls[0]
    assert (args.from_search, args.execute, args.ids) == ("deploy AND red", True, None)
    assert "[yes]" in screens(output)


def test_delete_messages_needs_ids_or_a_query():
    answers = [DELETE_MSGS, "1", "1", "6", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)
    assert calls == []
    assert "Fill in first: Message ids or an archive query." in screens(output)


def test_forward_picks_a_destination_chat_and_a_topic_there():
    # 1 = ids, "11", 5 = Send them to > 2 = Channels > 1 = Alerts, 6 = Topic there, 141, 7 = Do it
    answers = [FORWARD, "1", "1", "1", "11", "5", "2", "1", "6", "141", "7", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0
    args = calls[0]
    assert (args.message_verb, args.ids, args.to_chat, args.to_topic, args.limit, args.i_know) == (
        "forward", ["11"], "-100222", 141, None, False
    )
    assert "Send them to [Alerts]" in screens(output)


def test_react_stages_an_emoji_and_a_poll_stages_its_answers_and_topic():
    answers = [REACT, "1", "1", "1", "11", "2", "🔥", "3", "", "0"]
    code, calls, _output = run_menu(answers)
    assert code == 0 and (calls[0].message_verb, calls[0].message_id, calls[0].emoji) == ("react", 11, "🔥")

    # 1 = Topic > 1 = Deploys, 2 = Question, 3 = Answers (lines), 4 = toggle several, 5 = Do it
    answers = [POLL, "1", "1", "1", "1", "2", "Ship it?", "3", "yes", "no", ".", "4", "5", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0
    args = calls[0]
    assert (args.message_verb, args.topic, args.question, args.options, args.multiple) == ("poll", 141, "Ship it?", ["yes", "no"], True)
    assert "Answers      [yes / no]" in screens(output)


def test_mark_read_needs_only_the_chat():
    answers = [MARK_READ, "2", "1", "1", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0
    assert (calls[0].message_verb, calls[0].chat) == ("read", "-100222")
    assert "1. Do it (shows the preview, then asks)" in screens(output)


def test_send_stages_a_reply_to_and_can_clear_it_again():
    # 4 = Reply to, 4812; then 4 again offers keep/change/clear, 3 = clear; 2 = message, 5 = send
    answers = [SEND, "1", "1", "4", "4812", "2", "hi", ".", "5", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0 and calls[0].reply_to == 4812
    assert "Reply to  [4812]" in screens(output)

    answers = [SEND, "1", "1", "4", "4812", "4", "3", "2", "hi", ".", "5", "", "0"]
    code, calls, output = run_menu(answers)
    assert code == 0 and calls[0].reply_to is None
    assert "Reply to  [(nothing - a new message)]" in screens(output)


def test_main_menu_after_a_message_verb_lands_on_the_root():
    answers = [MARK_READ, "2", "1", "1", "", "0"]
    _code, _calls, output = run_menu(answers)
    text = screens(output)
    assert text.count("Main › Write\n") == 1, "the group screen is drawn once, on the way in"
