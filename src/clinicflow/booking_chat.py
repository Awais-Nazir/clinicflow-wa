"""Booking drafts and explicit confirmation for WhatsApp conversations."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
import re
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from clinicflow.agent import ReplyLanguage, detect_reply_language
from clinicflow.booking import (
    book_in_session,
    cancel_in_session,
    list_slots_in_session,
    reschedule_in_session,
)
from clinicflow.booking_time import (
    BookingDateError,
    booking_period,
    fits_booking_period,
    normalize_booking_text,
    parse_booking_clock,
    parse_booking_date,
)
from clinicflow.models import Appointment, ClinicService, Conversation, Provider, ProviderService, Tenant

CONFIRM_WORDS = {"confirm", "yes confirm", "haan confirm", "تصدیق", "کنفرم"}
DISMISS_WORDS = {"no", "nahi", "stop", "stop booking", "discard", "رہنے دیں", "نہیں"}
ACTION_LIFETIME = timedelta(minutes=15)
DRAFT_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class BookingReply:
    body: str
    buttons: tuple[dict[str, str], ...] = ()


@dataclass
class BookingDraft:
    operation: str
    language: ReplyLanguage
    service_id: int | None = None
    service_label: str | None = None
    provider_id: int | None = None
    appointment_id: int | None = None
    name: str | None = None
    day: str | None = None
    clock: str | None = None
    period: str | None = None
    starts_at: str | None = None
    choices: list[str] = field(default_factory=list)
    appointment_choices: list[int] = field(default_factory=list)
    token: str | None = None
    expires_at: str | None = None
    updated_at: str | None = None
    waiting_for_name: bool = False


@dataclass(frozen=True)
class BookingChat:
    session: Session
    conversation: Conversation
    now: datetime
    zone: ZoneInfo

    @property
    def tenant_id(self) -> int:
        return self.conversation.tenant_id

    def save(self, draft: BookingDraft | None) -> None:
        if draft is not None:
            draft.updated_at = self.now.isoformat()
        self.conversation.booking_draft = asdict(draft) if draft is not None else None


def handle_booking_message(
    session: Session, *, tenant_id: int, conversation_id: int, customer_wa_id: str,
    body: str, button_id: str | None = None, now: datetime,
) -> BookingReply | None:
    """Handle a verified booking turn, or leave the question for the FAQ path."""

    context = session.execute(
        select(Conversation, Tenant.timezone)
        .join(Tenant, Tenant.id == Conversation.tenant_id)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
            Conversation.customer_wa_id == customer_wa_id,
        )
        .with_for_update(of=Conversation)
    ).one_or_none()
    if context is None:
        return None
    conversation, timezone_name = context
    chat = BookingChat(session, conversation, now, ZoneInfo(timezone_name))
    draft = BookingDraft(**conversation.booking_draft) if conversation.booking_draft else None
    if draft and draft.updated_at and datetime.fromisoformat(draft.updated_at) + DRAFT_LIFETIME <= now:
        chat.save(None)
        draft = None

    text = normalize_booking_text(body).strip(" .!?؟")
    if button_id is not None or text in CONFIRM_WORDS:
        return _confirm(chat, draft, text, button_id) if draft or button_id else None
    if draft and text in DISMISS_WORDS:
        chat.save(None)
        return _say(draft.language, "discarded")

    operation = _operation(text)
    if operation and (draft is None or operation != draft.operation):
        draft = BookingDraft(operation, _booking_language(body))
    if draft is None:
        return None

    if draft.operation in {"cancel", "reschedule"} and draft.appointment_id is None:
        selection = _pick_appointment(chat, draft, text)
        if selection is not None:
            return selection

    if draft.operation == "cancel":
        return _propose(chat, draft)
    return _advance_booking(chat, draft, body, text, fresh_booking_request=operation == "book")


def _operation(text: str) -> str | None:
    if re.search(r"\bpolicy\b|پالیسی", text):
        return None
    if re.search(r"\b(?:reschedule|move my appointment|appointment change)\b|اپائنٹمنٹ تبدیل", text):
        return "reschedule"
    if re.search(r"\b(?:cancel|cancel my) (?:my )?(?:appointment|booking)\b|"
                 r"\bappointment cancel\b|اپائنٹمنٹ منسوخ", text):
        return "cancel"
    if re.search(r"\b(?:book|schedule|appointment (?:chahiye|lena))\b|"
                 r"اپائنٹمنٹ.*(?:بک|چاہیے)", text):
        return "book"
    return None


def _booking_language(body: str) -> ReplyLanguage:
    detected = detect_reply_language(body)
    if detected == "en" and re.search(r"\b(?:baje|shaam|kal|aaj|naam)\b", body.casefold()):
        return "roman_ur"
    return detected


def _offerings(chat: BookingChat):
    return chat.session.execute(
        select(ClinicService.id, ClinicService.name, Provider.id)
        .select_from(ProviderService)
        .join(ClinicService, and_(
            ClinicService.id == ProviderService.service_id,
            ClinicService.tenant_id == ProviderService.tenant_id,
        ))
        .join(Provider, and_(
            Provider.id == ProviderService.provider_id,
            Provider.tenant_id == ProviderService.tenant_id,
        ))
        .where(
            ProviderService.tenant_id == chat.tenant_id,
            ClinicService.is_active.is_(True),
            ClinicService.duration_minutes > 0,
            Provider.is_active.is_(True),
        )
        .order_by(ClinicService.name, Provider.id)
    ).all()


def _advance_booking(
    chat: BookingChat, draft: BookingDraft, body: str, text: str,
    *, fresh_booking_request: bool,
) -> BookingReply:
    try:
        day = parse_booking_date(body, today=chat.now.astimezone(chat.zone).date())
        clock = parse_booking_clock(body)
    except BookingDateError:
        draft.token = None
        chat.save(draft)
        return _say(draft.language, "clarify")
    period = booking_period(body)
    updates_slot = day is not None or clock is not None or period is not None
    if updates_slot:
        draft.token = None
        draft.choices = []
        draft.starts_at = None
        if day is not None:
            draft.day = day.isoformat()
        if period is not None:
            draft.period = period
            if clock is None:
                draft.clock = None
        if clock is not None:
            draft.clock = clock.isoformat()
            draft.period = period

    if draft.operation == "book":
        offerings = _offerings(chat)
        matching = {
            (service_id, label) for service_id, label, _ in offerings
            if re.search(rf"(?<!\w){re.escape(normalize_booking_text(label))}(?!\w)", text)
        }
        if len(matching) == 1:
            service_id, service_label = matching.pop()
            if service_id != draft.service_id:
                draft.service_id, draft.service_label = service_id, service_label
                draft.provider_id = None
                draft.starts_at = None
                draft.token = None
                draft.choices = []
                updates_slot = True
        elif fresh_booking_request or matching:
            draft.service_id = None
            draft.service_label = None
            draft.provider_id = None
            draft.starts_at = None
            draft.token = None
            draft.choices = []
        if draft.service_id is None:
            chat.save(draft)
            return _say(draft.language, "service", services=", ".join(sorted({row[1] for row in offerings})))

    named = re.fullmatch(
        r"(?:my name is |name[: ]+|mera naam |میرا نام )(.{1,100})", body.strip(), re.IGNORECASE
    )
    if named or (draft.waiting_for_name and not updates_slot and text not in DISMISS_WORDS):
        value = named[1] if named else body.strip()
        if _valid_name(value):
            draft.name = value
            draft.token = None

    if draft.day is None:
        chat.save(draft)
        return _say(draft.language, "date")
    chosen_day = date.fromisoformat(draft.day)
    local_now = chat.now.astimezone(chat.zone)
    if chosen_day > local_now.date() + timedelta(days=90):
        draft.day = None
        draft.starts_at = None
        chat.save(draft)
        return _say(draft.language, "too_far")
    if chosen_day < local_now.date() or (
        chosen_day == local_now.date() and draft.clock
        and time.fromisoformat(draft.clock) <= local_now.time()
    ):
        draft.day = None
        draft.clock = None
        draft.starts_at = None
        chat.save(draft)
        return _say(draft.language, "past")

    if draft.starts_at is None:
        if draft.choices and re.fullmatch(r"[1-3]", text) and int(text) <= len(draft.choices):
            choice = draft.choices[int(text) - 1]
            _choose(draft, choice)
        else:
            choices = _collect_slots(chat, draft)
            preferred = (
                next((choice for choice in choices if _local_clock(choice, chat.zone) == time.fromisoformat(draft.clock)), None)
                if draft.clock else None
            )
            if preferred is None:
                draft.choices = choices[:3]
                chat.save(draft)
                return _offer_slots(chat, draft)
            _choose(draft, preferred)

    if datetime.fromisoformat(draft.starts_at) <= chat.now:
        draft.starts_at = None
        draft.choices = []
        chat.save(draft)
        return _say(draft.language, "past")
    if draft.name is None:
        draft.waiting_for_name = True
        chat.save(draft)
        return _say(draft.language, "name")

    draft.waiting_for_name = False
    return _propose(chat, draft)


def _valid_name(name: str) -> bool:
    return (
        1 < len(name) <= 100
        and len(name.split()) <= 6
        and not re.match(r"(?i)^(?:what|where|how|when|why|can|please|actually)\b", name)
        and all(char.isalpha() or char in " '-’." for char in name)
        and any(char.isalpha() for char in name)
    )


def _collect_slots(chat: BookingChat, draft: BookingDraft) -> list[str]:
    day = date.fromisoformat(draft.day)
    provider_ids = (
        [draft.provider_id] if draft.operation == "reschedule" else
        [provider_id for service_id, _, provider_id in _offerings(chat) if service_id == draft.service_id]
    )
    choices = []
    for provider_id in provider_ids:
        for slot in list_slots_in_session(
            chat.session, chat.tenant_id, provider_id, draft.service_id, day, chat.now,
            exclude_appointment_id=draft.appointment_id,
        ):
            if fits_booking_period(slot.starts_at.astimezone(chat.zone), draft.period):
                choices.append((slot.starts_at, provider_id))
    return [f"{provider_id}|{start.isoformat()}" for start, provider_id in sorted(choices)]


def _choose(draft: BookingDraft, choice: str) -> None:
    provider_id, starts_at = choice.split("|", 1)
    draft.provider_id = int(provider_id)
    draft.starts_at = starts_at
    draft.choices = []


def _local_clock(choice: str, zone: ZoneInfo) -> time:
    return datetime.fromisoformat(choice.split("|", 1)[1]).astimezone(zone).time()


def _offer_slots(chat: BookingChat, draft: BookingDraft) -> BookingReply:
    if not draft.choices:
        return _say(draft.language, "no_slots")
    slots = "\n".join(
        f"{index}. {_local_clock(choice, chat.zone):%H:%M}"
        for index, choice in enumerate(draft.choices, start=1)
    )
    return _say(draft.language, "slots", day=draft.day, slots=slots)


def _pick_appointment(chat: BookingChat, draft: BookingDraft, text: str) -> BookingReply | None:
    rows = chat.session.execute(
        select(Appointment, ClinicService.name).join(ClinicService, and_(
            ClinicService.id == Appointment.service_id,
            ClinicService.tenant_id == Appointment.tenant_id,
        )).where(
            Appointment.tenant_id == chat.tenant_id,
            Appointment.conversation_id == chat.conversation.id,
            Appointment.status == "booked",
            Appointment.starts_at > chat.now,
        ).order_by(Appointment.starts_at, Appointment.id)
    ).all()
    if not rows:
        chat.save(None)
        return _say(draft.language, "no_booking")
    choice = int(text) - 1 if re.fullmatch(r"[1-9]\d?", text) else None
    if len(rows) == 1:
        selected = rows[0]
    elif choice is not None and choice < len(draft.appointment_choices):
        wanted = draft.appointment_choices[choice]
        selected = next((row for row in rows if row[0].id == wanted), None)
    else:
        selected = None
    if selected is None:
        draft.appointment_choices = [appointment.id for appointment, _ in rows[:9]]
        choices = "\n".join(
            f"{i}. {label} — {appointment.starts_at.astimezone(chat.zone):%Y-%m-%d %H:%M}"
            for i, (appointment, label) in enumerate(rows[:9], start=1)
        )
        chat.save(draft)
        return _say(draft.language, "pick_booking", slots=choices)
    appointment, label = selected
    draft.appointment_id = appointment.id
    draft.provider_id = appointment.provider_id
    draft.service_id = appointment.service_id
    draft.service_label = label
    draft.name = appointment.customer_name
    if draft.operation == "cancel":
        draft.starts_at = appointment.starts_at.isoformat()
    return None


def _propose(chat: BookingChat, draft: BookingDraft) -> BookingReply:
    draft.token = token_urlsafe(12)
    draft.expires_at = (chat.now + ACTION_LIFETIME).isoformat()
    chat.save(draft)
    when = datetime.fromisoformat(draft.starts_at).astimezone(chat.zone)
    prompt = _say(
        draft.language, "confirm_" + draft.operation, service=draft.service_label,
        when=when.strftime("%Y-%m-%d %H:%M"), name=draft.name,
    )
    return BookingReply(prompt.body, (
        {"id": f"booking:confirm:{draft.token}", "title": _say(draft.language, "confirm_label").body},
        {"id": f"booking:dismiss:{draft.token}", "title": _say(draft.language, "dismiss_label").body},
    ))


def _confirm(
    chat: BookingChat, draft: BookingDraft | None, text: str, button_id: str | None,
) -> BookingReply:
    language = draft.language if draft else _booking_language(text)
    if not draft or not draft.token or not draft.expires_at:
        return _say(language, "expired")
    if button_id is not None and button_id not in {
        f"booking:confirm:{draft.token}", f"booking:dismiss:{draft.token}"
    }:
        return _say(language, "expired")
    if datetime.fromisoformat(draft.expires_at) <= chat.now:
        draft.token = None
        chat.save(draft)
        return _say(language, "expired")
    if button_id == f"booking:dismiss:{draft.token}":
        chat.save(None)
        return _say(language, "discarded")

    caller = dict(
        tenant_id=chat.tenant_id, customer_wa_id=chat.conversation.customer_wa_id, now=chat.now,
    )
    starts_at = datetime.fromisoformat(draft.starts_at)
    if draft.operation == "book":
        result = book_in_session(
            chat.session, **caller, provider_id=draft.provider_id, service_id=draft.service_id,
            starts_at=starts_at, customer_name=draft.name,
        )
    elif draft.operation == "reschedule":
        result = reschedule_in_session(
            chat.session, **caller, appointment_id=draft.appointment_id, starts_at=starts_at,
        )
    else:
        result = cancel_in_session(chat.session, **caller, appointment_id=draft.appointment_id)
    if result.status == "slot_unavailable":
        draft.token = None
        draft.starts_at = None
        draft.clock = None
        draft.choices = [f"{draft.provider_id}|{slot.starts_at.isoformat()}" for slot in result.alternatives]
        chat.save(draft)
        return _offer_slots(chat, draft)
    chat.save(None)
    return _say(draft.language, result.status, appointment=result.appointment_id)


def _say(language: ReplyLanguage, key: str, **values) -> BookingReply:
    index = {"en": 0, "roman_ur": 1, "ur": 2}[language]
    return BookingReply(COPY[key][index].format(**values))


COPY = {
    "service": ("What service would you like? {services}", "Kaunsi service chahiye? {services}", "کون سی سروس چاہیے؟ {services}"),
    "date": ("What date suits you? Send a weekday or YYYY-MM-DD.", "Kis din? Din ka naam ya YYYY-MM-DD bhejein.", "کس دن؟ دن کا نام یا YYYY-MM-DD بھیجیں۔"),
    "clarify": ("Please send the exact date or time, for example Thursday at 17:00.", "Exact din ya waqt bhejein, misaal ke taur par Thursday 17:00.", "براہِ کرم ٹھیک دن یا وقت بھیجیں، مثلاً جمعرات 17:00۔"),
    "past": ("That time has passed. Please send another date or time.", "Yeh waqt guzar gaya hai. Doosra din ya waqt bhejein.", "یہ وقت گزر چکا ہے۔ کوئی اور دن یا وقت بھیجیں۔"),
    "too_far": ("Please choose a date within the next 90 days.", "Agley 90 din mein koi tareekh chunein.", "براہِ کرم اگلے 90 دنوں میں کوئی تاریخ منتخب کریں۔"),
    "slots": ("Available times on {day} (clinic time). Reply with a choice number.\n{slots}", "{day} ko yeh waqt available hain. Choice ka number bhejein.\n{slots}", "{day} کو یہ اوقات دستیاب ہیں۔ انتخاب کا نمبر بھیجیں۔\n{slots}"),
    "no_slots": ("No slots are available in that window. Please send another day or time.", "Is waqt koi slot available nahi. Doosra din ya waqt bhejein.", "اس وقت کوئی وقت دستیاب نہیں۔ کوئی اور دن یا وقت بھیجیں۔"),
    "name": ("What name should I use for the appointment?", "Appointment kis naam se karun?", "اپائنٹمنٹ کس نام سے رکھوں؟"),
    "no_booking": ("I couldn't find an upcoming appointment for this number.", "Is number ki koi upcoming appointment nahi mili.", "اس نمبر کی کوئی آنے والی اپائنٹمنٹ نہیں ملی۔"),
    "pick_booking": ("Which appointment? Reply with its number.\n{slots}", "Kaunsi appointment? Us ka number bhejein.\n{slots}", "کون سی اپائنٹمنٹ؟ اس کا نمبر بھیجیں۔\n{slots}"),
    "confirm_book": ("Confirm {service} on {when} (clinic time) for {name}? Tap Confirm or type confirm.", "{name} ke liye {service}, {when} (clinic time). Confirm dabayein ya confirm likhein.", "{name} کے لیے {service}، {when} (کلینک کا وقت)۔ تصدیق دبائیں یا confirm لکھیں۔"),
    "confirm_reschedule": ("Move {name}'s {service} to {when} (clinic time)? Tap Confirm or type confirm.", "{name} ki {service} {when} par shift karein? Confirm dabayein ya confirm likhein.", "{name} کی {service} کو {when} پر منتقل کریں؟ تصدیق کریں۔"),
    "confirm_cancel": ("Cancel {name}'s {service} on {when} (clinic time)? Tap Confirm or type confirm.", "{name} ki {service} {when} par cancel karein? Confirm dabayein ya confirm likhein.", "{name} کی {service}، {when} والی اپائنٹمنٹ منسوخ کریں؟ تصدیق کریں۔"),
    "confirm_label": ("Confirm", "Confirm", "تصدیق"),
    "dismiss_label": ("Go back", "Wapas", "واپس"),
    "expired": ("This confirmation has expired. Send your date or request again.", "Yeh confirmation expire ho gayi. Din ya request dobara bhejein.", "اس تصدیق کی مدت گزر گئی۔ دن یا درخواست دوبارہ بھیجیں۔"),
    "discarded": ("Okay, I’ve discarded this request.", "Theek hai, yeh request band kar di.", "ٹھیک ہے، یہ درخواست ختم کر دی۔"),
    "booked": ("Your appointment is booked. Reference #{appointment}.", "Appointment book ho gayi. Reference #{appointment}.", "اپائنٹمنٹ بک ہو گئی۔ حوالہ #{appointment}۔"),
    "rescheduled": ("Your appointment was rescheduled. Reference #{appointment}.", "Appointment ka waqt badal diya. Reference #{appointment}.", "اپائنٹمنٹ کا وقت تبدیل ہو گیا۔ حوالہ #{appointment}۔"),
    "cancelled": ("Your appointment was cancelled. Reference #{appointment}.", "Appointment cancel ho gayi. Reference #{appointment}.", "اپائنٹمنٹ منسوخ ہو گئی۔ حوالہ #{appointment}۔"),
    "not_found": ("I couldn't complete that request. Please start again.", "Yeh request poori nahi ho saki. Dobara shuru karein.", "یہ درخواست مکمل نہیں ہو سکی۔ دوبارہ شروع کریں۔"),
}
