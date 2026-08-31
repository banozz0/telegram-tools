import asyncio

from telegram_tools import menu
from telegram_tools.columns import width
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


def test_root_menu_lists_every_command_and_exits_on_zero():
    code, _calls, output = run_menu(["0"])

    text = screens(output)
    assert code == 0
    assert "1. Chats & topics (find IDs)" in text
    assert "2. Search / export messages" in text
    assert "3. Send a message" in text
    assert "4. Create a group, channel, or topic" in text
    assert "5. Clear topic messages" in text
    assert "6. My bots" in text
    assert "7. Check setup" in text
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

    code, _unused, _output = run_menu(["7", "", "0"], runner=runner)

    assert code == 0
    assert calls[0][0].command == "doctor"
    assert calls[0][1] is None
    assert calls[0][2] is None


def test_discover_builds_the_managed_chats_namespace_and_loops():
    # 1 = chats & topics, 1 = chats I manage, 1 = print here, Enter = menu, 0 = exit
    code, calls, _output = run_menu(["1", "1", "1", "", "0"])

    assert code == 0
    assert len(calls) == 1
    assert calls[0].command == "discover"
    assert calls[0].all_chats is False
    assert not hasattr(calls[0], "admin_only")
    assert calls[0].json_output is None


def test_discover_all_chats_to_a_json_file():
    code, calls, _output = run_menu(["1", "2", "2", "/tmp/out.json", "0"])

    assert code == 0
    assert calls[0].all_chats is True
    assert calls[0].json_output == "/tmp/out.json"


def test_zero_at_the_first_flow_screen_returns_to_the_root_menu():
    code, calls, output = run_menu(["1", "0", "0"])

    assert code == 0
    assert calls == []
    assert screens(output).count("1. Chats & topics (find IDs)") == 2


def test_discover_zero_at_where_screen_returns_to_scope_not_root():
    # 1 = discover, 1 = chats I manage, 0 = "where" back -> scope screen again,
    # 2 = every chat this time, 1 = print here, Enter = menu, 0 = exit
    answers = ["1", "1", "0", "2", "1", "", "0"]
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
    answers = ["1", "1", "2", "", "1", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].json_output is None
    assert screens(output).count("Where should it go?") == 2


def test_two_actions_in_one_session():
    code, calls, _output = run_menu(["7", "", "7", "", "0"])

    assert code == 0
    assert [call.command for call in calls] == ["doctor", "doctor"]


def test_an_action_error_prints_and_returns_to_the_menu():
    _calls, runner = recorder(error=ValueError("Cannot resolve chat 'nope'."))
    code, _unused, output = run_menu(["7", "", "0"], runner=runner)

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
    assert "Failed" in screens(output)
    assert screens(output).count("1. Chats & topics (find IDs)") == 2


def test_zero_after_an_action_exits():
    code, calls, _output = run_menu(["7", "0"])

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
    # The two titles draw the same width and len() calls them different, so a
    # len()-padded label puts one ID a column left of the other.
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
        "2", "1", "1",
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


