"""Chat folders on this tool: the four verbs, their gates, and the account-only rule.

Spec section 13's Folders row and the P7 row of section 21. The fake Telegram
is the structure tests' `World` with a list of dialog filters on it: two the
account made, one that arrived through a chatlist invite, and the "All chats"
row Telegram always sends and nobody made. It answers the two dialog-filter
calls and records every request, so "nothing was written" is checked by
looking at what reached the fake rather than by trusting the message.
"""

from __future__ import annotations

import json

import pytest
from telethon.tl import types

from telegram_tools import cli
from telegram_tools import folders as folders_ops
from telegram_tools.adapters.folders import folder_of
from telegram_tools.envelope import CommandError
from test_archive_sync import ACCOUNT, home  # noqa: F401 - fixture
from test_structure import FORUM_ID, PLAIN_ID, CHANNEL_ID, FakeClient, World, envelope_of, run_cli  # noqa: F401 - fixture

FORUM = "@teamhermes"
CHANNEL = "@agencyalerts"
WRITES = ("UpdateDialogFilterRequest",)


def peer(marked: int) -> types.InputPeerChannel:
    """A chat as Telegram puts it in a filter: a real input peer, so its marked id reads back."""
    return types.InputPeerChannel(channel_id=-1000000000000 - marked if marked < 0 else marked, access_hash=0)


def chat_peer(marked: int) -> types.InputPeerChannel:
    return types.InputPeerChannel(channel_id=abs(marked) - 1000000000000, access_hash=0)


def a_filter(folder_id: int, title: str, *, include=(), exclude=(), pinned=(), emoticon=None, **flags) -> types.DialogFilter:
    return types.DialogFilter(
        id=folder_id,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=list(pinned),
        include_peers=list(include),
        exclude_peers=list(exclude),
        emoticon=emoticon,
        **flags,
    )


def a_chatlist(folder_id: int, title: str, *, include=()) -> types.DialogFilterChatlist:
    return types.DialogFilterChatlist(
        id=folder_id,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=[],
        include_peers=list(include),
    )


FORUM_PEER = chat_peer(FORUM_ID)
PLAIN_PEER = chat_peer(PLAIN_ID)
CHANNEL_PEER = chat_peer(CHANNEL_ID)


class FolderedClient(FakeClient):
    """The structure fake, plus the account's dialog filters and the two calls over them."""

    def __init__(self, world: World | None = None, *, filters=None) -> None:
        super().__init__(world)
        self.me = ACCOUNT
        self.filters = list(
            filters
            if filters is not None
            else [
                types.DialogFilterDefault(),
                a_filter(2, "Work", include=[FORUM_PEER], pinned=[PLAIN_PEER], groups=True, emoticon="💼"),
                a_filter(3, "Noise", include=[CHANNEL_PEER], exclude=[PLAIN_PEER], exclude_muted=True),
                a_chatlist(4, "Shared club", include=[FORUM_PEER]),
            ]
        )

    async def get_me(self):
        return self.me

    def by_id(self, folder_id: int):
        return next((raw for raw in self.filters if getattr(raw, "id", None) == folder_id), None)

    async def __call__(self, request):
        name = type(request).__name__
        if name == "GetDialogFiltersRequest":
            self.world.requests.append(request)
            return types.messages.DialogFilters(filters=list(self.filters))
        if name == "UpdateDialogFilterRequest":
            self.world.requests.append(request)
            self.filters = [raw for raw in self.filters if getattr(raw, "id", None) != request.id]
            if request.filter is not None:
                self.filters.append(request.filter)
            return True
        return await super().__call__(request)


def writes(fake) -> list[str]:
    return [type(r).__name__ for r in fake.world.requests if type(r).__name__ in WRITES]


def audit_lines(home):
    path = home / ".telegram-tools" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# -- the domain ---------------------------------------------------------------


def test_the_default_row_is_not_a_folder_and_a_chatlist_is_a_shared_one():
    assert folder_of(types.DialogFilterDefault()) is None
    mine = folder_of(a_filter(2, "Work", include=[FORUM_PEER], groups=True, emoticon="💼"))
    assert (mine.id, mine.title, mine.emoticon, mine.kind) == (2, "Work", "💼", "folder")
    assert (mine.types, mine.include) == (("groups",), (f"tg:chat:{FORUM_ID}",))
    shared = folder_of(a_chatlist(4, "Shared club", include=[FORUM_PEER]))
    assert (shared.kind, shared.editable, shared.types) == ("shared", False, ())


