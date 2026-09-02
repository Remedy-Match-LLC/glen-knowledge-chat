/* Persistent Mentorship University mentor for the client portal.
 * Reuses the existing durable Ask Dr. Glen thread and chat endpoint.
 *
 * Two hosts, one conversation, one thread.
 *
 * Shell OFF (production today): the mentor lives in the floating launcher and
 * panel exactly as it always has. Nothing below changes that path.
 *
 * Shell ON: the "Messages & Order Help" card is the only chat surface, because
 * the card renders a practitioner reply with its own byline and the floating
 * panel collapses it into the AI voice. So the mentor binds its voice controls
 * into the card instead of the panel: the card keeps owning the thread, the
 * rendering and the sending, and the mentor keeps owning the microphone, the
 * spoken replies, the continuous conversation and the automatic page guide.
 * window.mentorAttachHost() re-binds after every render(), because render()
 * rebuilds the card and its controls.
 */
(function initPortalMentor(){
  var h=null;                                   // the host currently bound
  var speakerOn=true,recognition=null,sending=false,lastGuidedPanel="",micActivated=false;
  var listening=false,speaking=false,continuousOn=false,restartTimer=null,restartAttempts=0;
  var recognitionStarting=false,recognitionFatal=false;

  const panelNames={hub:"your healing home",current:"your current analysis",history:"your scan history",
    orders:"your orders and invoices",ask:"Ask Dr. Glen",intake:"your health intake",health:"your health profile",
    oasis:"your Healing Oasis plan",cart:"your cart",shop:"the product shop",calendar:"the live calendar",
    offers:"membership and offers",photo:"your profile photo",finder:"the practitioner finder",
    bodymap:"the body map",classes:"your classes",account:"your account",remedies:"your remedies",
    refer:"the ambassador program",referrals:"your referrals"};

  function byId(id){return document.getElementById(id)}

  // The card wins whenever its voice cluster is on the page. That cluster is
  // rendered only when shell_enabled is true, so with the flag off resolveHost
  // can only ever return the floating panel.
  function resolveHost(){
    var mic=byId("chatMic"),msgs=byId("chatMsgs"),input=byId("chatInput");
    if(mic&&msgs&&input&&byId("chatSpeaker")&&byId("chatAutoGuide")&&byId("chatContinuous")){
      return {card:true,launcher:null,panel:null,close:null,
        input:input,send:byId("chatSend"),msgs:msgs,mic:mic,speaker:byId("chatSpeaker"),
        autoGuide:byId("chatAutoGuide"),contextLabel:byId("chatContext"),
        continuous:byId("chatContinuous"),continuousWrap:byId("chatContinuousWrap")};
    }
    var launcher=byId("mentorLauncher"),panel=byId("mentorPanel"),mInput=byId("mentorInput"),
        mSend=byId("mentorSend"),mMsgs=byId("mentorMsgs");
    if(!launcher||!panel||!mInput||!mSend||!mMsgs)return null;
    return {card:false,launcher:launcher,panel:panel,close:byId("mentorClose"),
      input:mInput,send:mSend,msgs:mMsgs,mic:byId("mentorMic"),speaker:byId("mentorSpeaker"),
      autoGuide:byId("mentorAutoGuide"),contextLabel:byId("mentorContext"),
      continuous:byId("mentorContinuous"),continuousWrap:byId("mentorContinuousWrap")};
  }

  // "Can the client see this host right now?" The floating panel answers with its
  // own hidden flag. The card answers with the panel section it sits in, so the
  // mic does not keep listening after the client walks to another door.
  function hostHidden(){
    if(!h)return true;
    if(!h.card)return !!h.panel.hidden;
    var sec=(h.msgs&&h.msgs.closest)?h.msgs.closest("[data-panel]"):null;
    return sec?!!sec.hidden:false;
  }

  function syncContinuousControl(){
    if(!h||!h.continuous||!h.continuousWrap)return;
    const available=!!(recognition&&micActivated&&speakerOn);
    h.continuousWrap.hidden=!available;
    if(!available&&continuousOn){continuousOn=false;h.continuous.checked=false}
  }
  function syncAudioButtons(){
    if(h&&h.speaker){h.speaker.classList.toggle("is-on",speakerOn);h.speaker.setAttribute("aria-pressed",speakerOn?"true":"false")}
    syncContinuousControl()
  }
  function activePanel(){const el=Array.from(document.querySelectorAll("[data-panel]")).find(p=>!p.hidden);return el?(el.dataset.panel||"current"):"current"}
  function pageContext(){const key=activePanel(),root=document.querySelector('[data-panel="'+key+'"]')||document.getElementById("app")||document;
    const headings=Array.from(root.querySelectorAll("h1,h2,h3")).filter(h2=>h2.offsetParent!==null)
      .map(h2=>(h2.textContent||"").trim()).filter(Boolean).slice(0,8);
    return {panel:key,title:panelNames[key]||"your portal",headings:headings,hash:location.hash||""}}
  function setContext(){const c=pageContext();if(h&&h.contextLabel)h.contextLabel.textContent="Aware you’re viewing "+c.title;return c}
  function append(role,text){if(!h||!h.msgs)return null;
    const b=document.createElement("div");b.className=(h.card?"chat-bubble ":"mentor-bubble ")+role;
    b.textContent=text||"";h.msgs.appendChild(b);h.msgs.scrollTop=h.msgs.scrollHeight;return b}
  // The card renders its own thread through repopulateChatHistory(), which keeps
  // a practitioner reply in its own class with the author byline. Re-rendering it
  // here would collapse Dr. Glen and Rae back into the assistant voice, which is
  // the whole defect this consolidation removes. So on the card this is a no-op.
  function syncHistory(){if(!h||!h.msgs||h.card)return;
    h.msgs.innerHTML="";chatHistory.slice(-20).forEach(m=>append((m.role==="user"||m.role==="client")?"user":"assistant",m.content||""))}
  window.syncMentorHistory=syncHistory;
  function firstName(){const raw=(document.getElementById("portal-client-name")||{}).textContent||"";return raw.trim().split(/\s+/)[0]||""}
  function greeting(){const name=firstName(),c=pageContext();if(c.panel==="hub")return(name?"Aloha, "+name+". ":"Aloha. ")+"Would you like me to guide you through your healing home?";
    return(name?"Welcome back, "+name+". ":"Welcome. ")+"I can help with "+c.title+". What would you like to understand or do next?"}
  function paintMicActive(active){
    if(!h||!h.mic)return;
    h.mic.classList.toggle("mentor-listening",active);h.mic.classList.toggle("is-on",active);
    h.mic.setAttribute("aria-pressed",active?"true":"false");
  }
  function startListening(){
    if(!recognition||listening||recognitionStarting||speaking||sending||hostHidden()||recognitionFatal)return;
    recognitionStarting=true;
    try{recognition.start()}
    catch(e){recognitionStarting=false;scheduleListening(true)}
  }
  function scheduleListening(retry){
    window.clearTimeout(restartTimer);
    if(!continuousOn||recognitionFatal||hostHidden())return;
    // SpeechRecognition may still be tearing down when onend fires. Retry with a
    // short bounded backoff instead of letting one InvalidStateError kill the mic.
    const delay=retry?Math.min(250*Math.pow(2,restartAttempts++),4000):250;
    paintMicActive(true);
    restartTimer=window.setTimeout(startListening,delay);
  }
  function speak(text,listenAfter){
    if(document.hidden)return;
    // document.hidden is about the browser tab. It says nothing about whether the
    // surface the mentor is bound to is on screen, and at page load the floating
    // panel can be bound and live while the shell is on. A host the client cannot
    // see must not speak, and must not schedule a microphone either, which is why
    // this returns rather than falling through to startListening/scheduleListening.
    if(hostHidden())return;
    if(!speakerOn||!window.speechSynthesis||!text){if(listenAfter)startListening();else scheduleListening();return}
    try{
      speaking=true;if(listening)recognition.stop();speechSynthesis.cancel();
      const u=new SpeechSynthesisUtterance(text);u.rate=.96;
      u.onend=u.onerror=()=>{speaking=false;if(listenAfter)startListening();else scheduleListening()};speechSynthesis.speak(u)
    }catch(e){speaking=false;if(listenAfter)startListening();else scheduleListening()}
  }
  function disableContinuous(){
    continuousOn=false;if(h&&h.continuous)h.continuous.checked=false;window.clearTimeout(restartTimer);
    restartAttempts=0;recognitionStarting=false;paintMicActive(listening);
  }
  function openMentor(activateVoice){
    if(!h)return;
    if(h.card){setContext();if(h.input)h.input.focus();return}
    h.panel.hidden=false;h.launcher.setAttribute("aria-expanded","true");setContext();syncHistory();
    let greeted=false;if(!chatHistory.length&&!h.msgs.children.length){const g=greeting();append("assistant",g);greeted=true;if(activateVoice)speak(g,true)}h.input.focus();if(activateVoice&&recognition&&!greeted)startListening()}
  function closeMentor(){
    if(!h||h.card)return;
    h.panel.hidden=true;h.launcher.setAttribute("aria-expanded","false");disableContinuous();if(listening)try{recognition.stop()}catch(e){}}

  function silenceHiddenMentor(){
    window.clearTimeout(restartTimer);speaking=false;recognitionStarting=false;
    try{if(window.speechSynthesis)speechSynthesis.cancel()}catch(e){}
    if(listening)try{recognition.stop()}catch(e){}
  }
  document.addEventListener("visibilitychange",()=>{if(document.hidden)silenceHiddenMentor()});
  window.addEventListener("pagehide",silenceHiddenMentor);

  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(Recognition){recognition=new Recognition();recognition.lang="en-US";recognition.interimResults=false;recognition.continuous=false;
    recognition.onstart=()=>{recognitionStarting=false;restartAttempts=0;listening=true;micActivated=true;recognitionFatal=false;paintMicActive(true);syncContinuousControl()};
    recognition.onend=()=>{recognitionStarting=false;listening=false;if(continuousOn)scheduleListening();else paintMicActive(false)};
    recognition.onerror=e=>{recognitionStarting=false;
      if(e.error==="not-allowed"||e.error==="service-not-allowed"){
        recognitionFatal=true;continuousOn=false;if(h&&h.continuous)h.continuous.checked=false;micActivated=false;paintMicActive(false);syncContinuousControl()
      }else if(continuousOn){scheduleListening(true)}
    };
    recognition.onresult=e=>{if(!h||!h.input)return;h.input.value=Array.from(e.results).map(r=>r[0].transcript).join(" ");h.input.focus();if(continuousOn)submit()}}

  // ---- sending -------------------------------------------------------------
  // On the card the page already owns a sender (retry UI, product suggestions,
  // community cards, basket adds). Reusing it keeps one sender, one renderer and
  // one thread; the mentor only needs to know when the turn is over so it can
  // hand the microphone back.
  function cardSubmit(){
    if(typeof window.sendChatMessage!=="function")return;
    sending=true;
    function finish(){sending=false;if(!speaking)scheduleListening()}
    let p=null;
    try{p=window.sendChatMessage()}catch(e){finish();return}
    if(p&&typeof p.then==="function")p.then(finish,finish);else finish();
  }
  async function panelSubmit(){const query=(h.input.value||"").trim();if(!query||sending)return;sending=true;h.input.value="";h.input.disabled=true;h.send.disabled=true;append("user",query);
    const answerBubble=append("assistant","");let answer="";
    try{const resp=await fetch("/api/portal/"+encodeURIComponent(token)+"/chat",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({query:query,page_context:pageContext(),history:chatHistory.map(m=>({role:(m.role==="user"||m.role==="client")?"user":"assistant",content:m.content||""}))})});
      if(!resp.ok)throw new Error("Unable to reach Dr. Glen right now.");const reader=resp.body.getReader(),decoder=new TextDecoder();let buf="",done=false;
      while(!done){const chunk=await reader.read();if(chunk.done)break;buf+=decoder.decode(chunk.value,{stream:true});const lines=buf.split("\n");buf=lines.pop();
        for(const line of lines){if(line.slice(0,6)!=="data: ")continue;try{const evt=JSON.parse(line.slice(6));if(evt.token){answer+=evt.token;answerBubble.textContent=answer;h.msgs.scrollTop=h.msgs.scrollHeight}
          else if(evt.error)throw new Error(String(evt.error));else if(evt.done)done=true}catch(e){if(e instanceof SyntaxError)continue;throw e}}}
      chatHistory.push({role:"user",content:query},{role:"assistant",content:answer});repopulateChatHistory();speak(answer)
    }catch(e){answerBubble.textContent=(e&&e.message)||"Something went wrong. Please try again.";answerBubble.classList.add("error")}
    finally{sending=false;h.input.disabled=false;h.send.disabled=false;h.input.focus();if(continuousOn&&!speaking)scheduleListening()}}
  function submit(){if(!h)return;return h.card?cardSubmit():panelSubmit()}

  // Called by the card's sender once a reply is complete. Without continuous
  // conversation the reply is spoken the way the card has always spoken it, in
  // Dr. Glen's recorded voice, and the speaker button is now its off switch.
  // With continuous conversation on, the browser voice is used instead because
  // hands-free turn taking needs a reliable end-of-speech signal to hand the
  // microphone back; the recorded voice stays one tap away on the Listen button.
  function onReply(bubble,text){
    const tts=window.TTS;
    if(continuousOn){if(tts)tts.attach(bubble,text);speak(text,true);return}
    if(!tts){if(speakerOn)speak(text);return}
    if(speakerOn)tts.attachAndSpeak(bubble,text);else tts.attach(bubble,text);
  }
  window.PortalVoice={armed:function(){return !!(h&&h.card)},onReply:onReply};

  // ---- host binding --------------------------------------------------------
  function on(el,evt,fn){if(!el)return;if(el.__mentorBound&&el.__mentorBound[evt])return;
    el.__mentorBound=el.__mentorBound||{};el.__mentorBound[evt]=true;el.addEventListener(evt,fn)}

  // Nothing must survive a host switch. render() only runs once the payload has
  // arrived, so with the shell on there is a window at page load where the card
  // does not exist yet, resolveHost() falls back to the floating panel, and that
  // panel is bound, visible and fully usable. A spoken reply or an open microphone
  // started in that window would otherwise keep running against a surface the
  // client can no longer see. Stop everything that can produce or capture sound,
  // and close the panel behind us.
  function releaseHost(){
    if(!h)return;
    continuousOn=false;
    if(h.continuous)h.continuous.checked=false;
    window.clearTimeout(restartTimer);restartAttempts=0;recognitionStarting=false;speaking=false;
    try{if(window.speechSynthesis)speechSynthesis.cancel()}catch(e){}
    if(recognition)try{recognition.stop()}catch(e){}
    paintMicActive(false);
    if(!h.card&&h.panel){h.panel.hidden=true;if(h.launcher)h.launcher.setAttribute("aria-expanded","false")}
  }

  function bindHost(next){
    // Only a change of host KIND is a switch. render() hands us a rebuilt card on
    // every background poll, and tearing down there would end a conversation the
    // client is in the middle of. The first bind has no previous host at all.
    if(h&&h.card!==next.card)releaseHost();
    h=next;
    if(!h)return;
    try{if(h.autoGuide)h.autoGuide.checked=localStorage.getItem("rm_mentor_auto_guide")==="on"}catch(e){}
    if(h.mic&&!recognition)h.mic.hidden=true;
    if(h.continuous)h.continuous.checked=continuousOn;
    syncAudioButtons();
    on(h.launcher,"click",()=>{if(!h||h.card)return;if(h.panel.hidden){speakerOn=true;try{localStorage.setItem("rm_mentor_speaker","on")}catch(e){}syncAudioButtons();openMentor(true)}else closeMentor()});
    on(h.close,"click",closeMentor);
    on(h.mic,"click",()=>{if(!recognition)return;if(listening){disableContinuous();try{recognition.stop()}catch(e){}}else startListening()});
    on(h.speaker,"click",()=>{speakerOn=!speakerOn;try{localStorage.setItem("rm_mentor_speaker",speakerOn?"on":"off")}catch(e){}syncAudioButtons();if(!speakerOn&&window.speechSynthesis){speaking=false;speechSynthesis.cancel()}});
    on(h.continuous,"change",()=>{continuousOn=h.continuous.checked;recognitionFatal=false;restartAttempts=0;
      if(continuousOn){const t="Continuous conversation is on. Speak naturally, and I’ll listen again after each reply.";append("assistant",t);speak(t)}
      else{disableContinuous();if(listening)try{recognition.stop()}catch(e){}}});
    on(h.autoGuide,"change",()=>{try{localStorage.setItem("rm_mentor_auto_guide",h.autoGuide.checked?"on":"off")}catch(e){}
      if(h.autoGuide.checked){const t="Automatic guidance is on. I’ll quietly orient you when you move to a new part of your portal.";append("assistant",t);speak(t)}});
    // The card's own sender is already wired to its input and Send button by
    // render(), so binding here would send every message twice.
    if(!h.card){on(h.send,"click",submit);on(h.input,"keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();submit()}})}
  }
  window.mentorAttachHost=function(){const next=resolveHost();if(next)bindHost(next)};

  try{speakerOn=localStorage.getItem("rm_mentor_speaker")!=="off"}catch(e){}
  window.mentorAttachHost();
  if(!h)return;

  window.mentorPageChanged=function(name){window.setTimeout(()=>{setContext();if(!h||!h.autoGuide||!h.autoGuide.checked||name===lastGuidedPanel)return;lastGuidedPanel=name;
    const text="You’re now viewing "+(panelNames[name]||"this part of your portal")+
      (h.card?". Ask me here if you’d like an explanation or a recommended next step."
             :". Open me if you’d like an explanation or a recommended next step.");
    const wasOpen=!hostHidden();if(!h.card&&h.panel.hidden)openMentor(false);append("assistant",text);
    if(wasOpen&&!document.hidden)speak(text)},0)};
})();
