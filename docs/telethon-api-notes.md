# Telethon API Notes

Checked on 2026-07-06 before implementation.

- Current stable release checked from PyPI: Telethon 1.44.0, released 2026-06-15.
- Telethon sessions are local SQLite files by default and contain enough authorization data to reuse the login. This project stores them under `~/.telegram-tools/`, outside any repository.
- `TelegramClient.iter_dialogs()` is the high-level API for listing open dialogs.
- `TelegramClient.get_permissions(entity, user)` returns `ParticipantPermissions`; `is_admin` indicates admin/creator status.
- `TelegramClient.iter_messages()` supports chat search through `search`, sender filtering through `from_user`, and thread/topic traversal through `reply_to`.
- Telethon documents that `search` and `filter` have no effect with `reply_to`, so topic-scoped keyword search is implemented by iterating the topic and filtering locally.
- `TelegramClient.delete_messages(entity, message_ids)` chunks IDs internally, but it does not validate that message IDs belong to the passed chat. This project only deletes IDs collected from the selected chat/topic in the same process.
- Forum topic listing requires raw API support. In the Telethon 1.44.0 wheel, the relevant request class is `telethon.tl.functions.messages.GetForumTopicsRequest`.

Checked on 2026-08-13 for the `bots` command (Telethon 1.44.0):

- `bots.getAdminedBots` returns the bots the logged-in user owns; it is the API behind @BotFather's `/mybots`.
- `users.getFullUser` on a bot returns everything the profile view needs: `about` (the bio), `bot_info.description`, `bot_info.commands`, `bot_group_admin_rights`, and `bot_broadcast_admin_rights`. One request, and it works for bots the user does not own.
- `bots.setBotInfo` takes a `bot` parameter, so the owner's session can set `name`, `about`, and `description` without a bot token. `bots.getBotInfo` also exists but is not used — `getFullUser` covers the same fields in one call.
- `photos.uploadProfilePhoto` also takes a `bot` parameter, so setting a bot's photo needs no token.
- `bots.setBotCommands`, `bots.resetBotCommands`, `bots.setBotGroupDefaultAdminRights`, and `bots.setBotBroadcastDefaultAdminRights` have **no** `bot` parameter — they act on the caller, so they must be sent by a client authorized with the bot's token.
- `photos.deletePhotos` needs an `InputPhoto` with an access hash, which `UserProfilePhoto` does not carry; fetch the photo through `photos.getUserPhotos` and convert it with `telethon.utils.get_input_photo`.
- `bots.exportBotToken` exists and would let an owner read a bot's token. This project never calls it: it sits next to a `revoke` flag, and reading credentials is outside what the tool does.
- Changing a bot's `@username`, creating a bot, and deleting a bot have no user-facing API and remain @BotFather-only.