def test_a_folder_needs_a_chat_or_a_category_it_can_actually_match():
    folders_ops.require_matches_something(("groups",), ())
    folders_ops.require_matches_something((), ("tg:chat:-100",))
    with pytest.raises(ValueError, match="it would match nothing"):
        folders_ops.require_matches_something(("exclude_muted",), ())


def test_the_next_id_is_the_lowest_free_one_from_two():
    assert folders_ops.next_id([]) == 2
    assert folders_ops.next_id([folders_ops.Folder(id=2, title="a"), folders_ops.Folder(id=4, title="b")]) == 3


def test_types_are_telegrams_own_names_and_an_unknown_one_is_a_usage_error():
    assert folders_ops.parse_types("groups, bots") == ("bots", "groups")
    assert folders_ops.parse_types("none") == ()
    with pytest.raises(ValueError, match="Unknown folder category"):
        folders_ops.parse_types("chats")


def test_a_shared_folder_refuses_both_writes_by_name():
    shared = folders_ops.Folder(id=4, title="Shared club", kind="shared")
    for verb in ("edit", "delete"):
        with pytest.raises(CommandError) as refused:
            folders_ops.require_editable(shared, verb)
        assert refused.value.code == "PLATFORM_UNSUPPORTED" and "chatlist invite" in str(refused.value)


# -- the read ------------------------------------------------------------------


def test_folders_list_shows_every_folder_and_writes_nothing(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "folders", "list"], client=FolderedClient(), capsys=capsys)
    assert code == 0, out
    rows = envelope_of(out)["result"]["folders"]
    assert [row["id"] for row in rows] == [2, 3, 4], "the All chats row is not a folder anyone made"
    work = rows[0]
    assert (work["rid"], work["title"], work["emoticon"], work["types"]) == ("tg:folder:2", "Work", "💼", ["groups"])
    assert (work["include"], work["pinned"]) == ([f"tg:chat:{FORUM_ID}"], [f"tg:chat:{PLAIN_ID}"])
    assert rows[2]["kind"] == "shared"
    assert writes(fake) == []


def test_folders_list_reaches_telegram_once(run_cli, capsys):
    code, _out, _err, fake = run_cli(["--json", "folders", "list"], client=FolderedClient(), capsys=capsys)
    assert code == 0
    assert [type(r).__name__ for r in fake.world.requests] == ["GetDialogFiltersRequest"]


# -- create --------------------------------------------------------------------


def test_folders_create_takes_the_lowest_free_id_and_the_chats_named(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "folders", "create", "--title", "Ops", "--emoji", "🛠", "--include", FORUM, "--include", CHANNEL, "--types", "groups"],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["target"]["rid"] == "tg:folder:5"
    assert writes(fake) == ["UpdateDialogFilterRequest"]
    made = fake.by_id(5)
    assert (made.title.text, made.emoticon, made.groups, made.bots) == ("Ops", "🛠", True, False)
    assert [p.channel_id for p in made.include_peers] == [FORUM_PEER.channel_id, CHANNEL_PEER.channel_id]
    assert envelope["result"]["folder"]["include"] == [f"tg:chat:{FORUM_ID}", f"tg:chat:{CHANNEL_ID}"]
    assert envelope["evidence"]["readback"] == "Ops: made"
    line = audit_lines(home)[0]
    assert (line["command"], line["approval"]) == ("folders create", "prompt_y")
    assert line["targets"] == ["tg:folder:5"]


def test_folders_create_refuses_a_folder_that_would_match_nothing(run_cli, capsys):
    code, _out, err, fake = run_cli(
        ["folders", "create", "--title", "Empty", "--types", "exclude_muted"],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 2 and "it would match nothing" in err
    assert writes(fake) == []


def test_a_declined_create_writes_nothing(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "folders", "create", "--title", "Ops", "--include", FORUM],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="n",
    )
    assert code == 1 and envelope_of(out)["status"] == "cancelled"
    assert writes(fake) == [] and audit_lines(home) == []


# -- edit ----------------------------------------------------------------------


def test_folders_edit_changes_what_was_named_and_keeps_the_rest(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "folders", "edit", "--id", "2", "--title", "Work stuff"],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    after = fake.by_id(2)
    assert after.title.text == "Work stuff"
    assert after.emoticon == "💼" and after.groups is True, "a field no flag named is left alone"
    assert [p.channel_id for p in after.include_peers] == [FORUM_PEER.channel_id]
    assert [p.channel_id for p in after.pinned_peers] == [PLAIN_PEER.channel_id], "the pinned chats are put back"
    assert envelope_of(out)["evidence"]["readback"] == "Work stuff: title 'Work' -> 'Work stuff'"
    assert [line["command"] for line in audit_lines(home)] == ["folders edit"]


