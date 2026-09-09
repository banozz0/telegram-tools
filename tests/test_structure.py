"""Structure blueprints on this tool: the port, the four commands, the menu rows and the P6 row.

Spec section 12 and the P6 row of section 21. Every run here is against a fake
Telegram that holds chats and topics in memory and answers the raw requests the
port makes -- `channels.getFullChannel`, the forum-topic walk, the edit and
toggle calls, `createChannel` and `createForumTopic` -- minting ids the way the
real one does (a topic's id is its service message's). It refuses every delete
request outright, which is how "an apply never deletes" is proved rather than
asserted. No session is opened and no socket is touched.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import utils
from telethon.tl import types
from telethon.tl.types import ChatBannedRights

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools import structure as structure_ops
from telegram_tools._core import blueprint as core
from telegram_tools._core.contract import validate_envelope
from telegram_tools._core.identity import Target
from telegram_tools.adapters.account import RIGHT_NAMES
from telegram_tools.adapters.blueprint import (
    BANNED_RIGHT_NAMES,
    TelegramBlueprintPort,
    banned_right_names,
    banned_rights,
    chat_kind,
    topic_rid,
)
from telegram_tools.envelope import CommandError
from test_archive_sync import ACCOUNT, home  # noqa: F401 - fixture

FORUM_ID = -1001000000001
PLAIN_ID = -1001000000002
CHANNEL_ID = -1001000000003
BASIC_ID = -400000000005
FORUM_RID = f"tg:chat:{FORUM_ID}"
DOBBY_ICON = 5350554349074391003
MUTATING = (
    "CreateChannelRequest",
    "CreateForumTopicRequest",
    "EditForumTopicRequest",
    "EditTitleRequest",
    "EditChatAboutRequest",
    "EditChatDefaultBannedRightsRequest",
    "ToggleSlowModeRequest",
    "ToggleJoinRequestRequest",
    "ToggleForumRequest",
)
DELETES = ("DeleteChannelRequest", "DeleteTopicHistoryRequest", "DeleteHistoryRequest", "DeleteMessagesRequest")


def run(coroutine):
    return asyncio.run(coroutine)


# -- the fake Telegram -------------------------------------------------------


def _channel(bare_id: int, title: str, **flags) -> types.Channel:
    return types.Channel(id=bare_id, title=title, photo=None, date=None, **flags)


class World:
    """Chats and their topics, and every request that reached them."""

    def __init__(self) -> None:
        self.chats: dict[int, dict] = {}
        self.requests: list = []
        self.fail_on: tuple[str, int] | None = None
        self.next_bare = 9000
        self.add(
            FORUM_ID,
            _channel(1000000001, "Team Hermes", megagroup=True, forum=True, join_request=True, default_banned_rights=ChatBannedRights(until_date=None, send_stickers=True, send_gifs=True), username="teamhermes"),
            about="the agency's room",
            slowmode_seconds=30,
            linked_chat_id=None,
            topics=[(1, "General", None), (141, "Dobby", DOBBY_ICON), (217, "Support", None), (300, "Deploys", None)],
        )
        self.add(PLAIN_ID, _channel(1000000002, "Agency", megagroup=True), about="", slowmode_seconds=0)
        self.add(CHANNEL_ID, _channel(1000000003, "Alerts", broadcast=True, username="agencyalerts"), about="green means go", linked_chat_id=1000000002)
        self.chats[BASIC_ID] = {
            "entity": types.Chat(id=400000000005, title="Old Basic", photo=None, participants_count=3, date=None, version=1),
            "about": "",
            "slowmode_seconds": 0,
            "linked_chat_id": None,
            "participants_count": 3,
            "topics": [],
        }

    def add(self, marked: int, entity: types.Channel, *, about: str = "", slowmode_seconds: int = 0, linked_chat_id=None, topics=()) -> None:
        self.chats[marked] = {
            "entity": entity,
            "about": about,
            "slowmode_seconds": slowmode_seconds,
            "linked_chat_id": linked_chat_id,
            "participants_count": 12,
            "topics": [
                SimpleNamespace(id=topic_id, title=title, icon_emoji_id=icon, top_message=topic_id, date=None, closed=False, hidden=False)
                for topic_id, title, icon in topics
            ],
        }

    def marked_for(self, bare: int) -> int:
        return -1000000000000 - bare

    def mutations(self) -> list[str]:
        return [type(request).__name__ for request in self.requests if type(request).__name__ in MUTATING]

    def topics_of(self, marked: int) -> list[tuple[str, int | None]]:
        return [(topic.title, topic.icon_emoji_id) for topic in self.chats[marked]["topics"]]

    def by_peer(self, peer) -> tuple[int, dict]:
        marked = utils.get_peer_id(peer)
        return marked, self.chats[marked]


class FakeClient:
    """A signed-in account over one `World`: dialogs, entities, rights, and the raw calls."""

    def __init__(self, world: World | None = None) -> None:
        self.world = world or World()
        self.disconnected = False

    async def get_me(self):
        return ACCOUNT

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.disconnected = True

    def _dialog(self, marked: int):
        entity = self.world.chats[marked]["entity"]
        return SimpleNamespace(id=marked, title=entity.title, entity=entity, input_entity=self._input(marked, entity))

    @staticmethod
    def _input(marked: int, entity):
        # A real input peer, not a stand-in: production code reads a peer's
        # marked id back through `utils.get_peer_id`, and a namespace with the
        # right attribute names would pass a test the real object would fail.
        if isinstance(entity, types.Chat):
            return types.InputPeerChat(chat_id=entity.id)
        return types.InputPeerChannel(channel_id=entity.id, access_hash=0)

    async def iter_dialogs(self):
        for marked in list(self.world.chats):
            yield self._dialog(marked)

    async def get_entity(self, reference):
        for marked, chat in self.world.chats.items():
            entity = chat["entity"]
            if reference == marked or (isinstance(reference, str) and reference.lstrip("@") == getattr(entity, "username", None)):
                return entity
        raise ValueError(f"no entity {reference}")

    async def get_input_entity(self, entity):
        for marked, chat in self.world.chats.items():
            if chat["entity"] is entity:
                return self._input(marked, entity)
        raise ValueError("unknown entity")

    async def get_peer_id(self, entity):
        for marked, chat in self.world.chats.items():
            if chat["entity"] is entity:
                return marked
        raise ValueError("unknown entity")

    async def get_permissions(self, peer, user):
        return SimpleNamespace(**{name: True for name in RIGHT_NAMES})

    async def __call__(self, request):
        name = type(request).__name__
        self.world.requests.append(request)
        if name in DELETES:
            raise AssertionError(f"an apply never deletes, yet {name} was sent")
        if self.world.fail_on and self.world.fail_on[0] == name:
            count = sum(1 for sent in self.world.requests if type(sent).__name__ == name)
            if count == self.world.fail_on[1]:
                raise RuntimeError("Telegram said no (the fake)")
        if name == "GetFullChannelRequest":
            marked, chat = self.world.by_peer(request.channel)
            full_chat = SimpleNamespace(
                about=chat["about"],
                slowmode_seconds=chat["slowmode_seconds"],
                linked_chat_id=chat["linked_chat_id"],
                participants_count=chat["participants_count"],
                exported_invite=SimpleNamespace(link="https://t.me/+secretinvite"),
            )
            return SimpleNamespace(chats=[chat["entity"]], full_chat=full_chat)
        if name == "GetForumTopicsRequest":
            _marked, chat = self.world.by_peer(request.peer)
            return SimpleNamespace(topics=list(chat["topics"]), count=len(chat["topics"]))
        if name == "GetForumTopicsByIDRequest":
            _marked, chat = self.world.by_peer(request.peer)
            wanted = [int(topic_id) for topic_id in request.topics]
            found = [topic for topic in chat["topics"] if topic.id in wanted]
            return SimpleNamespace(topics=found, count=len(found))
        if name == "GetCustomEmojiDocumentsRequest":
            return []
        if name == "CreateForumTopicRequest":
            marked, chat = self.world.by_peer(request.peer)
            new_id = max([topic.id for topic in chat["topics"]] + [1]) + 100
            chat["topics"].append(SimpleNamespace(id=new_id, title=request.title, icon_emoji_id=request.icon_emoji_id or None, top_message=new_id, date=None, closed=False, hidden=False))
            return SimpleNamespace(chats=[], updates=[SimpleNamespace(message=SimpleNamespace(id=new_id))])
        if name == "EditForumTopicRequest":
            _marked, chat = self.world.by_peer(request.peer)
            topic = next(topic for topic in chat["topics"] if topic.id == request.topic_id)
            if request.title is not None:
                topic.title = request.title
            if request.icon_emoji_id is not None:
                topic.icon_emoji_id = request.icon_emoji_id or None
            if request.closed is not None:
                topic.closed = bool(request.closed)
            if request.hidden is not None:
                topic.hidden = bool(request.hidden)
            return SimpleNamespace(updates=[])
        if name == "ToggleForumRequest":
            _marked, chat = self.world.by_peer(request.channel)
            chat["entity"].forum = bool(request.enabled)
            if not request.enabled:
                # What Telegram does: the topics stop existing and their
                # messages become one stream.
                chat["topics"] = []
            return SimpleNamespace(updates=[])
        if name == "EditTitleRequest":
            _marked, chat = self.world.by_peer(request.channel)
            chat["entity"].title = request.title
            return SimpleNamespace(updates=[])
        if name == "EditChatAboutRequest":
            _marked, chat = self.world.by_peer(request.peer)
            chat["about"] = request.about
            return True
        if name == "EditChatDefaultBannedRightsRequest":
            _marked, chat = self.world.by_peer(request.peer)
            chat["entity"].default_banned_rights = request.banned_rights
            return SimpleNamespace(updates=[])
        if name == "ToggleSlowModeRequest":
            _marked, chat = self.world.by_peer(request.channel)
            chat["slowmode_seconds"] = request.seconds
            return SimpleNamespace(updates=[])
        if name == "ToggleJoinRequestRequest":
            _marked, chat = self.world.by_peer(request.channel)
            chat["entity"].join_request = request.enabled
            return SimpleNamespace(updates=[])
        if name == "CreateChannelRequest":
            self.world.next_bare += 1
            bare = self.world.next_bare
            entity = _channel(bare, request.title, megagroup=bool(request.megagroup), broadcast=bool(request.broadcast), forum=bool(request.forum))
            marked = self.world.marked_for(bare)
            self.world.add(marked, entity, about=request.about or "", topics=[(1, "General", None)] if request.forum else ())
            return SimpleNamespace(chats=[entity], updates=[])
        raise AssertionError(f"unexpected request {name}")


# -- running the CLI ----------------------------------------------------------


@pytest.fixture
def run_cli(home, monkeypatch):
    """`main(argv)` against a fake account, with the terminal and the typed answer controlled."""

    def go(argv, *, client=None, capsys, isatty=False, answer="", before_answer=None):
        fake = client or FakeClient()

        async def started(_client, *, authorize=True):
            return fake

        def readline():
            if before_answer is not None:
                before_answer(fake.world)
            return f"{answer}\n"

        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", started)
        monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=readline))
        monkeypatch.setattr("builtins.input", lambda _prompt="": readline().rstrip("\n"))
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return go


def envelope_of(out: str) -> dict:
    payload = json.loads(out)
    validate_envelope(payload)
    return payload


def export_to(run_cli, capsys, path: Path, chat: str, *, client=None):
    code, out, _err, fake = run_cli(["--json", "structure", "export", "--chat", chat, "--output", str(path)], client=client, capsys=capsys)
    assert code == 0, out
    return envelope_of(out), fake


# -- the port ---------------------------------------------------------------


def test_the_port_reads_a_forum_into_the_raw_shape_with_topics_as_rids():
    fake = FakeClient()
    port = TelegramBlueprintPort(fake)
    raw = run(port.read(Target(rid=FORUM_RID, kind="chat", title="Team Hermes", path=("Team Hermes",))))

    assert raw["container"]["rid"] == FORUM_RID
    assert raw["container"]["name"] == "Team Hermes"
    assert raw["container"]["kind"] == "forum"
    assert raw["container"]["about"] == "the agency's room"
    assert raw["container"]["default_banned_rights"] == ["send_gifs", "send_stickers"]
    assert raw["container"]["slow_mode_seconds"] == 30
    assert raw["container"]["join_request"] is True
    # By title, not by id: Telegram gives topics no order a client can set.
    assert [item["rid"] for item in raw["objects"]["topics"]] == [topic_rid(FORUM_ID, n) for n in (300, 141, 1, 217)]
    assert raw["objects"]["topics"][1] == {"rid": topic_rid(FORUM_ID, 141), "name": "Dobby", "position": 1, "icon_emoji_id": DOBBY_ICON}
    assert "exported_invite" not in json.dumps(raw), "an invite link never enters the raw shape"


def test_a_channel_reads_without_default_rights_or_slow_mode_and_a_plain_group_has_no_topics():
    fake = FakeClient()
    port = TelegramBlueprintPort(fake)
    channel = run(port.read(Target(rid=f"tg:chat:{CHANNEL_ID}", kind="chat", title="Alerts", path=("Alerts",))))
    assert channel["container"]["kind"] == "channel"
    assert "default_banned_rights" not in channel["container"] and "slow_mode_seconds" not in channel["container"]
    assert channel["objects"] == {}
    plain = run(port.read(Target(rid=f"tg:chat:{PLAIN_ID}", kind="chat", title="Agency", path=("Agency",))))
    assert plain["container"]["kind"] == "supergroup"
    assert plain["container"]["default_banned_rights"] == []
    assert plain["objects"] == {}


def test_a_basic_group_has_no_blueprint_for_the_create_parity_reason():
    fake = FakeClient()
    port = TelegramBlueprintPort(fake)
    with pytest.raises(CommandError) as caught:
        run(port.read(Target(rid=f"tg:chat:{BASIC_ID}", kind="chat", title="Old Basic", path=("Old Basic",))))
    assert caught.value.code == "PLATFORM_UNSUPPORTED"
    assert "`create` makes supergroups" in str(caught.value)
    assert chat_kind(fake.world.chats[FORUM_ID]["entity"]) == "forum"


def test_the_port_applies_topic_steps_and_container_settings_and_reports_each_rid():
    fake = FakeClient()
    seen: list[tuple[str, str]] = []
    port = TelegramBlueprintPort(fake, on_step=lambda step, rid: seen.append((step["handle"], rid)))
    made = run(port.apply({"op": "create", "handle": "topic:release", "kind": "topic", "position": 4, "fields": {"name": "Release", "icon_emoji_id": 42}, "target_rid": None, "container_rid": f"tg:chat:{PLAIN_ID}"}))
    assert made["target_rid"] == topic_rid(PLAIN_ID, 101)
    assert fake.world.topics_of(PLAIN_ID) == [("Release", 42)]

    run(port.apply({"op": "update", "handle": "topic:release", "kind": "topic", "position": 4, "fields": {"icon_emoji_id": None, "name": "Releases"}, "target_rid": topic_rid(PLAIN_ID, 101), "container_rid": f"tg:chat:{PLAIN_ID}"}))
    edit = fake.world.requests[-1]
    assert (edit.title, edit.icon_emoji_id) == ("Releases", 0), "no icon is sent as 0, because None would mean leave it"
    assert fake.world.topics_of(PLAIN_ID) == [("Releases", None)]

    run(port.apply({"op": "update", "handle": "chat:agency", "kind": "chat", "position": 0, "fields": {"name": "Agency HQ", "about": "hq", "default_banned_rights": ["send_polls"], "slow_mode_seconds": 10, "join_request": True}, "target_rid": f"tg:chat:{PLAIN_ID}", "container_rid": f"tg:chat:{PLAIN_ID}"}))
    chat = fake.world.chats[PLAIN_ID]
    assert (chat["entity"].title, chat["about"], chat["slowmode_seconds"], chat["entity"].join_request) == ("Agency HQ", "hq", 10, True)
    assert banned_right_names(chat["entity"].default_banned_rights) == ["send_polls"]
    assert seen == [("topic:release", topic_rid(PLAIN_ID, 101)), ("topic:release", topic_rid(PLAIN_ID, 101)), ("chat:agency", f"tg:chat:{PLAIN_ID}")]


def test_the_port_refuses_what_no_call_can_do():
    port = TelegramBlueprintPort(FakeClient())
    container = f"tg:chat:{PLAIN_ID}"
    with pytest.raises(core.BlueprintError, match="kind cannot be changed"):
        run(port.apply({"op": "update", "handle": "chat:agency", "kind": "chat", "position": 0, "fields": {"kind": "forum"}, "target_rid": container, "container_rid": container}))
    with pytest.raises(core.BlueprintError, match="no role objects"):
        run(port.apply({"op": "create", "handle": "role:mods", "kind": "role", "position": 0, "fields": {"name": "Mods"}, "target_rid": None, "container_rid": container}))
    with pytest.raises(core.BlueprintError, match="unknown banned right"):
        banned_rights(["fly"])
    assert "send_messages" in BANNED_RIGHT_NAMES and "until_date" not in BANNED_RIGHT_NAMES


def test_the_allowlist_generates_never_transferred_and_admits_no_stray_field():
    listed = structure_ops.ALLOWLIST.never_transferred
    for name in ("members", "messages", "authors", "audit_history", "secrets", "integrations", "admins", "invite_links", "linked_chat"):
        assert name in listed
    with pytest.raises(core.BlueprintError):
        core.Allowlist(schema="cli-tools/blueprint/telegram/1", container={"name", "members"}, objects={})


# -- export -----------------------------------------------------------------


def test_export_writes_a_blueprint_inside_the_allowlist_and_prints_the_banner(run_cli, capsys, tmp_path):
    path = tmp_path / "hermes.json"
    code, out, err, fake = run_cli(["--json", "structure", "export", "--chat", "@teamhermes", "--output", str(path)], capsys=capsys)
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["command"] == "structure export"
    assert envelope["target"]["rid"] == FORUM_RID
    blueprint = json.loads(path.read_text())
    assert blueprint == envelope["result"]["blueprint"]
    assert blueprint["schema"] == "cli-tools/blueprint/telegram/1"
    assert core.validate(blueprint, structure_ops.ALLOWLIST) == []
    settings = blueprint["container"]["settings"]
    assert settings == {"about": "the agency's room", "default_banned_rights": ["send_gifs", "send_stickers"], "join_request": True, "kind": "forum", "name": "Team Hermes", "slow_mode_seconds": 30}
    assert [item["handle"] for item in blueprint["objects"]] == ["topic:deploys", "topic:dobby", "topic:general", "topic:support"], "topics are listed by title"
    assert blueprint["objects"][1]["fields"] == {"icon_emoji_id": DOBBY_ICON, "name": "Dobby"}
    for word in ("members", "messages", "authors", "audit_history", "secrets", "integrations", "admins", "invite_links", "linked_chat"):
        assert word in blueprint["never_transferred"]
    text = path.read_text()
    for leak in ("participants_count", "linked_chat_id", "username", "exported_invite", "secretinvite", "4242"):
        assert leak not in text, leak
    assert sorted(envelope["result"]["dropped"]) == ["container.linked_chat_id", "container.participants_count", "container.username"]
    assert structure_ops.NO_PERFECT_CLONE[0] in err
    assert "never transferred:" in err
    assert path.read_text() == core.dumps(blueprint), "the file is the canonical spelling"
    assert not fake.world.mutations()


def test_export_prints_the_blueprint_when_no_output_is_given(run_cli, capsys):
    code, out, _err, _fake = run_cli(["structure", "export", "--chat", str(CHANNEL_ID)], capsys=capsys)
    assert code == 0
    assert structure_ops.NO_PERFECT_CLONE[0] in out
    tail = out[out.index("{"):]
    assert json.loads(tail)["container"]["settings"]["kind"] == "channel"


def test_export_takes_a_rid_as_well_as_a_reference(run_cli, capsys, tmp_path):
    envelope, _fake = export_to(run_cli, capsys, tmp_path / "b.json", FORUM_RID)
    assert envelope["target"]["rid"] == FORUM_RID
    code, out, _err, _fake = run_cli(["--json", "structure", "export", "--chat", "tg:topic:1:2"], capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"


def test_export_of_a_basic_group_is_refused_with_the_parity_reason(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "structure", "export", "--chat", str(BASIC_ID)], capsys=capsys)
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "PLATFORM_UNSUPPORTED"
    assert "`create` makes supergroups" in error["message"]
    assert not fake.world.mutations()


# -- the P6 row: export, apply to a fresh chat, export again --------------------


def test_p6_export_apply_to_a_new_chat_export_is_byte_identical_after_normalisation(run_cli, capsys, tmp_path, home):
    source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")

    code, out, err, fake = run_cli(
        ["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--create", "--execute"],
        client=fake, capsys=capsys, isatty=True, answer="Team Hermes",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["command"] == "structure apply"
    assert envelope["status"] == "ok"
    assert envelope["plan"]["approval"] == "typed_name"
    result = envelope["result"]
    assert result["status"] == "ok" and result["executed"] is True
    assert result["readback"]["counts"] == {"add": 0, "change": 0, "remove": 0}
    new_rid = envelope["target"]["rid"]
    assert new_rid != FORUM_RID

    # What the fake now holds: a forum with the same topics, in order, icon kept,
    # General matched by handle rather than made twice; the settings applied.
    new_marked = int(new_rid.split(":")[-1])
    assert fake.world.topics_of(new_marked) == [("General", None), ("Deploys", None), ("Dobby", DOBBY_ICON), ("Support", None)], "made in blueprint order, General matched not made"
    new_chat = fake.world.chats[new_marked]
    assert (new_chat["slowmode_seconds"], new_chat["entity"].join_request, new_chat["about"]) == (30, True, "the agency's room")
    assert banned_right_names(new_chat["entity"].default_banned_rights) == ["send_gifs", "send_stickers"]
    assert fake.world.mutations().count("CreateChannelRequest") == 1
    assert fake.world.mutations().count("CreateForumTopicRequest") == 3
    assert not any(type(request).__name__ in DELETES for request in fake.world.requests)

    again, _fake = export_to(run_cli, capsys, tmp_path / "copy.json", new_rid, client=fake)
    assert core.dumps(core.normalise(again["result"]["blueprint"])) == core.dumps(core.normalise(source["result"]["blueprint"]))
    assert again["result"]["hash"] == source["result"]["hash"]

    # The remap table names every minted id under the apply id, and is offline.
    apply_id = result["apply_id"]
    code, out, _err, _fake = run_cli(["--json", "structure", "remap", "--apply-id", apply_id], capsys=capsys)
    assert code == 0
    rows = envelope_of(out)["result"]["rows"]
    assert {row["source_rid"] for row in rows} == {FORUM_RID, *(topic_rid(FORUM_ID, n) for n in (1, 141, 217, 300))}
    assert {row["target_rid"] for row in rows} == {new_rid, *(topic_rid(new_marked, n) for n in (1, 101, 201, 301))}
    assert result["remap"]["handles"]["topic:dobby"] == topic_rid(new_marked, 201)

    # One audit line for the create and one per accepted step, all typed_name.
    lines = [json.loads(line) for line in (home / ".telegram-tools" / "audit.jsonl").read_text().splitlines()]
    applies = [line for line in lines if line["command"] == "structure apply"]
    assert len(applies) == 1 + len(result["made"])
    assert all(line["approval"] == "typed_name" for line in applies)
    assert "created forum Team Hermes as" in applies[0]["evidence"]["readback"]
    assert "-> " in applies[1]["evidence"]["readback"]


def test_apply_to_an_existing_forum_makes_the_missing_topics_and_leaves_its_extras_alone(run_cli, capsys, tmp_path):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    world = fake.world
    world.add(
        world.marked_for(7777),
        _channel(7777, "Team Hermes 2", megagroup=True, forum=True),
        topics=[(1, "General", None), (55, "Deploys", None), (66, "Watercooler", None)],
    )
    target = f"tg:chat:{world.marked_for(7777)}"

    code, out, _err, fake = run_cli(["--json", "structure", "diff", "--blueprint", str(tmp_path / "hermes.json"), "--chat", target], client=fake, capsys=capsys)
    assert code == 0
    diff = envelope_of(out)["result"]
    assert diff["matches"] is False
    kinds = {(change["kind"], change["handle"], change["field"]) for change in diff["changes"]}
    assert ("add", "topic:dobby", None) in kinds and ("add", "topic:support", None) in kinds
    assert ("remove", "topic:watercooler", None) in kinds, "an extra on the target is reported, never removed"
    assert ("change", "chat:team-hermes", "name") in kinds

    code, out, _err, fake = run_cli(
        ["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--chat", target, "--execute"],
        client=fake, capsys=capsys, isatty=True, answer="team hermes 2",
    )
    assert code == 0, out
    result = envelope_of(out)["result"]
    assert result["status"] == "ok"
    assert "- topic:watercooler" in " ".join(result["extras"])
    titles = [title for title, _icon in fake.world.topics_of(world.marked_for(7777))]
    assert titles == ["General", "Deploys", "Watercooler", "Dobby", "Support"], "made what was missing, kept the extra, deleted nothing"
    assert fake.world.chats[world.marked_for(7777)]["entity"].title == "Team Hermes", "the blueprint's title is applied, and the typed title was the old one"

    code, out, _err, fake = run_cli(["--json", "structure", "diff", "--blueprint", str(tmp_path / "hermes.json"), "--chat", target], client=fake, capsys=capsys)
    after = envelope_of(out)["result"]
    assert after["counts"] == {"add": 0, "change": 0, "remove": 1}


# -- the gate -----------------------------------------------------------------


def test_apply_without_execute_is_a_dry_run_that_lists_every_step_and_changes_nothing(run_cli, capsys, tmp_path):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    code, out, err, fake = run_cli(["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--chat", str(PLAIN_ID)], client=fake, capsys=capsys)
    assert code == 2, "a forum blueprint does not apply to a plain supergroup"
    assert envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"

    code, out, err, fake = run_cli(["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--create"], client=fake, capsys=capsys)
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["status"] == "dry_run"
    result = envelope["result"]
    assert result["dry_run"] is True and result["executed"] is False and result["create"] is True
    assert [step["handle"] for step in result["steps"]] == ["topic:deploys", "topic:dobby", "topic:general", "topic:support", "chat:team-hermes"]
    assert "Dry-run. Add --execute" in err
    assert structure_ops.NO_PERFECT_CLONE[0] in err
    assert not fake.world.mutations()


def test_apply_refuses_the_wrong_title_and_makes_nothing(run_cli, capsys, tmp_path):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    code, out, _err, fake = run_cli(
        ["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--create", "--execute"],
        client=fake, capsys=capsys, isatty=True, answer="Team Hermes 3",
    )
    assert code == 1
    assert envelope_of(out)["status"] == "cancelled"
    assert not fake.world.mutations()


def test_apply_refuses_without_a_terminal_in_either_mode(run_cli, capsys, tmp_path):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    code, out, _err, fake = run_cli(
        ["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--create", "--execute"],
        client=fake, capsys=capsys, isatty=False, answer="Team Hermes",
    )
    assert code == 3
    assert envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    code, _out, err, fake = run_cli(
        ["structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--chat", "@teamhermes", "--execute"],
        client=fake, capsys=capsys, isatty=False, answer="Team Hermes",
    )
    assert code == 3, "a title piped into stdin is not a person"
    assert "confirmation" in err
    assert not fake.world.mutations()


def test_apply_has_no_yes_flag():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["structure", "apply", "--blueprint", "b.json", "--create", "--yes"])


def test_apply_refuses_when_the_chat_changed_between_the_gate_and_the_call(run_cli, capsys, tmp_path):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    world = fake.world
    world.add(world.marked_for(7777), _channel(7777, "Team Hermes 2", megagroup=True, forum=True), topics=[(1, "General", None)])

    def rename(world):
        world.chats[world.marked_for(7777)]["entity"].title = "Someone Else's Forum"

    code, out, _err, fake = run_cli(
        ["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--chat", f"tg:chat:{world.marked_for(7777)}", "--execute"],
        client=fake, capsys=capsys, isatty=True, answer="Team Hermes 2", before_answer=rename,
    )
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "PLAN_DRIFT"
    assert not fake.world.mutations()


def test_a_failed_step_stops_the_apply_and_keeps_the_partial_remap(run_cli, capsys, tmp_path):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    fake.world.fail_on = ("CreateForumTopicRequest", 2)  # Deploys is made; Dobby fails
    code, out, err, fake = run_cli(
        ["--json", "structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--create", "--execute"],
        client=fake, capsys=capsys, isatty=True, answer="Team Hermes",
    )
    assert code == 1
    envelope = envelope_of(out)
    assert envelope["status"] == "partial"
    result = envelope["result"]
    assert result["status"] == "failed"
    assert result["failed"]["handle"] == "topic:dobby"
    assert [step["handle"] for step in result["made"]] == ["topic:deploys"]
    assert result["remap"]["handles"]["topic:deploys"].startswith("tg:topic:")
    assert "failed at create topic:dobby" in err
    new_rid = envelope["target"]["rid"]
    code, out, _err, fake = run_cli(["--json", "structure", "diff", "--blueprint", str(tmp_path / "hermes.json"), "--chat", new_rid], client=fake, capsys=capsys)
    remaining = {(change["kind"], change["handle"]) for change in envelope_of(out)["result"]["changes"]}
    assert ("add", "topic:support") in remaining and ("add", "topic:dobby") in remaining
    assert ("add", "topic:deploys") not in remaining


def test_a_blueprint_outside_the_allowlist_is_refused_before_anything_is_resolved(run_cli, capsys, tmp_path):
    path = tmp_path / "bad.json"
    blueprint = {
        "schema": "cli-tools/blueprint/telegram/1",
        "container": {"handle": "chat:x", "kind": "chat", "source_rid": FORUM_RID, "settings": {"name": "X", "kind": "forum", "members": ["tg:user:1"]}},
        "objects": [],
        "never_transferred": list(structure_ops.ALLOWLIST.never_transferred),
    }
    path.write_text(json.dumps(blueprint))
    code, out, _err, fake = run_cli(["--json", "structure", "apply", "--blueprint", str(path), "--create"], capsys=capsys)
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "CONFIG_INVALID" and "members is outside the allowlist" in error["message"]
    assert fake.world.requests == []
    path.write_text("not json")
    code, out, _err, _fake = run_cli(["--json", "structure", "diff", "--blueprint", str(path), "--chat", "@teamhermes"], capsys=capsys)
    assert envelope_of(out)["error"]["code"] == "CONFIG_INVALID"


# -- the rest of the surface --------------------------------------------------


def test_remap_with_an_unknown_apply_id_is_empty_and_offline(run_cli, capsys):
    code, out, err, fake = run_cli(["--json", "structure", "remap", "--apply-id", "feedfacefeedface"], capsys=capsys)
    assert code == 0
    envelope = envelope_of(out)
    assert envelope["status"] == "empty" and envelope["result"]["rows"] == []
    assert fake.world.requests == [], "remap reads the archive and asks Telegram nothing"
    assert "No remap rows" in err


def test_structure_refuses_under_bot_mode_before_connecting(run_cli, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "alerts:123456:ABCDEFghijklmnopqrstuvwxyz0123456789")
    code, out, _err, fake = run_cli(["--json", "--as-bot", "alerts", "structure", "export", "--chat", "@teamhermes"], capsys=capsys)
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "IDENTITY_MODE_UNSUPPORTED"
    assert fake.world.requests == []


def test_human_export_and_diff_read_as_screens(run_cli, capsys, tmp_path):
    path = tmp_path / "hermes.json"
    code, out, _err, fake = run_cli(["structure", "export", "--chat", "@teamhermes", "--output", str(path)], capsys=capsys)
    assert code == 0
    assert out.startswith("Acting as: Sven (@sven) · account · Target: Team Hermes")
    assert "4 topic(s), blueprint" in out and "  topic:dobby  Dobby  icon 5350554349074391003" in out
    assert f"written to {path}" in out
    code, out, _err, _fake = run_cli(["structure", "diff", "--blueprint", str(path), "--chat", "@teamhermes"], client=fake, capsys=capsys)
    assert code == 0 and "already matches the blueprint" in out


def test_the_screens_for_an_apply_name_every_step_and_the_gate(run_cli, capsys, tmp_path, home):
    _source, fake = export_to(run_cli, capsys, tmp_path / "hermes.json", "@teamhermes")
    code, out, _err, fake = run_cli(
        ["structure", "apply", "--blueprint", str(tmp_path / "hermes.json"), "--create", "--execute"],
        client=fake, capsys=capsys, isatty=True, answer="Team Hermes",
    )
    assert code == 0, out
    assert "  1. create topic:deploys" in out
    assert "Executing: the next prompt asks for the chat's exact title." in out
    assert "readback: the chat matches the blueprint" in out
    assert "remap table: `structure remap --apply-id" in out
