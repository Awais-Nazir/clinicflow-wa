"""Create or update a tenant from a public-information manifest."""

import argparse
from datetime import time
from decimal import Decimal
from hashlib import sha256
import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from clinicflow.credentials import CredentialError, encrypt_access_token
from clinicflow.database import session_scope
from clinicflow.embeddings import HashingEmbedder
from clinicflow.models import (
    ClinicOpeningHours, ClinicProfile, ClinicService, Doctor, KnowledgeChunk,
    Provider, ProviderHours, ProviderService, Tenant, WhatsAppPhoneNumber,
)


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OpeningWindow(ManifestModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def valid_window(self):
        if self.opens_at.tzinfo or self.closes_at.tzinfo or self.opens_at >= self.closes_at:
            raise ValueError("opening hours must be a same-day local interval")
        return self


class Profile(ManifestModel):
    description: str
    address: str
    city: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=100)
    contact_phone: str
    email: str | None = None


class Service(ManifestModel):
    name: str = Field(min_length=1, max_length=160)
    description: str
    price_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="PKR", pattern=r"^[A-Z]{3}$")
    duration_minutes: int = Field(ge=1, le=480)


class BookableProvider(ManifestModel):
    name: str = Field(min_length=1, max_length=160)
    specialty: str = Field(min_length=1, max_length=160)
    bio: str = ""
    services: list[str] = Field(min_length=1)
    hours: list[OpeningWindow] = Field(min_length=1)


class Knowledge(ManifestModel):
    source_key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1)
    language: Literal["en", "ur", "roman_ur"] = "en"


