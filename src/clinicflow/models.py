"""SQLAlchemy persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""


class Tenant(Base):
    """A clinic whose data and conversations must remain isolated."""

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WhatsAppPhoneNumber(Base):
    """A Meta phone-number ID routed to exactly one tenant."""

    __tablename__ = "whatsapp_phone_numbers"
    __table_args__ = (
        UniqueConstraint(
            "phone_number_id",
            name="uq_whatsapp_phone_numbers_phone_number_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    phone_number_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_phone_number: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ClinicProfile(Base):
    """One structured public-information profile per clinic."""

    __tablename__ = "clinic_profiles"

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        primary_key=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_phone: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ClinicService(Base):
    """A tenant-owned service with deterministic price and duration facts."""

    __tablename__ = "clinic_services"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_clinic_services_tenant_name",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="PKR",
    )
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class InboxMessage(Base):
    """A durable, uniquely identified inbound WhatsApp message."""

    __tablename__ = "inbox_messages"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            name="uq_inbox_messages_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    phone_number_id: Mapped[str] = mapped_column(Text, nullable=False)
    sender_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    message_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class OutboxMessage(Base):
    """A durable outbound message waiting to be sent to WhatsApp."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint(
            "inbox_message_id",
            name="uq_outbox_messages_inbox_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    inbox_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("inbox_messages.id"),
        nullable=False,
    )
    phone_number_id: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
