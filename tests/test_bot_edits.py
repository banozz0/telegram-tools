import asyncio
from types import SimpleNamespace

from telegram_tools.bots import (
    apply_owner_edits,
    build_edit_plan,
    confirm_bot_edits,
    format_edit_plan,
    parse_rights,
)
from telegram_tools.models import BotCommandInfo, BotInfo


def current_bot(**overrides):
    defaults = dict(
        id=12345,
        username="harrybot",
        name="Harry",
        bio="Assistant",
        description="Does things",
        is_owned=True,
        has_photo=True,
        commands=[BotCommandInfo(command="start", description="Start the bot")],
        group_rights=["delete_messages"],
        channel_rights=[],
    )
    defaults.update(overrides)
    return BotInfo(**defaults)


class RecordingClient:
    def __init__(self):
        self.requests = []
        self.uploaded = []

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace()

    async def upload_file(self, path):
        self.uploaded.append(path)
        return SimpleNamespace(name=str(path))


def test_build_edit_plan_keeps_only_real_changes():
    plan = build_edit_plan(current_bot(), {"name": "Harry", "bio": "New bio"})

    assert [change.field for change in plan.changes] == ["bio"]
    assert plan.skipped == ["name"]


def test_build_edit_plan_splits_rails():
    plan = build_edit_plan(
        current_bot(),
        {"name": "Harry Two", "group_rights": parse_rights("ban_users")},
    )

    assert [change.field for change in plan.owner_changes] == ["name"]
    assert [change.field for change in plan.bot_changes] == ["group_rights"]


def test_build_edit_plan_skips_commands_that_already_match():
    plan = build_edit_plan(
        current_bot(),
        {"commands": [BotCommandInfo(command="start", description="Start the bot")]},
    )

    assert plan.is_empty is True
    assert plan.skipped == ["commands"]


def test_build_edit_plan_skips_clearing_when_there_is_nothing_to_clear():
    plan = build_edit_plan(current_bot(commands=[]), {"clear_commands": True})

    assert plan.is_empty is True


def test_build_edit_plan_skips_removing_a_photo_that_does_not_exist():
    plan = build_edit_plan(current_bot(has_photo=False), {"remove_photo": True})

    assert plan.is_empty is True


def test_build_edit_plan_always_treats_a_new_photo_as_a_change():
    plan = build_edit_plan(current_bot(), {"photo": "face.png"})

    assert [change.field for change in plan.changes] == ["photo"]


def test_build_edit_plan_skips_rights_that_already_match():
    plan = build_edit_plan(current_bot(), {"group_rights": parse_rights("delete_messages")})

    assert plan.is_empty is True


def test_build_edit_plan_treats_clearing_a_set_field_as_a_change():
    plan = build_edit_plan(current_bot(), {"bio": ""})

    assert [change.field for change in plan.changes] == ["bio"]
    assert plan.skipped == []


def test_build_edit_plan_skips_clearing_a_field_that_is_already_unset():
    plan = build_edit_plan(current_bot(bio=None), {"bio": ""})

    assert plan.is_empty is True


def test_format_edit_plan_shows_old_and_new_and_truncates_long_text():
    plan = build_edit_plan(current_bot(), {"description": "x" * 200})

    output = format_edit_plan(plan)

    assert "description" in output
    assert "Does things" in output
    assert "..." in output


def test_confirm_bot_edits_requires_a_y():
    plan = build_edit_plan(current_bot(), {"bio": "New bio"})
    written = []

    assert confirm_bot_edits(plan, read=lambda _prompt: "n", write=written.append) is False
    assert confirm_bot_edits(plan, read=lambda _prompt: "Y", write=written.append) is True


def test_apply_owner_edits_sends_one_set_bot_info_request_for_text_fields():
    plan = build_edit_plan(current_bot(), {"name": "Harry Two", "bio": "New bio"})
    client = RecordingClient()
    input_user = SimpleNamespace(user_id=12345)

    applied = asyncio.run(apply_owner_edits(client, input_user, plan.owner_changes))

    assert applied == ["bio", "name"]
    assert len(client.requests) == 1
    request = client.requests[0]
    assert type(request).__name__ == "SetBotInfoRequest"
    assert request.name == "Harry Two"
    assert request.about == "New bio"
    assert request.description is None


def test_apply_owner_edits_uploads_and_sets_a_photo():
    plan = build_edit_plan(current_bot(), {"photo": "face.png"})
    client = RecordingClient()

    applied = asyncio.run(apply_owner_edits(client, SimpleNamespace(user_id=12345), plan.owner_changes))

    assert applied == ["photo"]
    assert client.uploaded == ["face.png"]
    assert type(client.requests[0]).__name__ == "UploadProfilePhotoRequest"