def test_search_topic_picker_says_the_chat_has_no_topics():
    session = FakeSession(topics=[])
    # 2 = search, 1 = forum groups, 1 = Hermes, 1 = Topic row (no topics), 0 = back to
    # the staging screen, 0 = back to the chat picker, 0 = back out of the picker to
    # root, 0 = exit
    answers = ["2", "1", "1", "1", "0", "0", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls == []
    text = screens(output)
    assert "That chat has no topics." in text
    # It returned to the staging screen rather than crashing or exiting: the
    # screen renders once before the Topic row is chosen, once again after.
    assert text.count("Main › Search › Hermes\n") == 2


def test_search_zero_at_staging_returns_to_the_chat_picker_not_root():
    # 2 = search, 1 = forum groups, 1 = Hermes, 0 = staging back -> chat picker,
    # 4 = type an ID/username this time, a new chat, 7 = run it (topic row is
    # shown since the typed chat's forum-ness is unknown), Enter, 0 = exit
    answers = ["2", "1", "1", "0", "4", "@newchat", "7", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    # Proves the chat picker ran again (not root): "@newchat" only ends up as
    # the search target if 0 at the staging screen landed on the chat picker.
    assert calls[0].command == "search"
    assert calls[0].chat == "@newchat"


def test_search_staging_back_discards_and_says_so():
    # 2 = search, 1 = forum groups, 1 = Hermes, 2 = Contains (unset -> straight to
    # the value prompt), "deploy" = the value, 0 = staging back -> asks first,
    # 0 = discard (says so) -> chat picker, 0 = chat picker back, 0 = exit.
    answers = ["2", "1", "1", "2", "deploy", "0", "0", "0", "0"]
    code, calls, output = run_menu(answers)

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "0. Back (discards)" in text
    assert "Main › Search › Hermes › 1 staged change\n" in text
    assert "Discarded 1 staged change." in text


def test_clear_dry_runs_first_then_executes():
    # 5 = clear, 1 = Hermes, 1 = tick Deploys, 5 = continue (3 is All topics,
    # 4 Select all), 1 = for real, Enter = main menu, 0 = exit
    answers = ["5", "1", "1", "5", "1", "", "0"]
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
    answers = ["5", "1", "1", "5", "0", "0", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert len(calls) == 1
    assert calls[0].execute is False


def test_clear_zero_at_dry_run_returns_to_the_ticker_with_ticks_preserved():
    # 5 = clear, 1 = Hermes, 1 = tick Deploys, 5 = continue, dry-run runs, 0 = back
    # to the ticker (Deploys should still be ticked), 5 = continue again with no
    # further ticking, 1 = for real, Enter, 0 = exit
    answers = ["5", "1", "1", "5", "0", "5", "1", "", "0"]
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
    answers = ["5", "1", "1 2", "5", "0", "0", "0", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert len(calls) == 1
    assert calls[0].command == "clear-messages"
    assert calls[0].all_topics is True
    assert calls[0].topics is None
    assert calls[0].execute is False


def test_clear_every_topic_uses_all_topics():
    # 3 = select all (two topics + select all), 4 = continue
    answers = ["5", "1", "3", "4", "1", "", "0"]
    code, calls, _output = run_menu(answers)

    assert calls[0].all_topics is True
    assert calls[0].topics is None
    assert calls[1].all_topics is True


def test_clear_does_not_offer_the_real_pass_when_the_dry_run_errors():
    _calls, runner = recorder(error=PermissionError("Current user lacks Telegram delete_messages permission in this chat."))
    answers = ["5", "1", "1", "5", "0"]
    code, _unused, output = run_menu(answers, runner=runner)

    assert code == 0
    text = screens(output)
    assert "error: Current user lacks Telegram delete_messages permission in this chat." in text
    assert "Clear them for real" not in text


def test_clear_says_when_a_chat_has_no_topics():
    session = FakeSession(topics=[])
    # 5 = clear, 1 = Hermes has no topics -> back to the forum picker (one screen
    # back, not the root), 0 = out of the picker, 0 = exit
    answers = ["5", "1", "0", "0"]
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
    code, calls, output = run_menu(["5", "2", "-100999", "0", "0"], session=session)

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "Type an ID or @username" in text
    assert "That chat has no topics." in text


def test_bots_lists_and_prints_a_profile():
    # 6 = my bots, 1 = harrybot, 0 out of the bot screen to the list, 0 = root, 0 = exit
    code, calls, output = run_menu(["6", "1", "0", "0", "0"])

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
    code, calls, output = run_menu(["6", "1", "0", "0", "0"], session=session)

    text = screens(output)
    assert code == 0
    assert calls == []
    assert "1. (no username)  Nameless" in text
    assert "bot 99999" in text
    assert "@99999" not in text


def test_bots_saves_a_profile_to_json():
    code, calls, _output = run_menu(["6", "1", "2", "/tmp/bot.json", "0"])

    assert calls[0].command == "bots"
    assert calls[0].bot == "12345"
    assert calls[0].json_output == "/tmp/bot.json"


def test_bot_edit_stages_a_name_and_applies_without_yes():
    # 4, 1 = bot, 1 = edit, 1 = Name, 2 = change, text, 8 = review & apply
    answers = ["6", "1", "1", "1", "2", "Harry Two", "8", "", "0"]
    code, calls, _output = run_menu(answers)

    args = calls[0]
    assert args.command == "bots"
    assert args.bot == "12345"
    assert args.name == "Harry Two"
    assert args.bio is None
    assert args.yes is False


def test_bot_edit_clears_a_bio_with_an_empty_string():
    answers = ["6", "1", "1", "2", "3", "8", "", "0"]
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
    answers = ["6", "1", "1", "2", "hello", "8", "", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls[0].bio == "hello"
    assert "Keep it as (not set)" not in screens(output)


def test_bot_edit_shows_current_values_and_staged_changes():
    # ... 0 = field list back -> asks first, 0 = discard (to the bot's own screen),
    # 0 = the list, 0 = root, 0 = exit
    answers = ["6", "1", "1", "1", "2", "Harry Two", "0", "0", "0", "0", "0"]
    _code, _calls, output = run_menu(answers)

    text = screens(output)
    # The bracketed value, not the column padding.
    assert "[Harry]" in text
    assert "[Harry -> Harry Two]" in text
    assert "Discarded 1 staged change." in text


def test_bot_edit_staging_the_name_none_is_not_shown_as_cleared():
    # "none" is the sentinel for clearing rights, not an ordinary staged value.
    # Typed as a *name* it must render as the literal value, not "(cleared)".
    answers = ["6", "1", "1", "1", "2", "none", "0", "0", "0", "0", "0"]
    _code, _calls, output = run_menu(answers)

    text = screens(output)
    assert "[Harry -> none]" in text
    assert "(cleared)" not in text


def test_bot_edit_refuses_token_fields_without_a_token():
    # ... 0 = field list back (to the bot's own screen), 0 = the list, 0 = root, 0 = exit
    answers = ["6", "1", "1", "4", "0", "0", "0", "0"]
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
    answers = ["6", "1", "1", "6", "2", "1", "18", "8", "", "0"]
    _code, calls, _output = run_menu(answers, session=session)

    assert calls[0].group_rights == "change_info,post_messages"


def test_bot_edit_clears_rights_with_none():
    session = FakeSession(bot_tokens={"harry": "12345:AAtoken"})
    answers = ["6", "1", "1", "6", "3", "8", "", "0"]
    _code, calls, _output = run_menu(answers, session=session)

    assert calls[0].group_rights == "none"


def test_bot_edit_apply_with_nothing_staged_says_so():
    # ... 0 = field list back (to the bot's own screen), 0 = the list, 0 = root, 0 = exit
    answers = ["6", "1", "1", "8", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)

    assert calls == []
    assert "Nothing staged yet." in screens(output)


def test_bot_edit_zero_at_field_list_returns_to_the_bots_own_screen_not_root():
    # 4 = bots, 1 = harrybot, 1 = edit, 0 = field list back (nothing staged) ->
    # the bot's own screen, 2 = save profile (proves we landed there, not root),
    # path, Enter, 0 = exit
    answers = ["6", "1", "1", "0", "2", "/tmp/bot.json", "", "0"]
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

    code, calls, output = run_menu(["2", "", "0"], session=ExplodingSession())

    assert code == 0
    assert calls == []
    assert "error: Cannot resolve chat." in screens(output)


# --- send -------------------------------------------------------------------


def test_send_stages_a_topic_and_a_message():
    # 3 = send, 1 = forum groups, 1 = Hermes, 1 = Topic row, 1 = Deploys,
    # 2 = Message, 3 = Send it
    answers = ["3", "1", "1", "1", "1", "2", "ship it", ".", "4", "", "0"]
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
    answers = ["3", "1", "1", "2", "hi", ".", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].topic is None
    assert "[(the chat itself)]" in screens(output)


def test_send_topic_picker_offers_the_chat_itself():
    # Topic > row 3 is the extra after the two topics
    answers = ["3", "1", "1", "1", "3", "2", "hi", ".", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].topic is None
    assert "The chat itself (no topic)" in screens(output)


def test_send_hides_the_topic_row_for_a_non_forum_chat():
    # 3 = send, 2 = Channels, 1 = Alerts, 1 = Message, 2 = Send it
    answers = ["3", "2", "1", "1", "hi", ".", "3", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert "Topic" not in screens(output)
    assert calls[0].chat == "-100222"


def test_send_says_a_chat_with_no_topics_goes_to_the_chat():
    session = FakeSession(topics=[])
    answers = ["3", "1", "1", "1", "2", "hi", ".", "4", "", "0"]
    code, calls, output = run_menu(answers, session=session)

    assert code == 0
    assert calls[0].topic is None
    assert "no topics" in screens(output)


def test_send_refuses_to_run_without_a_message():
    # 4 = Send it with nothing staged, then 0 back out of each screen to the root.
    answers = ["3", "1", "1", "4", "0", "0", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls == []
    assert "Type a message or attach a file first." in screens(output)


def test_send_shows_a_long_message_on_one_line():
    body = "line one\nline two that keeps going well past the width of the row"
    answers = ["3", "1", "1", "2", body, ".", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].text == body
    text = screens(output)
    assert "line one / line two" in text
    assert "…" in text


# --- create -----------------------------------------------------------------


def test_create_group_asks_for_a_title_and_description():
    # 4 = create, 1 = Group
    answers = ["4", "1", "Hermes", "the agency", "", "0"]
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
    answers = ["4", "2", "Hermes", "", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].forum is True
    assert calls[0].about is None


def test_create_channel_asks_for_a_broadcast():
    answers = ["4", "3", "Alerts", "", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].create_kind == "channel"
    assert calls[0].title == "Alerts"


def test_create_topic_picks_a_forum_group_first():
    # 4 = Topic in a forum group, 1 = Hermes (the only forum group)
    answers = ["4", "4", "1", "Deploys", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    args = calls[0]
    assert args.create_kind == "topic"
    assert args.chat == "-100111"
    assert args.title == "Deploys"


def test_create_cancelling_the_title_returns_to_the_kind_list():
    answers = ["4", "1", "", "0", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls == []
    assert screens(output).count("1. Group") == 2


def test_send_reoffers_the_staged_message_in_the_header():
    # 2 = Message twice: the second header must carry what was already typed, so
    # keeping it does not mean typing it again. A blank first line keeps it.
    answers = ["3", "1", "1", "2", "hiiiii", ".", "2", "", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "hiiiii"
    assert "Message [hiiiii] (blank cancels, . on its own line ends it):" in screens(output)


def test_send_message_header_is_bare_before_anything_is_typed():
    answers = ["3", "1", "1", "2", "hi", ".", "4", "", "0"]
    _code, _calls, output = run_menu(answers)

    assert "Message (blank cancels, . on its own line ends it):" in screens(output)


def test_send_shows_a_long_staged_message_cut_in_the_header():
    body = "line one\nline two that keeps going well past the width of the row"
    answers = ["3", "1", "1", "2", body, ".", "2", "", "4", "", "0"]
    _code, calls, output = run_menu(answers)

    assert calls[0].text == body
    reoffer = [line for line in output if line.startswith("Message [")][0]
    assert "line one / line two" in reoffer
    assert "\n" not in reoffer


def test_send_takes_a_multi_line_message_from_the_menu():
    answers = ["3", "1", "1", "2", "deploy is green", "all 300 tests pass", ".", "4", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "deploy is green\nall 300 tests pass"


def test_send_pasted_lines_become_body_not_menu_answers():
    # The hazard this replaced: line two used to be read as the next menu choice.
    answers = ["3", "1", "1", "2", "one", "2", "3", ".", "4", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "one\n2\n3"


def test_send_attaches_a_file_from_the_menu():
    # 3 = Files row, then a path; 4 = Send it
    answers = ["3", "1", "1", "3", "/tmp/shot.png", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].files == ["/tmp/shot.png"]
    # A file alone is a valid send: no message body required.
    assert calls[0].text is None
    assert "[shot.png]" in screens(output)


def test_send_attaches_several_files():
    # Files row a second time offers Add another / Remove them all
    answers = ["3", "1", "1", "3", "/tmp/a.png", "3", "1", "/tmp/b.pdf", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].files == ["/tmp/a.png", "/tmp/b.pdf"]
    assert "[a.png +1 more]" in screens(output)


def test_send_can_clear_the_attachments():
    answers = ["3", "1", "1", "3", "/tmp/a.png", "3", "2", "2", "hi", ".", "4", "", "0"]
    code, calls, output = run_menu(answers)

    assert code == 0
    assert calls[0].files is None
    assert calls[0].text == "hi"
    assert "[(none)]" in screens(output)


def test_send_a_caption_with_a_file_sends_both():
    answers = ["3", "1", "1", "2", "look at this", ".", "3", "/tmp/a.png", "4", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].text == "look at this"
    assert calls[0].files == ["/tmp/a.png"]


def test_send_cancelling_the_file_path_stages_nothing():
    answers = ["3", "1", "1", "3", "", "2", "hi", ".", "4", "", "0"]
    code, calls, _output = run_menu(answers)

    assert code == 0
    assert calls[0].files is None


# --- after an action: Run it again / Tweak it / Main menu ---------------------


def test_after_run_runs_the_same_search_again():
    # 2 = search, 1 = forum groups, 1 = Hermes, 7 = run, 1 = run it again, Enter, 0
    code, calls, output = run_menu(["2", "1", "1", "7", "1", "", "0"])

    assert code == 0
    assert [call.chat for call in calls] == ["-100111", "-100111"]
    assert "1. Run it again" in screens(output)
    assert "2. Tweak it" in screens(output)
    assert "3. Main menu" in screens(output)


def test_search_tweak_returns_to_the_form_with_the_filters_kept():
    # ... 2 = contains (unset, so straight to the prompt), "hello", 7 = run,
    # 2 = tweak, 6 = limit, 5, 7 = run again, Enter, 0
    answers = ["2", "1", "1", "2", "hello", "7", "2", "6", "5", "7", "", "0"]
    _code, calls, output = run_menu(answers)

    assert len(calls) == 2
    assert calls[0].keyword == "hello"
    assert calls[0].limit is None
    assert calls[1].keyword == "hello"
    assert calls[1].limit == 5
    assert "Contains       [hello]" in screens(output)


def test_after_run_says_not_done_when_the_action_was_declined_and_failed_on_an_error():
    _calls, declined = recorder(result=1)
    _code, _unused, output = run_menu(["2", "1", "1", "7", "", "0"], runner=declined)
    assert "Not done" in screens(output)

    _calls, broken = recorder(error=ValueError("no"))
    _code, _unused, output = run_menu(["2", "1", "1", "7", "", "0"], runner=broken)
    assert "Failed" in screens(output)


def test_discover_offers_run_again_but_no_tweak():
    # 1 = discover, 1 = managed, 1 = print, 1 = run it again, Enter = menu, 0
    _code, calls, output = run_menu(["1", "1", "1", "1", "", "0"])

    assert [call.all_chats for call in calls] == [False, False]
    assert "1. Run it again" in screens(output)
    assert "Tweak it" not in screens(output)


def test_doctor_keeps_the_plain_enter_or_zero_prompt():
    _code, calls, output = run_menu(["7", "", "0"])

    assert [call.command for call in calls] == ["doctor"]
    assert "Run it again" not in screens(output)
    assert "Main menu" not in screens(output)


def test_send_tweak_keeps_the_message_files_and_topic():
    # 3 = send, 1 = forum groups, 1 = Hermes, 2 = message, "hi", ".", 4 = send,
    # 2 = tweak, 4 = send again, Enter, 0
    answers = ["3", "1", "1", "2", "hi", ".", "4", "2", "4", "", "0"]
    _code, calls, output = run_menu(answers)

    assert [call.text for call in calls] == ["hi", "hi"]
    assert screens(output).count("Message   [hi]") >= 2


def test_create_offers_another_instead_of_a_rerun():
    # 4 = create, 1 = group, "Team", blank description, 1 = create another -> the
    # kind list, 3 = channel, "News", blank description, Enter, 0
    answers = ["4", "1", "Team", "", "1", "3", "News", "", "", "0"]
    _code, calls, output = run_menu(answers)

    assert [(call.create_kind, call.title) for call in calls] == [("group", "Team"), ("channel", "News")]
    assert "1. Create another" in screens(output)
    assert "Run it again" not in screens(output)


# --- send: back asks before discarding ---------------------------------------


def test_send_back_with_a_message_asks_before_discarding():
    # ... 2 = message, "hi", ".", 0 = back -> asks, 1 = keep editing, 0 = back -> asks
    # again, 0 = discard -> the chat picker, 0 = root, 0 = exit
    answers = ["3", "1", "1", "2", "hi", ".", "0", "1", "0", "0", "0", "0"]
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
    _code, calls, output = run_menu(["3", "1", "1", "0", "0", "0"])

    assert calls == []
    assert "Unsent message" not in screens(output)


# --- clear: the audit gaps and what it remembers ------------------------------


def test_clear_all_topics_row_is_the_all_topics_flag():
    # 5 = clear, 1 = Hermes, 3 = All topics (the row after the two topics), dry-run,
    # 1 = for real, Enter, 0
    _code, calls, output = run_menu(["5", "1", "3", "1", "", "0"])

    assert "3. All topics (no need to tick)" in screens(output)
    assert calls[0].all_topics is True
    assert calls[0].topics is None
    assert calls[1].execute is True
    assert calls[1].all_topics is True


def test_clear_batch_size_is_an_advanced_row_on_the_dry_run_screen():
    # ... 5 = continue, dry-run, 2 = batch size, 25, 1 = for real, Enter, 0
    _code, calls, output = run_menu(["5", "1", "1", "5", "2", "25", "1", "", "0"])

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
    answers = ["5", "1", "1", "5", "0", "0", "1", "5", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers)

    assert len(calls) == 1
    assert calls[0].topics == [141]
    # Ticked once, shown ticked four times: after the tick, back from the dry-run
    # screen, after re-picking the chat, and back from the dry-run screen again.
    assert screens(output).count("[x] 141") == 4
    assert "Same topics as the last dry-run; its count still stands." in screens(output)


def test_clear_more_topics_after_the_real_pass_starts_from_a_clean_ticker():
    # ... 1 = for real, 1 = clear more topics -> the ticker with nothing ticked, 0, 0, 0
    _code, calls, output = run_menu(["5", "1", "1", "5", "1", "1", "0", "0", "0", "0"])

    text = screens(output)
    assert calls[1].execute is True
    assert "1. Clear more topics" in text
    assert text.count("[x] 141") == 1
    assert text.count("[ ] 141") == 2


# --- bots: the audit gaps ------------------------------------------------------


def test_bots_saves_the_whole_bot_list_to_json():
    # 6 = my bots, 2 = save the list (row after the one bot), path, Enter, 0
    _code, calls, output = run_menu(["6", "2", "/tmp/bots.json", "", "0"])

    assert "2. Save the bot list to a JSON file" in screens(output)
    assert calls[0].command == "bots"
    assert calls[0].bot is None
    assert calls[0].json_output == "/tmp/bots.json"


def test_bots_typed_username_shows_a_bot_you_do_not_own_read_only():
    other = BotInfo(id=777, username="otherbot", name="Other", bio="Not mine", description=None, is_owned=False)
    session = FakeSession(profile=other)
    # 6 = my bots, 3 = type a bot, "@otherbot", 0 = back to the list, 0 = root, 0 = exit
    _code, calls, output = run_menu(["6", "3", "@otherbot", "0", "0", "0"], session=session)

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

    _code, calls, output = run_menu(["6", "3", "nope", "0", "0"], session=Session())

    text = screens(output)
    assert calls == []
    assert "error: 'nope' is not a bot." in text
    assert text.count("1. @harrybot  Harry") == 2


def test_bots_with_none_of_your_own_still_offers_the_lookup():
    session = FakeSession(bots=[])
    _code, calls, output = run_menu(["6", "0", "0"], session=session)

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
    _code, _calls, _output = run_menu(["6", "3", "harry", "0", "0", "0"], session=session)

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
    answers = ["6", "1", "1", "1", "2", "Harry Two", "8", "1", "0", "0", "0", "0"]
    _code, calls, output = run_menu(answers, session=session)

    assert calls[0].name == "Harry Two"
    assert "1. Edit more" in screens(output)
    assert session.profile_calls == 2


# --- breadcrumbs and the colour boundary ---------------------------------------


def test_every_screen_below_the_root_carries_its_trail():
    # 2 = search, 1 = forum groups, 1 = Hermes, 3 = From, 0 = back to the form,
    # 0 = back to the picker, 0 = root, 0 = exit
    _code, _calls, output = run_menu(["2", "1", "1", "3", "0", "0", "0", "0"])
    text = screens(output)
    assert "Main › Search › Pick a chat\n" in text
    assert "Main › Search › Pick a chat › Forum groups\n" in text
    assert "Main › Search › Hermes\n" in text
    assert "Main › Search › Hermes › From\n" in text

    # 6 = my bots, 1 = harrybot, 1 = edit, 1 = name, then back out four times and exit
    _code, _calls, output = run_menu(["6", "1", "1", "1", "0", "0", "0", "0", "0"])
    text = screens(output)
    assert "Main › My bots\n" in text
    assert "Main › My bots › @harrybot\n" in text
    assert "Main › My bots › @harrybot › Edit\n" in text
    assert "Main › My bots › @harrybot › Edit › Name\n" in text

    # 5 = clear, 1 = Hermes, 1 = tick, 5 = continue, 1 = for real, Enter, 0
    _code, _calls, output = run_menu(["5", "1", "1", "5", "1", "", "0"])
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
