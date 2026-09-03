"""Machine output: one envelope per run, stable errors, and where words go.

Human mode is this tool as it has always been: tables, progress and payloads
on stdout, and the exit code the command returns. Under `--json` stdout
carries exactly one envelope and nothing else, so everything a person would
read moves to stderr, prompts read from the controlling terminal, and the
payload becomes the envelope's `result` with every key it printed before.

The envelope, the error codes and the exit table are not this tool's to
invent: they come from the shared copy in `_core/`, which the sibling carries
byte-for-byte, so one reader parses both.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Iterable, Sequence

from telegram_tools import __version__
from telegram_tools._core.contract import (
    Error,
    Meta,
    build_envelope,
    dumps,
    exit_code,
    jsonl_line,
    utc_now,
)
from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.plan import Evidence, Plan

TOOL = "telegram-tools"
PLATFORM = "telegram"
# The rid prefix for everything this tool resolves: `tg:chat:-100…`.
PREFIX = "tg"

# Flags that select the output mode rather than describe the job; they are the
# one thing an echoed `args` leaves out.
MODE_FLAGS = ("command", "create_kind", "delete_kind", "json_envelope", "jsonl")

# `refused` is the tool declining -- usage, config, permission, a gate. `failed`
# is the run breaking on something outside it. Both exit 2; the distinction is
# for the reader, and it decides whether retrying could ever help.
BROKE = ("PLATFORM_ERROR", "RATE_LIMITED", "INTERRUPTED")


class CommandError(ValueError):
    """A refusal an agent can key on: a stable code, and a hint that fixes it.

    Deliberately a `ValueError`, which is what every one of these sites raised
    before the codes existed, so human mode keeps printing the same message and
    exiting 2 without knowing this class is here.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        hint: str | None = None,
        retryable: bool = False,
        platform: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.retryable = retryable
        self.platform = platform

    def as_error(self) -> Error:
        return Error(
            code=self.code,
            message=str(self),
            hint=self.hint,
            retryable=self.retryable,
            platform=self.platform,
        )


class ApprovalRequired(CommandError):
    """A gate wanted a human and there is no terminal to ask on.

    Only reachable under `--json`: the hint is the same command without it, so
    the person reading the agent's output can run the gate themselves.
    """

    def __init__(self, human_command: str) -> None:
        super().__init__(
            "This command asks for confirmation and there is no terminal to ask on.",
            code="APPROVAL_REQUIRED",
            hint=human_command,
        )


def error_for(exc: BaseException) -> Error | None:
    """The envelope error for `exc`, or None when the contract has no code for it.

    None means the caller falls back to what this tool has always done with
    that exception: argparse prints the message on stderr and exits 2. The one
    class of failure with no code today is a usage mistake argparse cannot
    express -- `send` naming neither text nor a file, `--output` missing for a
    CSV export, `create` with no kind. Those keep their old handling rather
    than borrow a code that means something else.
    """
    if isinstance(exc, CommandError):
        return exc.as_error()
    code = getattr(exc, "envelope_code", None)
    if code:
        return Error(code=code, message=str(exc), hint=getattr(exc, "envelope_hint", None))
    if isinstance(exc, KeyboardInterrupt):
        return Error(code="INTERRUPTED", message="Interrupted.")
    if isinstance(exc, EOFError):
        return Error(code="INTERRUPTED", message="Input ended.")
    if isinstance(exc, PermissionError):
        return Error(code="PERMISSION_DENIED", message=str(exc))
    return None


def platform_error(exc: BaseException) -> Error:
    """A Telethon failure as an envelope error: the class name, never its body."""
    name = type(exc).__name__
    if name == "FloodWaitError":
        seconds = int(getattr(exc, "seconds", 0))
        return Error(
            code="RATE_LIMITED",
            message=f"Telegram asked for a {seconds}s wait before this call may be retried.",
            hint=f"Wait {seconds}s and run it again, or ask a narrower question.",
            retryable=True,
            platform=name,
        )
    return Error(code="PLATFORM_ERROR", message=str(exc), platform=name)


def human_command(argv: Sequence[str]) -> str:
    """The same command without the flags that made it machine-readable."""
    kept = [word for word in argv if word not in ("--json", "--jsonl")]
    return " ".join([TOOL, *kept])


def echoed_args(args: Any) -> dict[str, Any]:
    """The parsed flags as the envelope echoes them: what was asked, minus how to print it."""
    return {
        name: value
        for name, value in sorted(vars(args).items())
        if name not in MODE_FLAGS and value is not None and value is not False
    }


