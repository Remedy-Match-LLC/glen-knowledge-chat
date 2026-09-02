from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" /
        "client-portal.html").read_text()


def test_portal_fetches_are_bounded_and_settled_independently():
    assert "const PORTAL_FETCH_TIMEOUT_MS = 20000;" in HTML
    assert "new AbortController()" in HTML
    assert "Promise.allSettled([" in HTML
    assert 'cache: "no-store"' in HTML
    assert "async function fetchPortalJsonOnce(url)" in HTML
    assert "result.status >= 500" in HTML
    assert "result = await fetchPortalJsonOnce(url)" in HTML
    assert "for(let attempt=0; attempt<3; attempt++)" in HTML


def test_portal_load_alert_uses_active_theme_colors():
    assert ".card.portal-load-alert{border-color:var(--btn-bg);background:var(--card2)!important;color:var(--ink)!important}" in HTML
    assert ':root[data-theme="dark"] .card.portal-load-alert' in HTML
    assert ':root:not([data-theme]) .card.portal-load-alert' in HTML
    assert "background:#fffaf0" not in HTML


def test_intake_completion_explains_where_saved_details_live():
    assert "My Clinical Record from the Understand section" in HTML
    assert 'button.textContent = healthPanel ? "Open My Clinical Record"' in HTML
    assert "My Health Profile is where you can review" in HTML
    assert "Your health profile, documents and past intakes" in HTML


def test_portal_distinguishes_invalid_link_from_transient_failure():
    assert 'required.status === 404) notFound()' in HTML
    assert "showPortalLoadFailure(required)" in HTML
    assert "Retry missing information" in HTML
    assert 'data-portal-home href="${esc(location.pathname)}"' in HTML
    assert "Back to portal home" in HTML


def test_shared_portal_hashes_route_to_real_panels_and_cards():
    for route in ("biofield", "recs", "intake", "offers", "photo", "cart", "shop"):
        assert f"{route}:" in HTML
    assert 'window.addEventListener("hashchange", applyPortalHash)' in HTML
    assert 'id="photo-section"' in HTML
    assert 'id="offers-card"' in HTML
    assert "There isn’t a new upgrade available" not in HTML
    assert "Next step to consider" in HTML
    assert "buildMembershipSummaryHtml" in HTML
    assert 'id="biofield-section"' in HTML
    assert 'data-panel="intake"' in HTML
    assert 'id="portal-intake-card"' in HTML


def test_hub_restores_selected_card_after_background_render():
    """A hub-only portal must not bounce back home after an async refresh."""
    assert "function showTab(name, options)" in HTML
    assert "if(options.persist !== false)" in HTML
    assert "showTab('hub', {persist:false})" in HTML
    assert "if (_wrapPanels) {" in HTML
    restore = HTML.index("if (_wrapPanels) {")
    scan_only = HTML.index("if (d.scan_history_enabled) {", restore)
    selected = HTML.index('sessionStorage.getItem("rm_portal_tab")', restore)
    show = HTML.index("showTab(wantTab);", selected)
    assert restore < selected < show < scan_only


def test_background_refresh_does_not_replace_an_open_card():
    assert "function portalDetailPanelIsActive()" in HTML
    assert "options.preserveActivePanel && portalDetailPanelIsActive()" in HTML
    assert HTML.count("options.preserveActivePanel && portalDetailPanelIsActive()") == 2
    assert HTML.count("preserveActiveIntake:true, preserveActivePanel:true") == 2
    assert "if(portalDetailPanelIsActive()) return;" in HTML


def test_intake_panel_is_rendered_for_hub_and_legacy_portals():
    # One panel lives in the shared wrapped-panel path (hub or scan history),
    # and the other is the legacy fallback when neither feature is enabled.
    assert HTML.count('data-panel="intake" hidden') == 2
    assert "The onboarding checklist is available independently" in HTML


def test_intake_hash_routes_to_terms_prerequisite_when_gate_is_active():
    assert 'const tosCard = document.getElementById("portal-tos-card")' in HTML
    assert 'if(key === "intake" && tosCard)' in HTML
    assert 'id="portal-tos-card"' in HTML
    assert "applyPortalHash();\n    return;   // suppress the home until agreed" in HTML


def test_terms_gate_is_an_explicit_first_step_of_intake():
    assert 'onboardingMount.hidden = d.tos_agreed === false' in HTML
    assert 'Step 1 of Intake' in HTML
    assert 'Before opening your Intake form' in HTML
    assert 'I agree — continue to Intake' in HTML


def test_native_intake_is_resumable_and_reports_save_state():
    assert '"My Intake", "Build or continue your clinical health profile"' in HTML
    assert "initPortalIntakeCard();" in HTML
    assert "Save and finish later" in HTML
    assert "Section ${sectionIndex + 1} of ${sectionEls.length}" in HTML
    assert 'saveState.textContent = "Saving…"' in HTML
    assert 'saveState.textContent = "Saved"' in HTML
    assert "allowSubmittedEdit: true" in HTML
    assert 'editButton.textContent = "Edit my Intake"' in HTML
    assert 'stateData && stateData.submitted ? "Save changes"' in HTML


def test_unified_clinical_record_contains_health_and_documents():
    assert '"My Clinical Record"' in HTML
    assert 'data-panel="records"' in HTML
    assert 'id="portal-clinical-record"' in HTML
    assert 'id="portal-health-profile"' in HTML
    assert 'id="portal-documents-mount"' in HTML
    assert 'id="portal-historical-intakes-mount"' in HTML
    assert 'data-panel="health"' not in HTML
    assert 'health:   {panel:"records", target:"portal-health-profile"}' in HTML
    assert 'documents:{panel:"records", target:"portal-documents-mount"}' in HTML


def test_life_stress_essence_links_are_readable_in_dark_mode():
    assert ':root[data-theme="dark"] .life-stress-card a' in HTML
    assert ':root[data-theme="dark"] .life-stress-card a:visited' in HTML
    assert "color:#8ecbff" in HTML


def test_selected_scan_and_intake_autosaves_do_not_go_stale():
    assert 'body:JSON.stringify({scan_date:selectedScanDate || ""})' in HTML
    assert "let saveChain = Promise.resolve(true)" in HTML
    assert "await saveChain" in HTML
    assert "initPortalIntakeCard(true)" in HTML


def test_product_links_are_readable_and_open_outside_portal():
    assert ':root[data-theme="dark"] .remitem a' in HTML
    assert 'target="_blank" rel="noopener"' in HTML
