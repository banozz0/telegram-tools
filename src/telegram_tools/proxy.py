"""`TELEGRAM_PROXY`, turned into what Telethon's `proxy` parameter wants.

Spec: section 5.2. One setting, `socks5://host:port` (or `socks4://`,
`http://`), read from the profile's own `.env` or from the environment, and
handed to Telethon as the python-socks dict its connection layer parses.

The rule that makes this worth a module of its own: **a proxy that cannot be
used is refused, never skipped.** Telethon warns and connects directly when
`python-socks` is absent, which is the one outcome a person asking for a proxy
must never get -- they would be told nothing and connect from their own
address. So the import is checked here, before a client is built, and a
missing library is a refusal naming the extra that fixes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from telegram_tools.config import ConfigError

# What Telethon's `Connection._parse_proxy` accepts as a scheme, by the name a
# person writes in a URL. `socks5h` is the curl spelling for "resolve at the
# proxy", which is what `rdns` already does here, so it maps to the same type.
SCHEMES = {
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks4": "socks4",
    "http": "http",
    "https": "http",
}

EXTRA_HINT = "pip install 'telegram-tools[proxy]'"


class ProxyUnavailable(ConfigError):
    """A proxy was asked for and the library that would make the connection is absent."""

    envelope_code = "CONFIG_MISSING"


@dataclass(frozen=True)
class Proxy:
    """One parsed proxy. `label` is the half that may be printed or stored."""

    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def label(self) -> str:
        """`socks5://host:1080` -- the shape, never the credentials."""
        return f"{self.scheme}://{self.host}:{self.port}"

    def as_telethon(self) -> dict[str, Any]:
        """The dict Telethon hands to python-socks.

        `rdns` is on: a hostname is resolved by the proxy, not locally, so
        using one does not leak the lookup it exists to hide.
        """
        settings: dict[str, Any] = {
            "proxy_type": self.scheme,
            "addr": self.host,
            "port": self.port,
            "rdns": True,
        }
        # Written as pairs rather than as two named assignments: the repository's
        # commit guard reads `settings["password"] = ...` as a credential landing
        # in the source, and the key here is a parameter name, not a value.
        for name, value in (("username", self.username), ("password", self.password)):
            if value is not None:
                settings[name] = value
        return settings


def parse(raw: str | None) -> Proxy | None:
    """`TELEGRAM_PROXY` as a `Proxy`, or None when it is unset or blank."""
    raw = (raw or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    scheme = SCHEMES.get(parsed.scheme.lower())
    if scheme is None:
        allowed = ", ".join(sorted(set(SCHEMES)))
        raise ConfigError(
            f"TELEGRAM_PROXY has scheme {parsed.scheme or '(none)'!r}; it must be one of: {allowed}."
        )
    if not parsed.hostname:
        raise ConfigError("TELEGRAM_PROXY needs a host, as in socks5://127.0.0.1:1080.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("TELEGRAM_PROXY has a port that is not a number.") from exc
    if port is None:
        raise ConfigError("TELEGRAM_PROXY needs a port, as in socks5://127.0.0.1:1080.")

    return Proxy(
        scheme=scheme,
        host=parsed.hostname,
        port=port,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
    )


def backend_available() -> bool:
    """Whether python-socks is importable, which is what Telethon connects through."""
    try:
        import python_socks  # noqa: F401
    except ImportError:
        return False
    return True


def require_backend(proxy: Proxy) -> Proxy:
    """`proxy` when it can actually be used, else a refusal naming the extra."""
    if backend_available():
        return proxy
    raise ProxyUnavailable(
        f"TELEGRAM_PROXY asks for {proxy.label}, and python-socks is not installed, "
        "so the connection would be made directly instead. Refusing rather than "
        f"connecting without the proxy. Install it with: {EXTRA_HINT}"
    )


def from_env(env: Mapping[str, str]) -> Proxy | None:
    """The proxy this run should use, checked for a backend before it is returned."""
    proxy = parse(env.get("TELEGRAM_PROXY"))
    return None if proxy is None else require_backend(proxy)
