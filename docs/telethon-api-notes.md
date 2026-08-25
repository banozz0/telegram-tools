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

Checked on 2026-08-25 for the `send` and `create` commands (Telethon 1.44.0):

- `TelegramClient.send_message(entity, message, reply_to=...)` posts into a forum topic by passing the topic id as `reply_to` — a topic *is* its root message thread, so there is no separate topic parameter.
- `channels.CreateChannelRequest` covers both a supergroup (`megagroup=True`) and a broadcast channel (`broadcast=True`), and takes a `forum` flag. Passing `forum=True` at creation avoids a second `channels.ToggleForumRequest` round trip, and with it the window where a group exists but the toggle failed.
- `messages.CreateForumTopicRequest` lives under `messages`, not `channels` (unlike `ToggleForumRequest`). Its `random_id` is auto-generated when omitted.
- Neither create request returns the created object directly. `CreateChannelRequest` returns `Updates` whose `chats[0]` is the new channel — `telethon.utils.get_peer_id` converts it to the marked `-100…` form. `CreateForumTopicRequest` returns `Updates` carrying only the topic's service message; that message's `id` **is** the new topic id.

Checked on 2026-08-25 for `send --file` and the session lock (Telethon 1.44.0):

- `TelegramClient.send_file(entity, file, caption=..., reply_to=...)` accepts a list for `file` and groups it into a single album; passing a one-item list is the same as passing the item, so the caller never has to special-case one attachment. It returns a list of messages for a list and a single message otherwise.
- The SQLite session raises a bare `sqlite3.OperationalError("database is locked")` when a second client opens a session file another already holds — no Telethon-specific exception wraps it. Matching on that wording is the only way to tell "someone else has it" from a corrupt database, so the check is narrow on purpose and any other `OperationalError` still propagates.
