import asyncio
import sqlite3

import pytest

from telegram_tools.client import SessionInUseError, start_client


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.started = False

    async def start(self):
        if self.error is not None:
            raise self.error
        self.started = True


def test_start_client_starts_a_healthy_client():
    client = FakeClient()

    assert asyncio.run(start_client(client)) is client
    assert client.started is True


def test_a_locked_session_becomes_a_readable_error():
    client = FakeClient(sqlite3.OperationalError("database is locked"))

    with pytest.raises(SessionInUseError) as excinfo:
        asyncio.run(start_client(client))

    message = str(excinfo.value)
    # It must name the cause a human can act on, not the SQLite wording.
    assert "another" in message.lower()
    assert "menu" in message.lower()
    assert "database is locked" not in message


def test_other_sqlite_errors_are_not_swallowed():
    client = FakeClient(sqlite3.OperationalError("no such table: sessions"))

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        asyncio.run(start_client(client))

    assert not isinstance(excinfo.value, SessionInUseError)


def test_the_error_is_a_menu_error_so_the_menu_survives_it():
    from telegram_tools.menu import MENU_ERRORS

    assert isinstance(SessionInUseError("x"), MENU_ERRORS)


def test_a_locked_session_disconnects_before_raising():
    class ConnectedClient(FakeClient):
        def __init__(self):
            super().__init__(sqlite3.OperationalError("database is locked"))
            self.disconnected = False

        async def disconnect(self):
            self.disconnected = True

    client = ConnectedClient()
    with pytest.raises(SessionInUseError):
        asyncio.run(start_client(client))

    # Otherwise the clean message lands under a wall of pending-task warnings.
    assert client.disconnected is True


def test_a_teardown_failure_does_not_mask_the_real_error():
    class BadTeardown(FakeClient):
        def __init__(self):
            super().__init__(sqlite3.OperationalError("database is locked"))

        async def disconnect(self):
            raise OSError("socket already gone")

    with pytest.raises(SessionInUseError):
        asyncio.run(start_client(BadTeardown()))


def test_other_errors_also_disconnect_before_propagating():
    class ConnectedClient(FakeClient):
        def __init__(self):
            super().__init__(sqlite3.OperationalError("no such table: sessions"))
            self.disconnected = False

        async def disconnect(self):
            self.disconnected = True

    client = ConnectedClient()
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(start_client(client))

    assert client.disconnected is True
