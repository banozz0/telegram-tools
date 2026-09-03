"""The adapters: the one place platform behaviour becomes the shared vocabulary.

Everything above these classes works in `_core` terms -- an `Identity`, a
`Target`, a set of rights -- and knows nothing about entities, peers or
dialogs. The classes themselves sit directly on top of this tool's one SDK
seam, so the tests that mock a client keep mocking exactly what they mocked
before.

`AccountIdentity` is the account mode: signed in as the person, which is what
every command does today. A bot mode is a later card and lands beside it.
"""

from telegram_tools.adapters.account import AccountIdentity, ChatPermissions, ChatTargets, Rights

__all__ = ["AccountIdentity", "ChatPermissions", "ChatTargets", "Rights"]
