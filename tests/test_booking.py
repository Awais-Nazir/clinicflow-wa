"""Integration checks for booking isolation, ownership and concurrency."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest

from clinicflow.booking import (
    book_appointment,
    cancel_appointment,
    list_booking_slots,
    reschedule_appointment,
)
from clinicflow.conversations import get_or_create_conversation
from clinicflow.seed_booking import seed_demo_booking

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="DATABASE_URL is required for PostgreSQL"
)
NOW = datetime(2026, 11, 25, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


@pytest.fixture
def bookable():
    tag = uuid4().hex
    tenant_id, offerings = seed_demo_booking("booking-" + tag)
    offering = offerings["General consultation"]
    callers = ("caller-a-" + tag, "caller-b-" + tag)
    for caller in callers:
        get_or_create_conversation(tenant_id, caller)
    return tenant_id, offering, callers


def test_simultaneous_requests_for_one_slot_offer_the_loser_an_alternative(bookable):
    tenant_id, offering, callers = bookable
    race = Barrier(2)

    def book(caller):
        race.wait(timeout=5)
        return book_appointment(
            tenant_id=tenant_id, customer_wa_id=caller, provider_id=offering.provider_id,
            service_id=offering.service_id, starts_at=START, customer_name=caller, now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(book, callers))
    assert {result.status for result in results} == {"booked", "slot_unavailable"}
    loser = next(result for result in results if result.status == "slot_unavailable")
    assert loser.alternatives
    assert all(slot.starts_at != START for slot in loser.alternatives)
    winner = callers[next(i for i, result in enumerate(results) if result.status == "booked")]
    booked = next(result for result in results if result.status == "booked")
    assert cancel_appointment(
        tenant_id=tenant_id, customer_wa_id=winner, appointment_id=booked.appointment_id, now=NOW
    ).status == "cancelled"


def test_only_owner_can_reschedule_or_cancel_and_cancelled_slot_can_be_reused(bookable):
    tenant_id, offering, (owner, stranger) = bookable
    booking = dict(
        tenant_id=tenant_id, provider_id=offering.provider_id,
        service_id=offering.service_id, customer_name="Example Customer", now=NOW,
    )
    original = book_appointment(**booking, customer_wa_id=owner, starts_at=START)
    assert original.status == "booked"
    lookup = dict(tenant_id=tenant_id, appointment_id=original.appointment_id, now=NOW)
    new_start = START + timedelta(minutes=15)
    assert reschedule_appointment(
        **lookup, customer_wa_id=stranger, starts_at=new_start
    ).status == "not_found"
    assert cancel_appointment(**lookup, customer_wa_id=stranger).status == "not_found"
    assert book_appointment(
        **booking, customer_wa_id=stranger, starts_at=new_start
    ).status == "slot_unavailable"
    assert reschedule_appointment(
        **lookup, customer_wa_id=owner, starts_at=new_start
    ).status == "rescheduled"
    assert cancel_appointment(**lookup, customer_wa_id=owner).status == "cancelled"
    reused = book_appointment(
        **booking, customer_wa_id=stranger, starts_at=new_start
    )
    assert reused.status == "booked"
    slots = list_booking_slots(
        tenant_id + 100000, offering.provider_id, offering.service_id, START.date(), now=NOW
    )
    assert slots == []
    assert cancel_appointment(
        tenant_id=tenant_id, customer_wa_id=stranger, appointment_id=reused.appointment_id, now=NOW
    ).status == "cancelled"
