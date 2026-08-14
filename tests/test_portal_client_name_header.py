from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1] / "static" / "client-portal.html"
).read_text(encoding="utf-8")


def test_client_name_header_is_at_top_of_portal():
    identity = HTML.index('id="portal-client-identity"')
    onboarding = HTML.index('id="portal-onboarding-mount"')
    app = HTML.index('id="app"')

    assert identity < onboarding < app
    assert 'id="portal-client-name"' in HTML
    assert "My Healing Oasis" in HTML
    assert "Healing Oasis client portal" not in HTML
    assert HTML.count('id="client-photo"') == 1
    assert (
        HTML.index('id="client-photo"')
        < HTML.index('id="portal-client-name"')
        < onboarding
    )


def test_hub_places_where_you_are_before_the_onboarding_checklist():
    banner = HTML.index('<div class="hub-banner"><div class="where">')
    slot = HTML.index('id="portal-onboarding-slot"')
    calendar = HTML.index('${buildCalendarHtml(v,true)}', banner)

    assert banner < slot < calendar
    assert 'if(onboardingMount && onboardingSlot) onboardingSlot.appendChild(onboardingMount);' in HTML
    assert '.hub-banner .eyebrow' in HTML and 'text-transform:none' in HTML


def test_client_name_header_uses_account_name_without_email_fallback():
    assert (
        'const portalClientName = d.name || (v && v.account && v.account.name) || "";'
        in HTML
    )
    assert 'identityName.textContent = safeClientName;' in HTML
    assert 'identity.hidden = !safeClientName;' in HTML
    assert '/[@]/.test(portalClientName)' in HTML


def test_header_photo_keeps_upload_and_initials_fallback():
    assert 'id="client-photo-placeholder"' in HTML
    assert 'id="client-photo-file"' in HTML
    assert 'placeholder.style.display = "none"' in HTML
    assert 'placeholder.style.display = ""' in HTML
    assert ".portal-client-identity[hidden]{display:none}" in HTML
