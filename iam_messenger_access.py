"""Pure remote Messenger authorization foundation; no network or runtime dispatch.

Grants and principals MUST come from trusted local storage and authenticated
transport respectively, never from caller-controlled request JSON. This module
does not make full-access agent execution safe for remote input.
"""
from dataclasses import dataclass
from enum import Enum


class AccessDenied(ValueError):
    pass


class Operation(str, Enum):
    HISTORY = 'history'
    SEND = 'send'
    ATTACHMENT = 'attachment'
    EVENTS = 'events'


@dataclass(frozen=True)
class Principal:
    home_server: str
    account_id: str
    device_id: str
    credential_kind: str


@dataclass(frozen=True)
class OwnerGrant:
    home_server: str
    account_id: str
    device_id: str
    installation_id: str
    conversation_ids: frozenset[str]
    expires_at: int
    history_from: int
    revoked: bool = False
    owner_key: str | None = None


def authorize(*, enabled: bool, principal: Principal | None,
              grant: OwnerGrant | None, installation_id: str,
              conversation_id: str, operation: str, now: int) -> OwnerGrant:
    """Recheck at every read, event, download and dispatch; fail closed."""
    if enabled is not True or principal is None or grant is None:
        raise AccessDenied('Remote Messenger access unavailable')
    if operation not in {item.value for item in Operation}:
        raise AccessDenied('Operation not permitted')
    if principal.credential_kind != 'messenger_device':
        raise AccessDenied('A paired Messenger device is required')
    actual = (principal.home_server, principal.account_id, principal.device_id)
    expected = (grant.home_server, grant.account_id, grant.device_id)
    if any(not isinstance(value, str) or not value for value in actual + expected):
        raise AccessDenied('Invalid identity')
    if actual != expected or not installation_id or installation_id != grant.installation_id:
        raise AccessDenied('Pairing does not match this identity and installation')
    if grant.revoked or now >= grant.expires_at:
        raise AccessDenied('Pairing expired or revoked')
    if not conversation_id or conversation_id not in grant.conversation_ids:
        raise AccessDenied('Conversation not granted')
    return grant


def authorize_history(*, message_created_at: int, **arguments) -> OwnerGrant:
    grant = authorize(operation='history', **arguments)
    if message_created_at < grant.history_from:
        raise AccessDenied('Message predates the history grant')
    return grant
