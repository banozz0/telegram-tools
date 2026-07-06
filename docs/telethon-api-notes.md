# Telethon API Notes

Checked on 2026-07-06 before implementation.

- Current stable release checked from PyPI: Telethon 1.44.0, released 2026-06-15.
- Telethon sessions are local SQLite files by default and contain enough authorization data to reuse the login. This project stores them under `.telegram-tools/` and gitignores them.
- `TelegramClient.iter_dialogs()` is the high-level API for listing open dialogs.
- `TelegramClient.get_permissions(entity, user)` returns `ParticipantPermissions`; `is_admin` indicates admin/creator status.
- `TelegramClient.iter_messages()` supports chat search through `search`, sender filtering through `from_user`, and thread/topic traversal through `reply_to`.
- Telethon documents that `search` and `filter` have no effect with `reply_to`, so topic-scoped keyword search is implemented by iterating the topic and filtering locally.
- `TelegramClient.delete_messages(entity, message_ids)` chunks IDs internally, but it does not validate that message IDs belong to the passed chat. This project only deletes IDs collected from the selected chat/topic in the same process.
- Forum topic listing requires raw API support. In the Telethon 1.44.0 wheel, the relevant request class is `telethon.tl.functions.messages.GetForumTopicsRequest`.
