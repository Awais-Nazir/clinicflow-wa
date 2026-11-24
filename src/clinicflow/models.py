"""SQLAlchemy persistence models."""

from datetime import date, datetime, time
from decimal import Decimal

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
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
        UniqueConstraint("id", "tenant_id", name="uq_clinic_services_id_tenant"),
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


class ClinicOpeningHours(Base):
    """A clinic's regular hours for one weekday, Monday zero through Sunday six."""

    __tablename__ = "clinic_opening_hours"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "weekday",
            name="uq_clinic_opening_hours_tenant_weekday",
        ),
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_clinic_opening_hours_weekday",
        ),
        CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND "
            "closes_at IS NOT NULL AND opens_at < closes_at)",
            name="ck_clinic_opening_hours_valid_interval",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time)
    closes_at: Mapped[time | None] = mapped_column(Time)


class Doctor(Base):
    """A doctor displayed by one clinic."""

    __tablename__ = "doctors"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_doctors_tenant_name",
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
    specialty: Mapped[str] = mapped_column(String(160), nullable=False)
    bio: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )


class KnowledgeChunk(Base):
    """A tenant-owned FAQ or policy chunk available to retrieval."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_key",
            "chunk_index",
            name="uq_knowledge_chunks_tenant_source_chunk",
        ),
        Index(
            "ix_knowledge_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    source_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="en",
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple'::regconfig, COALESCE(content, ''))",
            persisted=True,
        ),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(384))
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Conversation(Base):
    """One customer's ongoing conversation with one tenant."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "customer_wa_id",
            name="uq_conversations_tenant_customer",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_conversations_id_tenant",
        ),
        CheckConstraint(
            "status IN ('bot', 'human', 'closed')",
            name="ck_conversations_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    customer_wa_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="bot",
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    booking_draft: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    human_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class HandoffCase(Base):
    """A staff-visible case for a conversation that needs a human."""

    __tablename__ = "handoff_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"], ["conversations.id", "conversations.tenant_id"],
            name="fk_handoff_conversation_tenant",
        ),
        Index("ix_handoff_tenant_open", "tenant_id", "closed_at"),
        Index(
            "uq_handoff_open_conversation", "conversation_id", unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageCounter(Base):
    """A locked daily quota bucket for one tenant and opaque subject."""

    __tablename__ = "usage_counters"
    __table_args__ = (CheckConstraint("units >= 0", name="ck_usage_counters_units"),)

    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenants.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    subject: Mapped[str] = mapped_column(String(80), primary_key=True)
    units: Mapped[int] = mapped_column(BigInteger, server_default="0")


class Provider(Base):
    """A bookable clinician or resource owned by one clinic."""

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_providers_id_tenant"),
        UniqueConstraint("tenant_id", "name", name="uq_providers_tenant_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class ProviderHours(Base):
    """One bookable local working window per provider and weekday."""

    __tablename__ = "provider_hours"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "tenant_id"], ["providers.id", "providers.tenant_id"],
            name="fk_provider_hours_provider_tenant",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_provider_hours_weekday"),
        CheckConstraint("opens_at < closes_at", name="ck_provider_hours_interval"),
    )

    provider_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    opens_at: Mapped[time] = mapped_column(Time)
    closes_at: Mapped[time] = mapped_column(Time)


class ProviderService(Base):
    """Services a provider may perform, within one tenant."""

    __tablename__ = "provider_services"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "tenant_id"], ["providers.id", "providers.tenant_id"],
            name="fk_provider_services_provider_tenant",
        ),
        ForeignKeyConstraint(
            ["service_id", "tenant_id"], ["clinic_services.id", "clinic_services.tenant_id"],
            name="fk_provider_services_service_tenant",
        ),
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    service_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class Appointment(Base):
    """A customer's booked or cancelled appointment with UTC endpoints."""

    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "provider_id", "service_id"],
            ["provider_services.tenant_id", "provider_services.provider_id", "provider_services.service_id"],
            name="fk_appointments_offering",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"], ["conversations.id", "conversations.tenant_id"],
            name="fk_appointments_conversation_tenant",
        ),
        CheckConstraint("starts_at < ends_at", name="ck_appointments_interval"),
        CheckConstraint("status IN ('booked', 'cancelled')", name="ck_appointments_status"),
        Index(
            "uq_appointments_booked_provider_start", "provider_id", "starts_at",
            unique=True, postgresql_where=text("status = 'booked'"),
        ),
        Index("ix_appointments_conversation_start", "conversation_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    provider_id: Mapped[int] = mapped_column(BigInteger)
    service_id: Mapped[int] = mapped_column(BigInteger)
    customer_name: Mapped[str] = mapped_column(String(160))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="booked")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationMessage(Base):
    """A tenant-scoped inbound or outbound message in a conversation."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            name="fk_conversation_messages_conversation_tenant",
        ),
        UniqueConstraint(
            "external_message_id",
            name="uq_conversation_messages_external_message_id",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_conversation_messages_direction",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    external_message_id: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AgentRun(Base):
    """A durable explanation of one FAQ-agent execution."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            name="fk_agent_runs_conversation_tenant",
        ),
        CheckConstraint(
            "status IN ('completed', 'fallback')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND "
            "latency_ms >= 0 AND cost_usd >= 0",
            name="ck_agent_runs_nonnegative_metrics",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    guardrail_reason: Mapped[str | None] = mapped_column(String(64))
    unsupported_numbers: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="[]",
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reply: Mapped[str] = mapped_column(Text, nullable=False)
    tool_events: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 8),
        nullable=False,
        server_default="0",
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
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            name="fk_inbox_messages_conversation_tenant",
        ),
        UniqueConstraint(
            "message_id",
            name="uq_inbox_messages_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
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
    faq_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_day: Mapped[date | None] = mapped_column(Date)
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
    reply_buttons: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    allow_during_handoff: Mapped[bool] = mapped_column(Boolean, server_default="false")
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
