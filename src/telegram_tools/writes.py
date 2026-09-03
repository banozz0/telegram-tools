"""What every write does around the moment it touches Telegram.

Four things, in this order and on every path that changes something:

* a **plan** -- who is acting, on what, doing which mutations, behind which
  gate -- built before anything is asked, so a dry-run prints the same object
  the real run will execute;
* a **preflight** naming the rights the plan needs against the rights the
  account holds, which refuses by name rather than letting Telegram refuse by
  error;
* a **re-derivation** once the gate has been answered and before the call goes
  out, because the window between "you read the title" and "it happened" is
  exactly where the wrong chat gets deleted;
* a **readback** afterwards, and an audit line, so what happened is recorded
  rather than assumed. A readback that cannot be fetched says so; it is never
  reported as verified.

None of this is mode-dependent. The menu writes an audit line too, and a
preflight that would refuse a `--json` run refuses the same run from a menu.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence

from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.plan import Evidence, Mutation, Plan, Preflight, drift
from telegram_tools.adapters import Rights
from telegram_tools.envelope import TOOL, CommandError

from telegram_tools import __version__


def build_plan(
    *,
    identity: Identity,
    command: str,
    targets: Sequence[Target],
    mutations: Sequence[Mutation],
    approval: str,
    rights: Rights,
    required: Sequence[str],
) -> tuple[Plan, tuple[str, ...]]:
    """The plan for a write, and the warnings its preflight could not settle.

    `held` carries only what Telegram actually reported, so a right it would
    not answer for shows as missing with a warning that says why, rather than
    being quietly counted as held.
    """
    plan = Plan(
        tool=TOOL,
        version=__version__,
        identity=identity,
        command=command,
        targets=tuple(targets),
        mutations=tuple(mutations),
        approval=approval,
        preflight=Preflight(required=tuple(required), held=tuple(sorted(rights.held))),
    )
    unknown = rights.unknown(required)
    warnings: list[str] = []
    if unknown:
        why = rights.unreadable or "Telegram reports no permissions for this chat"
        warnings.append(
            f"preflight could not confirm {', '.join(unknown)}: {why}. The write was attempted anyway."
        )
    return plan, tuple(warnings)


def require_rights(plan: Plan, rights: Rights, required: Sequence[str]) -> None:
    """Refuse before any mutation when Telegram says a needed right is absent.

    Only a right Telegram answered for can refuse a run. A right it would not
    answer for is unknown, and this tool has always let those writes through to
    Telegram's own error -- narrowing that would break sends that work today.
    """
    missing = rights.missing(required)
    if not missing:
        return
    names = ", ".join(missing)
    target = plan.targets[0].title if plan.targets else "this chat"
    raise CommandError(
        f"Your Telegram account lacks {names} in {target}.",
        code="PERMISSION_DENIED",
        hint=f"Ask an admin of {target} for {names}, or run this as an account that has it.",
    )


def rederive(shown: Plan, now: Plan) -> None:
    """Refuse when the thing about to be changed is no longer the thing that was approved."""
    differences = drift(shown, now)
    if differences:
        raise CommandError(
            "The target changed between the preview and the execution: " + "; ".join(differences) + ".",
            code="PLAN_DRIFT",
            hint="Run it again: the preview will show what it is now.",
        )


def recheck_for(
    shown: Plan,
    rebuild: Callable[[], Awaitable[Plan]],
) -> Callable[[], Awaitable[None]]:
    """The callback a write runs once its gate is answered, just before it fires."""

    async def check() -> None:
        rederive(shown, await rebuild())

    return check


async def read_back(what: str, fetch: Callable[[], Awaitable[str]]) -> Evidence:
    """Fetch the resulting state and say it, or say plainly that it could not be read."""
    try:
        return Evidence.verified(await fetch())
    except Exception as exc:  # noqa: BLE001 - a readback that fails is evidence, not a crash
        return Evidence.unverified(f"{what} could not be read back ({type(exc).__name__})")
