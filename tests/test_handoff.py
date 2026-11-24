import pytest

from clinicflow.handoff import handoff_reason, handoff_reply


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("I can't breathe", "emergency"),
        ("Mujhe insaan se baat karni hai", "requested_staff"),
        ("I have a complaint", "complaint"),
        ("I have a rash", "medical_question"),
        ("What is the cancellation policy?", None),
        ("Can WhatsApp diagnose my condition?", None),
    ],
)
def test_handoff_classifies_requests_before_inference(message, reason):
    assert handoff_reason(message) == reason


def test_emergency_notice_gives_urgent_direction():
    assert "emergency services" in handoff_reply("I can't breathe", "emergency")
