import shutil, subprocess, textwrap
import pytest

@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_render_onboarding_emits_phases_done_and_link():
    js = textwrap.dedent('''
      const fs = require('fs');
      const src = fs.readFileSync('static/js/portal-onboarding.js','utf8');
      const mod = {exports:{}};
      // The checklist itself lives in static/js/portal-conditions.js, which the
      // page loads from its own <script src> ahead of this one. Evaluating this
      // source outside node's module system means supplying it the same way.
      const win = {};
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-conditions.js','utf8'))({exports:{}}, {}, win);
      new Function('module','exports','window', src)(mod, mod.exports, win);
      const status = {
        phases: [
          {key:'be_read', title:'Discover What Your Body Is Saying', steps:[
            {key:'voice', label:'Voice analysis', done:true, href:'https://truly.vip/E4L'},
            {key:'intake', label:'Intake', done:false, href:'https://truly.vip/Join'}
          ]},
          {key:'match', title:'Match remedies', steps:[
            {key:'history', label:'Match Remedies', done:false, href:'#recs'}
          ]},
          {key:'heal', title:'Accelerate healing', steps:[
            {key:'light', label:'Light', done:false, href:'https://clinicalpraxis.com', checkable:true},
            {key:'pemf', label:'PEMF', done:false, href:'', soon:true, checkable:true}
          ]}
        ],
        history_conditions_done: false,
        member: false
      };
      const html = (mod.exports.renderOnboarding || global.renderOnboarding)(status);
      if (!/Discover what your body is saying/.test(html)) { console.error('missing discover title'); process.exit(1); }
      if ((html.match(/<h3>/g) || []).length !== 2) { console.error('match rendered as a duplicate heading'); process.exit(1); }
      if (!/<a[^>]*href="#recs"[^>]*>Match Remedies<\\/a>/.test(html)) { console.error('match is not in discover checklist'); process.exit(1); }
      if (!/>Member<\\/a>|>Member<\\/li>/.test(html)) { console.error('member is not in discover checklist'); process.exit(1); }
      if (!/Accelerate healing/.test(html)) { console.error('missing heal title'); process.exit(1); }
      if (!/\\u2713/.test(html)) { console.error('missing done check'); process.exit(1); }
      if (!/<a[^>]*href="https:\\/\\/clinicalpraxis\\.com"[^>]*>Light<\\/a>/.test(html)) {
        console.error('missing heal link anchor'); process.exit(1);
      }
      if (!/coming soon/.test(html)) { console.error('missing soon badge'); process.exit(1); }
      if ((html.match(/class="ob-accelerator-check"/g) || []).length !== 2) {
        console.error('accelerator steps are not checkable'); process.exit(1);
      }
      if (!/<span class="ob-mark ob-mark-done">\\u2713<\\/span>/.test(html)) {
        console.error('done step missing ob-mark-done class'); process.exit(1);
      }
      if (!/<span class="ob-mark ob-mark-open">\\u25cb<\\/span>/.test(html)) {
        console.error('open step missing ob-mark-open class'); process.exit(1);
      }
      console.log('ok');
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_render_onboarding_shows_intake_page_progress():
    js = textwrap.dedent('''
      const fs = require('fs');
      const src = fs.readFileSync('static/js/portal-onboarding.js','utf8');
      const mod = {exports:{}};
      // The checklist itself lives in static/js/portal-conditions.js, which the
      // page loads from its own <script src> ahead of this one. Evaluating this
      // source outside node's module system means supplying it the same way.
      const win = {};
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-conditions.js','utf8'))({exports:{}}, {}, win);
      new Function('module','exports','window', src)(mod, mod.exports, win);
      const html = mod.exports.renderOnboarding({member:false, phases:[
        {key:'be_read', title:'Read', steps:[{key:'intake', label:'Intake', done:false,
          in_progress:true, href:'#intake', progress:{completed:2,total:5,percent:40}}]}
      ]});
      if (!/ob-progress-fill[^>]*width:40%/.test(html)) process.exit(1);
      if (!/2 of 5 pages/.test(html)) process.exit(2);
      if (!/Intake 40% complete/.test(html)) process.exit(3);
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_completed_biofield_checklist_opens_latest_report():
    """Only a checked Biofield item is promoted to the latest-report link."""
    from dashboard import portal_onboarding

    source = open("static/client-portal.html", encoding="utf-8").read()
    onboarding_source = open(portal_onboarding.__file__, encoding="utf-8").read()

    assert '"#biofield-latest" if biofield_done else "#biofield-order"' in onboarding_source
    assert '"biofield-latest": {panel:"current", target:"biofield-section", latest:true}' in source
    assert '"biofield-order": {panel:"current", target:"biofield-order-card"}' in source
    assert 'id="biofield-order-card"' in source
    assert 'id="biofieldOrderBtn"' in source
    assert 'function startBiofieldOrder(btn)' in source
    assert 'latestUrl.searchParams.delete("scan_date")' in source
    assert 'location.replace(latestUrl.toString())' in source


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_render_onboarding_triage_form_gated_on_history_done():
    js = textwrap.dedent('''
      const fs = require('fs');
      const src = fs.readFileSync('static/js/portal-onboarding.js','utf8');
      const mod = {exports:{}};
      // The checklist itself lives in static/js/portal-conditions.js, which the
      // page loads from its own <script src> ahead of this one. Evaluating this
      // source outside node's module system means supplying it the same way.
      const win = {};
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-conditions.js','utf8'))({exports:{}}, {}, win);
      new Function('module','exports','window', src)(mod, mod.exports, win);
      const render = mod.exports.renderOnboarding || global.renderOnboarding;

      function baseStatus(historyDone, member) {
        return {
          phases: [
            {key:'be_read', title:'Discover What Your Body Is Saying', steps:[
              {key:'voice', label:'Voice analysis', done:true, href:'https://truly.vip/E4L'}
            ]},
            {key:'match', title:'Match remedies', steps:[
              {key:'history', label:'Match Remedies', done:historyDone, href:'#recs'}
            ]},
            {key:'heal', title:'Accelerate healing', steps:[
              {key:'light', label:'Light', done:null, href:'https://clinicalpraxis.com'}
            ]}
          ],
          history_conditions_done: historyDone,
          member: member
        };
      }

      // (a) history NOT done -> the condition checklist is present.
      const htmlNotDone = render(baseStatus(false, false));
      if (!/ob-triage-form/.test(htmlNotDone)) { console.error('triage form missing when history.done===false'); process.exit(1); }
      if (!/name="iop_od"/.test(htmlNotDone)) { console.error('missing iop_od input'); process.exit(1); }
      if (!/name="iop_os"/.test(htmlNotDone)) { console.error('missing iop_os input'); process.exit(1); }
      const required = ['glaucoma','cataract','macular','dry-eye','retinitis-pigmentosa','diabetic-retinopathy','other'];
      for (const condition of required) {
        if (!htmlNotDone.includes('value="' + condition + '"')) {
          console.error('missing condition ' + condition); process.exit(1);
        }
      }
      const systemic = ['symptom-fatigue','symptom-brain-fog','symptom-stress',
        'symptom-sleep','symptom-headache','symptom-digestion','symptom-constipation',
        'symptom-immune','symptom-skin','symptom-blood-sugar'];
      for (const symptom of systemic) {
        if (!htmlNotDone.includes('value="' + symptom + '"')) {
          console.error('missing systemic symptom ' + symptom); process.exit(1);
        }
      }
      if (!/name="other_condition"/.test(htmlNotDone)) {
        console.error('missing Other free-text field'); process.exit(1);
      }
      if (/What are you currently taking|More about your health history/.test(htmlNotDone)) {
        console.error('duplicated Intake history sections are still visible'); process.exit(1);
      }

      // (a) history IS done -> the triage form is absent.
      const htmlDone = render(baseStatus(true, false));
      if (/ob-triage-form/.test(htmlDone)) { console.error('triage form present when history.done===true'); process.exit(1); }

      console.log('ok');
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_render_onboarding_member_thread():
    js = textwrap.dedent('''
      const fs = require('fs');
      const src = fs.readFileSync('static/js/portal-onboarding.js','utf8');
      const mod = {exports:{}};
      // The checklist itself lives in static/js/portal-conditions.js, which the
      // page loads from its own <script src> ahead of this one. Evaluating this
      // source outside node's module system means supplying it the same way.
      const win = {};
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-conditions.js','utf8'))({exports:{}}, {}, win);
      new Function('module','exports','window', src)(mod, mod.exports, win);
      const render = mod.exports.renderOnboarding || global.renderOnboarding;

      function baseStatus(member) {
        return {
          phases: [
            {key:'match', title:'Match remedies', steps:[
              {key:'history', label:'Starter remedies from your history', done:true, href:'#recs'}
            ]},
            {key:'heal', title:'Accelerate healing', steps:[
              {key:'light', label:'Light', done:null, href:'https://clinicalpraxis.com'}
            ]}
          ],
          member: member
        };
      }

      const htmlNonMember = render(baseStatus(false));
      if (!/href="#offers"[^>]*>Member<\\/a>/.test(htmlNonMember)) { console.error('missing Member upgrade link'); process.exit(1); }
      if (/ob-mark-done">✓<\\/span> Member/.test(htmlNonMember)) { console.error('non-member should not show member check'); process.exit(1); }

      const htmlMember = render(baseStatus(true));
      if (!/ob-mark-done">\\u2713<\/span> Member/.test(htmlMember)) {
        console.error('missing member marker for member:true'); process.exit(1);
      }
      if (/href="#offers"[^>]*>Member<\\/a>/.test(htmlMember)) { console.error('member should not see Member upgrade link'); process.exit(1); }

      console.log('ok');
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_triage_success_message_adds_biofield_nudge_when_consult_recommended():
    """Consult nudge (cataract/macular triage): the success message stays the
    short baseline copy normally, and additionally invites a Biofield
    Analysis with Dr. Glen when the triage response's consult_recommended
    flag is true (e.g. a wet-AMD resolve)."""
    js = textwrap.dedent('''
      const fs = require('fs');
      const src = fs.readFileSync('static/js/portal-onboarding.js','utf8');
      const mod = {exports:{}};
      // The checklist itself lives in static/js/portal-conditions.js, which the
      // page loads from its own <script src> ahead of this one. Evaluating this
      // source outside node's module system means supplying it the same way.
      const win = {};
      new Function('module','exports','window',
        fs.readFileSync('static/js/portal-conditions.js','utf8'))({exports:{}}, {}, win);
      new Function('module','exports','window', src)(mod, mod.exports, win);
      const buildMsg = mod.exports._triageSuccessMessage;

      const plain = buildMsg(false);
      if (!/starter remedies are ready/.test(plain)) { console.error('missing baseline copy'); process.exit(1); }
      if (/Biofield/.test(plain)) { console.error('non-consult message should not mention Biofield'); process.exit(1); }

      const nudged = buildMsg(true);
      if (!/starter remedies are ready/.test(nudged)) { console.error('nudged message dropped baseline copy'); process.exit(1); }
      if (!/Biofield Analysis/.test(nudged)) { console.error('missing Biofield Analysis nudge'); process.exit(1); }
      if (!/Dr\\. Glen/.test(nudged)) { console.error('missing Dr. Glen mention'); process.exit(1); }
      const nudgeOnly = nudged.split('ready.')[1] || '';
      if (/—/.test(nudgeOnly)) { console.error('nudge text must not use an em dash'); process.exit(1); }
      if (/\\b[A-Z]{4,}\\b/.test(nudgeOnly)) { console.error('nudge text must not use ALL CAPS'); process.exit(1); }

      console.log('ok');
    ''')
    out = subprocess.run(["node", "-e", js], cwd=".", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_successful_triage_refreshes_full_portal_and_opens_remedies():
    """Saving starter remedies must refresh v.remedies, not just remove the
    completed onboarding form from its independent mount."""
    onboarding = open("static/js/portal-onboarding.js", encoding="utf-8").read()
    portal = open("static/client-portal.html", encoding="utf-8").read()

    assert "window.refreshPortalAfterStarterRemedies();" in onboarding
    assert "window.refreshPortalAfterStarterRemedies = async function()" in portal
    assert 'history.pushState(null, "", "#remedies")' in portal
    assert "await load();" in portal
    assert 'remedies: {panel:"remedies", target:"remedies-panel"}' in portal
    assert 'id="remedies-panel" data-panel="remedies"' in portal