def test_folders_edit_replaces_the_lists_and_none_empties_one(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "folders", "edit", "--id", "3", "--include", FORUM, "--exclude", "none"],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    after = fake.by_id(3)
    assert [p.channel_id for p in after.include_peers] == [FORUM_PEER.channel_id]
    assert after.exclude_peers == []
    readback = envelope_of(out)["evidence"]["readback"]
    assert f"chats 'tg:chat:{CHANNEL_ID}' -> 'tg:chat:{FORUM_ID}'" in readback


def test_folders_edit_clearing_the_categories_keeps_the_folder_matching_something(run_cli, capsys):
    code, _out, err, fake = run_cli(
        ["folders", "edit", "--id", "3", "--include", "none", "--types", "none"],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 2 and "it would match nothing" in err
    assert writes(fake) == []


def test_an_unknown_folder_id_names_the_ids_there_are(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "folders", "edit", "--id", "99", "--title", "Nope"],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "TARGET_NOT_FOUND" and "2, 3, 4" in error["hint"]
    assert writes(fake) == []


def test_editing_nothing_is_a_usage_error(run_cli, capsys):
    code, _out, err, fake = run_cli(["folders", "edit", "--id", "2"], client=FolderedClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and "folders edit changes nothing" in err
    assert writes(fake) == []


@pytest.mark.parametrize("argv", [["edit", "--id", "4", "--title", "Mine"], ["delete", "--id", "4", "--execute"]])
def test_a_shared_folder_is_listed_but_never_written(run_cli, capsys, argv):
    code, out, _err, fake = run_cli(["--json", "folders", *argv], client=FolderedClient(), capsys=capsys, isatty=True, answer="Shared club")
    assert code == 2 and envelope_of(out)["error"]["code"] == "PLATFORM_UNSUPPORTED"
    assert writes(fake) == []


# -- delete --------------------------------------------------------------------


def test_folders_delete_dry_runs_then_asks_for_the_folders_exact_title(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "folders", "delete", "--id", "2"], client=FolderedClient(), capsys=capsys, isatty=True)
    assert code == 0 and envelope_of(out)["status"] == "dry_run"
    assert writes(fake) == [] and audit_lines(home) == []

    code, out, _err, fake = run_cli(
        ["--json", "folders", "delete", "--id", "2", "--execute"], client=FolderedClient(), capsys=capsys, isatty=True, answer="Noise"
    )
    assert code == 1 and envelope_of(out)["status"] == "cancelled"
    assert writes(fake) == [], "the wrong title deletes nothing"

    code, out, _err, fake = run_cli(
        ["--json", "folders", "delete", "--id", "2", "--execute"], client=FolderedClient(), capsys=capsys, isatty=True, answer="work"
    )
    assert code == 0, out
    assert writes(fake) == ["UpdateDialogFilterRequest"] and fake.by_id(2) is None
    assert envelope_of(out)["evidence"]["readback"] == "folder 2 is gone"
    line = audit_lines(home)[0]
    assert (line["command"], line["approval"]) == ("folders delete", "typed_name")


def test_folders_delete_needs_a_terminal_in_either_mode(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "folders", "delete", "--id", "2", "--execute"], client=FolderedClient(), capsys=capsys, isatty=False
    )
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    assert writes(fake) == []


def test_no_folders_verb_has_a_yes(run_cli, capsys):
    for argv in (
        ["folders", "create", "--title", "Ops", "--include", FORUM, "--yes"],
        ["folders", "edit", "--id", "2", "--title", "Ops", "--yes"],
        ["folders", "delete", "--id", "2", "--execute", "--yes"],
    ):
        code, _out, err, fake = run_cli(argv, client=FolderedClient(), capsys=capsys, isatty=True, answer="y")
        assert code == 2 and "--yes" in err, argv
        assert writes(fake) == []


# -- the plan, and what never reaches an envelope ------------------------------


def test_the_plan_names_the_folder_and_needs_no_chat_right(run_cli, capsys):
    code, out, _err, _fake = run_cli(
        ["--json", "folders", "create", "--title", "Ops", "--include", FORUM],
        client=FolderedClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    plan = envelope_of(out)["plan"]
    assert plan["preflight"]["required"] == [], "a folder is the account's, not a chat's"
    assert plan["approval"] == "prompt_y"
    assert envelope_of(out)["target"]["kind"] == "folder"
