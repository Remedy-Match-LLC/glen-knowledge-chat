def test_client_proposal_email_points_staff_to_console(monkeypatch):
    import app as appmod

    sent = []
    monkeypatch.setattr(appmod, "GLEN_CONSULT_EMAIL", "glen@example.com")
    monkeypatch.setattr(appmod, "_queue_appointment_email",
                        lambda *args: sent.append(args))

    appmod._notify_staff_of_appointment_proposal({
        "client_email": "steve@example.com",
        "practitioner": "glen",
        "session_type": "biofield-consult",
        "session_label": "Biofield Analysis Consultation",
        "proposed_start": "2026-08-14T11:00:00",
    })

    assert sent[0][0] == "glen@example.com"
    assert "steve@example.com" in sent[0][2]
    assert "2026-08-14 11:00:00 HST" in sent[0][3]
    assert "/console/appointment-proposals" in sent[0][3]


def test_evox_proposal_notifies_rae(monkeypatch):
    import app as appmod

    sent = []
    monkeypatch.setattr(appmod, "EVOX_RAE_EMAIL", "rae@example.com")
    monkeypatch.setattr(appmod, "_queue_appointment_email",
                        lambda *args: sent.append(args))

    appmod._notify_staff_of_appointment_proposal({
        "client_email": "client@example.com",
        "practitioner": "rae",
        "session_type": "evox",
        "session_label": "EVOX Session",
        "proposed_start": "2026-08-15T10:30:00",
    })

    assert sent[0][0] == "rae@example.com"
