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

Checked on 2026-09-06 for the `message` verbs (Telethon 1.44.0):

- `TelegramClient.edit_message(entity, message_id, text)`, `delete_messages(entity, ids, revoke=True)`, `forward_messages(to, ids, from_peer=...)`, `pin_message` / `unpin_message(entity, id)` and `send_read_acknowledge(entity)` cover reply, edit, delete, forward, pin, unpin and read. `forward_messages` has no topic parameter; a forward into a topic goes through the raw `messages.ForwardMessagesRequest` with `top_msg_id`, whose `Updates` carry the new messages.
- A reply is `send_message(entity, text, reply_to=<message id>)`; Telegram threads it into that message's topic itself, so a reply needs no topic id.
- Reactions are `messages.SendReactionRequest(peer, msg_id, reaction=[ReactionEmoji(emoticon)])`; an empty list removes the caller's reactions. A message's `reactions.results[].chosen_order` is set on the ones the caller placed, which is what the readback checks.
- A poll is `send_message(entity, file=InputMediaPoll(Poll(id=0, hash=0, question=TextWithEntities(...), answers=[PollAnswer(TextWithEntities(...), option=bytes)])))`; `id` and `hash` are zero on a new poll and Telegram assigns them.
- `client.action(entity, "typing")` is an async context manager that keeps the status alive until it exits. There is nothing to read back afterwards.
- `messages.MarkDialogUnreadRequest(peer=InputDialogPeer(peer), unread=True)` marks unread; `messages.SaveDraftRequest(peer, message, reply_to=InputReplyToMessage(reply_to_msg_id=topic, top_msg_id=topic))` saves a draft in a topic. `messages.GetPeerDialogsRequest(peers=[InputDialogPeer(peer)])` returns `dialogs[0]` with `unread_count`, `unread_mark` and `draft`, which is the one readback for read, unread and draft.
- Saved Messages is the entity `"me"`; `forward_messages("me", ids, from_peer=...)` is a bookmark.
- `MessageEntityUrl(offset, length)` marks a URL typed into the text and `MessageEntityTextUrl(offset, length, url)` text linked to one; the offsets count UTF-16 code units, not Python characters, so an emoji before the link shifts them by two. `message.get_entities_text()` handles that on a real `Message`; this project slices the text as UTF-16 itself so a fake message in a test needs no Telethon class.
- A message's file is `message.media.document` (`Document`: `id`, `mime_type`, `size`, a `DocumentAttributeFilename` among `attributes`) or `message.media.photo` (`Photo`: `sizes`, each with a byte `size`; the largest is what a download serves, and every one is a JPEG). `MessageMediaWebPage` is a link preview, not a file.
- `TelegramClient.iter_download(media, offset=N)` yields the bytes of a file from byte `N`; an offset that is not a multiple of the request size costs Telethon extra work but is honoured, which is what makes a killed download resumable from the bytes on disk. The media object comes from a fresh `get_messages(peer, ids=id)`, because file references expire and a message's file can be replaced.

Checked on 2026-09-06 for the `structure` commands (Telethon 1.44.0):

- `channels.GetFullChannelRequest(channel)` returns `messages.ChatFull` whose `full_chat` (`ChannelFull`) carries `about`, `slowmode_seconds`, `linked_chat_id`, `participants_count` and an `exported_invite`, and whose `chats[0]` is the `Channel` itself with the `forum`, `join_request`, `megagroup`, `broadcast` flags and `default_banned_rights` (`ChatBannedRights`, or `None` on a broadcast channel). One request covers every container setting a blueprint carries; the invite is never copied into the raw shape.
- `ChatBannedRights` spells 22 rights plus `until_date`; `ChatAdminRights` spells 17. Both tables are read off the constructors' annotations at import, so a right Telegram adds shows up in `banned_right_names` without a code change, and a right a blueprint names that Telegram does not spell is refused.
- The edit calls: `channels.EditTitleRequest(channel, title)`, `messages.EditChatAboutRequest(peer, about)`, `messages.EditChatDefaultBannedRightsRequest(peer, banned_rights)`, `channels.ToggleSlowModeRequest(channel, seconds)` (0 switches it off), `channels.ToggleJoinRequestRequest(channel, enabled)`, `channels.EditAdminRequest(channel, user_id, admin_rights, rank)`. `messages.EditForumTopicRequest(peer, topic_id, title=, icon_emoji_id=, closed=, hidden=)` takes only the fields to change; `icon_emoji_id=0` removes the icon, where `None` means leave it. `messages.CreateForumTopicRequest` takes `icon_emoji_id` at creation.
- A `ForumTopic` carries `icon_emoji_id` (the custom-emoji document id) beside `title`; the character drawn in front of the title is a second lookup (`resolve_icon_emoji`). A blueprint carries the id, because the character is only its rendering and the id is what `createForumTopic` takes.
- Telegram has no topic order a client can set: the app sorts topics by last activity with pinned ones first, and `ForumTopic` has no position field. A blueprint therefore lists topics by title.
- Admin rights exist only on a participant (`ChannelParticipantAdmin.admin_rights` and `rank`); there is no chat-level template of rights sets. That is why a Telegram blueprint carries none and `admins` is on its never-transferred list, while the primitive that sets one person's rights lives in the blueprint port for the administration commands.
