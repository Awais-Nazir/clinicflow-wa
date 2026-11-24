from clinicflow.observability import redact_error_event


def test_error_reporting_removes_patient_context_and_raw_errors():
    event = {
        "event_id": "abc123",
        "user": {"name": "Patient Name"},
        "request": {"headers": {"Authorization": "secret"}, "data": "appointment details"},
        "exception": {"values": [{
            "type": "ValueError", "value": "patient telephone +92123456",
            "stacktrace": {"frames": [{
                "filename": r"C:\Users\private-user\worker.py", "lineno": 17,
                "vars": {"phone": "+92123456"}, "function": "process",
            }]},
        }]},
    }
    assert redact_error_event(event) == {
        "event_id": "abc123",
        "exception": {"values": [{
            "type": "ValueError", "module": None,
            "stacktrace": {"frames": [{"filename": "worker.py", "lineno": 17, "function": "process"}]},
        }]},
    }
