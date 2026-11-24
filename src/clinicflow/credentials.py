"""Resolve outbound WhatsApp credentials by the receiving phone number."""

import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from clinicflow.database import session_scope
from clinicflow.models import WhatsAppPhoneNumber


class CredentialError(RuntimeError):
    """A phone's WhatsApp token is missing or unavailable."""


def _cipher() -> Fernet:
    key = os.getenv("CLINICFLOW_TOKEN_KEY", "")
    if not key:
        raise CredentialError("ClinicFlow token key is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise CredentialError("ClinicFlow token key is invalid") from exc


def encrypt_access_token(token: str) -> str:
    if not token.strip():
        raise CredentialError("WhatsApp token is empty")
    return _cipher().encrypt(token.strip().encode()).decode("ascii")


def resolve_access_token(phone_number_id: str) -> str:
    with session_scope() as session:
        ciphertext = session.scalar(select(WhatsAppPhoneNumber.access_token_ciphertext).where(
            WhatsAppPhoneNumber.phone_number_id == phone_number_id,
        ))
    if ciphertext:
        try:
            return _cipher().decrypt(ciphertext.encode("ascii")).decode()
        except (InvalidToken, UnicodeError) as exc:
            raise CredentialError("Could not decrypt the WhatsApp token") from exc
    if phone_number_id == os.getenv("WHATSAPP_PHONE_NUMBER_ID"):
        legacy_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        if legacy_token:
            return legacy_token
    raise CredentialError("WhatsApp token is not configured for this phone number")
