import pytest

from gmail_mcp.access import AccessLevel
from gmail_mcp.auth import GmailCredentials
from gmail_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        credentials=GmailCredentials(
            client_id="fake-client-id",
            client_secret="fake-client-secret",
            refresh_token="fake-refresh-token",
        ),
        access_level=AccessLevel.READONLY,
    )


def at_level(settings: Settings, level: AccessLevel) -> Settings:
    return Settings(credentials=settings.credentials, access_level=level)