class TenantManifest(ManifestModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{2,60}$")
    name: str = Field(min_length=1, max_length=160)
    timezone: str = Field(default="Asia/Karachi", max_length=64)
    reply_tone: Literal["standard", "warm", "formal", "concise"] = "standard"
    default_language: Literal["en", "ur", "roman_ur"] = "roman_ur"
    phone_number_id: str = Field(min_length=1, max_length=128)
    display_phone_number: str | None = None
    profile: Profile
    hours: list[OpeningWindow] = Field(min_length=1)
    services: list[Service] = Field(min_length=1)
    providers: list[BookableProvider] = Field(min_length=1)
    knowledge: list[Knowledge] = Field(min_length=1)

    @model_validator(mode="after")
    def check_relationships(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        services = {service.name for service in self.services}
        _ensure_unique([service.name.casefold() for service in self.services], "services")
        _ensure_unique([provider.name.casefold() for provider in self.providers], "providers")
        _ensure_unique([chunk.source_key for chunk in self.knowledge], "knowledge sources")
        _ensure_unique([item.weekday for item in self.hours], "clinic weekdays")
        for provider in self.providers:
            if set(provider.services) - services:
                raise ValueError("each provider service must exist in the clinic catalogue")
            _ensure_unique([item.weekday for item in provider.hours], "provider weekdays")
        return self


def _ensure_unique(values, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate manifest {label}")


def _upsert(model, key_columns, values):
    statement = insert(model).values(**values)
    return statement.on_conflict_do_update(
        index_elements=key_columns,
        set_={name: statement.excluded[name] for name in values if name not in key_columns},
    )


def onboard_tenant(
    manifest: TenantManifest, *, whatsapp_token: str | None = None, staff_token: str | None = None,
) -> int:
    if staff_token is not None and len(staff_token.strip()) < 32:
        raise ValueError("staff token must contain at least 32 characters")
    ciphertext = encrypt_access_token(whatsapp_token) if whatsapp_token is not None else None
    settings = {
        key: getattr(manifest, key)
        for key in ("slug", "name", "timezone", "reply_tone", "default_language")
    }
    if staff_token is not None:
        settings["staff_token_hash"] = sha256(staff_token.strip().encode()).hexdigest()
    embedder = HashingEmbedder()
    embeddings = embedder.embed([chunk.content for chunk in manifest.knowledge])

    with session_scope() as session:
        tenant_id = session.execute(
            _upsert(Tenant, ["slug"], settings).returning(Tenant.id)
        ).scalar_one()
        phone_values = {
            "tenant_id": tenant_id, "phone_number_id": manifest.phone_number_id,
            "display_phone_number": manifest.display_phone_number,
        }
        if ciphertext is not None:
            phone_values["access_token_ciphertext"] = ciphertext
        phone = insert(WhatsAppPhoneNumber).values(**phone_values)
        accepted = session.scalar(phone.on_conflict_do_update(
            index_elements=["phone_number_id"],
            set_={key: phone.excluded[key] for key in phone_values if key != "phone_number_id"},
            where=WhatsAppPhoneNumber.tenant_id == tenant_id,
        ).returning(WhatsAppPhoneNumber.id))
        if accepted is None:
            raise ValueError("phone number already belongs to another tenant")

        session.execute(_upsert(ClinicProfile, ["tenant_id"], {
            "tenant_id": tenant_id, **manifest.profile.model_dump(),
        }))
        opening_by_day = {window.weekday: window for window in manifest.hours}
        for weekday in range(7):
            window = opening_by_day.get(weekday)
            session.execute(_upsert(ClinicOpeningHours, ["tenant_id", "weekday"], {
                "tenant_id": tenant_id, "weekday": weekday, "is_closed": window is None,
                "opens_at": window.opens_at if window else None,
                "closes_at": window.closes_at if window else None,
            }))

        service_ids = {}
        for service in manifest.services:
            service_ids[service.name] = session.execute(
                _upsert(ClinicService, ["tenant_id", "name"], {
                    "tenant_id": tenant_id, "is_active": True, **service.model_dump(),
                }).returning(ClinicService.id)
            ).scalar_one()
        for provider in manifest.providers:
            provider_id = session.execute(_upsert(Provider, ["tenant_id", "name"], {
                "tenant_id": tenant_id, "name": provider.name, "is_active": True,
            }).returning(Provider.id)).scalar_one()
            session.execute(_upsert(Doctor, ["tenant_id", "name"], {
                "tenant_id": tenant_id, "name": provider.name, "specialty": provider.specialty,
                "bio": provider.bio, "is_active": True,
            }))
            for service_name in provider.services:
                session.execute(insert(ProviderService).values(
                    tenant_id=tenant_id, provider_id=provider_id, service_id=service_ids[service_name],
                ).on_conflict_do_nothing())
            for window in provider.hours:
                session.execute(_upsert(ProviderHours, ["provider_id", "weekday"], {
                    "tenant_id": tenant_id, "provider_id": provider_id, **window.model_dump(),
                }))
        for chunk, embedding in zip(manifest.knowledge, embeddings, strict=True):
            session.execute(_upsert(KnowledgeChunk, ["tenant_id", "source_key", "chunk_index"], {
                "tenant_id": tenant_id, "chunk_index": 0, "embedding": embedding,
                "embedding_model": embedder.model_name, **chunk.model_dump(),
            }))
    return tenant_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--offline", action="store_true", help="use a simulated WhatsApp number")
    args = parser.parse_args()
    token = None if args.offline else os.getenv("CLINICFLOW_NEW_TENANT_TOKEN")
    if not args.offline and not token:
        parser.error("set CLINICFLOW_NEW_TENANT_TOKEN or use --offline")
    try:
        manifest = TenantManifest.model_validate_json(args.manifest.read_bytes())
        tenant_id = onboard_tenant(
            manifest, whatsapp_token=token, staff_token=os.getenv("CLINICFLOW_NEW_STAFF_TOKEN"),
        )
    except ValidationError as exc:
        invalid = ", ".join(".".join(str(part) for part in err["loc"]) or "manifest" for err in exc.errors())
        parser.error("invalid manifest fields: " + invalid)
    except (OSError, CredentialError, ValueError) as exc:
        parser.error(str(exc))
    except IntegrityError:
        parser.error("tenant provisioning conflicted with an existing database record")
    print(f"Ready: {manifest.slug} (tenant {tenant_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