class Reporter:
    """One command's run: where its words go, what it reports, what it records.

    A human reporter prints and returns; a machine one collects and emits one
    envelope at the end. Every command talks to this object rather than to
    `print`, so the mode is decided once, in `main`, and the menu -- which
    builds its own namespaces and never passes one -- keeps the human default.
    """

    def __init__(
        self,
        *,
        machine: bool = False,
        jsonl: bool = False,
        command: str = "",
        args: Any = None,
        argv: Sequence[str] = (),
        audit: Any = None,
        stdout: Any = None,
        stderr: Any = None,
    ) -> None:
        self.machine = machine or jsonl
        self.jsonl = jsonl
        self.command = command
        self.args = echoed_args(args) if args is not None else {}
        self.human_command = human_command(argv)
        self.audit_log = audit
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        self._started = utc_now()
        self._clock = time.monotonic()

        self.me: Any = None
        self.acting: Identity | None = None
        self._target: Target | None = None
        self._plan: Plan | None = None
        self._evidence: Evidence | None = None
        self._result: dict[str, Any] = {}
        self._status = "ok"
        self._warnings: list[str] = []

    # -- what a person reads ------------------------------------------------

    @property
    def _words(self) -> Any:
        """Where a table, a preview or a progress line goes: stdout, unless an envelope owns it."""
        return self._stderr if self.machine else self._stdout

    def info(self, text: Any = "") -> None:
        print(text, file=self._words)

    @property
    def write(self) -> Callable[[Any], None]:
        """A `write` a prompt or a preview can be handed."""
        return self.info

    # -- what a machine reads -----------------------------------------------

    def result(self, payload: dict[str, Any], *, status: str = "ok") -> None:
        """The command's own payload and how its run went. Prints nothing."""
        self._result = dict(payload)
        self._status = status

    def printed_result(self, payload: dict[str, Any], *, status: str = "ok") -> None:
        """The same, for the commands whose human output has always been this JSON."""
        self.result(payload, status=status)
        if not self.machine:
            from telegram_tools.exporters import json_text

            print(json_text(payload), file=self._stdout)

    def record(self, row: Any) -> None:
        """One streamed record; only `--jsonl` has anywhere to put it."""
        if self.jsonl:
            from telegram_tools.exporters import json_line

            print(json_line(row), file=self._stdout)

    def set_identity(self, identity: Identity, *, me: Any = None) -> None:
        self.acting = identity
        if me is not None:
            self.me = me

    def set_target(self, target: Target) -> None:
        self._target = target

    def set_plan(self, plan: Plan) -> None:
        self._plan = plan

    def set_evidence(self, evidence: Evidence) -> None:
        self._evidence = evidence

    def warn(self, text: str) -> None:
        if text not in self._warnings:
            self._warnings.append(text)

    # -- gates --------------------------------------------------------------

    def confirm_io(self) -> dict[str, Callable[..., Any]]:
        """The `read`/`write` a gate's prompt should use, or a refusal it cannot be asked.

        Human mode hands back nothing and the prompt keeps its own defaults.
        Under `--json` the question goes to stderr so stdout stays one
        envelope, and with no terminal to ask on the run refuses rather than
        blocking on an `input()` nobody will answer.
        """
        if not self.machine:
            return {}
        if not sys.stdin.isatty():
            raise ApprovalRequired(self.human_command)
        return {"read": self._ask, "write": self.info}

    def _ask(self, question: str) -> str:
        print(question, end="", file=self._stderr, flush=True)
        return sys.stdin.readline().rstrip("\n")

    # -- what is left behind ------------------------------------------------

    def audit(self, plan: Plan, *, status: str, evidence: Evidence | None) -> None:
        """One line per executed write, wherever this run's audit log lives.

        A run with no log -- a direct call in a test -- records nothing; every
        run that came through `main` or the menu has one.
        """
        if self.audit_log is None:
            return
        # A first write on a fresh machine can land before anything has made
        # ~/.telegram-tools/, and the shared writer opens the file rather than
        # creating a tree. Make room for the line, tighten nothing that exists.
        self.audit_log.path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_log.append(
            tool=TOOL,
            version=__version__,
            identity=plan.identity,
            command=plan.command,
            targets=plan.targets,
            plan_id=plan.plan_id,
            approval=plan.approval,
            status=status,
            evidence=evidence,
        )

    # -- the end ------------------------------------------------------------

    def _meta(self) -> Meta:
        # api_calls and waited_ms have no counter yet: the client seam that
        # could count them is owned by a later card, and a made-up number is
        # worse than a zero the documentation admits to.
        return Meta(started=self._started, duration_ms=int((time.monotonic() - self._clock) * 1000))

    def envelope(self, *, status: str | None = None, error: Error | None = None) -> dict[str, Any]:
        return build_envelope(
            tool=TOOL,
            version=__version__,
            command=self.command,
            status=status or self._status,
            args=self.args,
            identity=self.acting,
            target=self._target,
            result={} if error is not None else self._result,
            plan=self._plan,
            evidence=self._evidence,
            warnings=self._warnings,
            error=error,
            meta=self._meta(),
        )

    def finish(self, code: int) -> int:
        """Close the run: human mode keeps the command's own exit code, machine mode emits."""
        if not self.machine:
            return code
        envelope = self.envelope()
        print(jsonl_line(envelope) if self.jsonl else dumps(envelope, indent=2), file=self._stdout)
        return exit_code(self._status)

    def failed(self, error: Error) -> int:
        """The run refused or broke: one envelope carrying the error, and its exit code."""
        status = "failed" if error.code in BROKE else "refused"
        envelope = self.envelope(status=status, error=error)
        print(jsonl_line(envelope) if self.jsonl else dumps(envelope, indent=2), file=self._stdout)
        return exit_code(status, error.code)
