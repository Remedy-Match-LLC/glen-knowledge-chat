/* Persistent Mentorship University mentor for the client portal.
 * Reuses the existing durable Ask Dr. Glen thread and chat endpoint. */
(function initPortalMentor(){
  const launcher=document.getElementById("mentorLauncher"), panel=document.getElementById("mentorPanel");
  const close=document.getElementById("mentorClose"), input=document.getElementById("mentorInput");
  const send=document.getElementById("mentorSend"), msgs=document.getElementById("mentorMsgs");
  const mic=document.getElementById("mentorMic"), speaker=document.getElementById("mentorSpeaker");
  const autoGuide=document.getElementById("mentorAutoGuide"), contextLabel=document.getElementById("mentorContext");
  if(!launcher||!panel||!input||!send||!msgs) return;

  const panelNames={hub:"your healing home",current:"your current analysis",history:"your scan history",
    orders:"your orders and invoices",ask:"Ask Dr. Glen",intake:"your health intake",health:"your health profile",
    oasis:"your Healing Oasis plan",cart:"your cart",shop:"the product shop",calendar:"the live calendar",
    offers:"membership and offers",photo:"your profile photo",finder:"the practitioner finder",
    bodymap:"the body map",classes:"your classes",account:"your account",remedies:"your remedies",
    refer:"the ambassador program",referrals:"your referrals"};
  let speakerOn=true,recognition=null,sending=false,lastGuidedPanel="";
  try{speakerOn=localStorage.getItem("rm_mentor_speaker")!=="off";autoGuide.checked=localStorage.getItem("rm_mentor_auto_guide")==="on"}catch(e){}

  function syncAudioButtons(){speaker.classList.toggle("is-on",speakerOn);speaker.setAttribute("aria-pressed",speakerOn?"true":"false")}
  function activePanel(){const el=Array.from(document.querySelectorAll("[data-panel]")).find(p=>!p.hidden);return el?(el.dataset.panel||"current"):"current"}
  function pageContext(){const key=activePanel(),root=document.querySelector('[data-panel="'+key+'"]')||document.getElementById("app")||document;
    const headings=Array.from(root.querySelectorAll("h1,h2,h3")).filter(h=>h.offsetParent!==null)
      .map(h=>(h.textContent||"").trim()).filter(Boolean).slice(0,8);
    return {panel:key,title:panelNames[key]||"your portal",headings:headings,hash:location.hash||""}}
  function setContext(){const c=pageContext();contextLabel.textContent="Aware you’re viewing "+c.title;return c}
  function append(role,text){const b=document.createElement("div");b.className="mentor-bubble "+role;b.textContent=text||"";msgs.appendChild(b);msgs.scrollTop=msgs.scrollHeight;return b}
  function syncHistory(){msgs.innerHTML="";chatHistory.slice(-20).forEach(m=>append((m.role==="user"||m.role==="client")?"user":"assistant",m.content||""))}
  window.syncMentorHistory=syncHistory;
  function firstName(){const raw=(document.getElementById("portal-client-name")||{}).textContent||"";return raw.trim().split(/\s+/)[0]||""}
  function greeting(){const name=firstName(),c=pageContext();if(c.panel==="hub")return(name?"Aloha, "+name+". ":"Aloha. ")+"Would you like me to guide you through your healing home?";
    return(name?"Welcome back, "+name+". ":"Welcome. ")+"I can help with "+c.title+". What would you like to understand or do next?"}
  function speak(text){if(!speakerOn||!window.speechSynthesis||!text)return;try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.rate=.96;speechSynthesis.speak(u)}catch(e){}}
  function openMentor(activateVoice){panel.hidden=false;launcher.setAttribute("aria-expanded","true");setContext();syncHistory();
    if(!chatHistory.length&&!msgs.children.length){const g=greeting();append("assistant",g);if(activateVoice)speak(g)}input.focus();if(activateVoice&&recognition)try{recognition.start()}catch(e){}}
  function closeMentor(){panel.hidden=true;launcher.setAttribute("aria-expanded","false")}

  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(Recognition){recognition=new Recognition();recognition.lang="en-US";recognition.interimResults=false;recognition.continuous=false;
    recognition.onstart=()=>mic.classList.add("mentor-listening","is-on");
    recognition.onend=recognition.onerror=()=>mic.classList.remove("mentor-listening","is-on");
    recognition.onresult=e=>{input.value=Array.from(e.results).map(r=>r[0].transcript).join(" ");input.focus()}}
  else mic.hidden=true;
  syncAudioButtons();
  launcher.addEventListener("click",()=>{if(panel.hidden){speakerOn=true;try{localStorage.setItem("rm_mentor_speaker","on")}catch(e){}syncAudioButtons();openMentor(true)}else closeMentor()});
  close.addEventListener("click",closeMentor);mic.addEventListener("click",()=>{if(recognition)try{recognition.start()}catch(e){}});
  speaker.addEventListener("click",()=>{speakerOn=!speakerOn;try{localStorage.setItem("rm_mentor_speaker",speakerOn?"on":"off")}catch(e){}syncAudioButtons();if(!speakerOn&&window.speechSynthesis)speechSynthesis.cancel()});
  autoGuide.addEventListener("change",()=>{try{localStorage.setItem("rm_mentor_auto_guide",autoGuide.checked?"on":"off")}catch(e){}
    if(autoGuide.checked){const t="Automatic guidance is on. I’ll quietly orient you when you move to a new part of your portal.";append("assistant",t);speak(t)}});

  async function submit(){const query=(input.value||"").trim();if(!query||sending)return;sending=true;input.value="";input.disabled=true;send.disabled=true;append("user",query);
    const answerBubble=append("assistant","");let answer="";
    try{const resp=await fetch("/api/portal/"+encodeURIComponent(token)+"/chat",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({query:query,page_context:pageContext(),history:chatHistory.map(m=>({role:(m.role==="user"||m.role==="client")?"user":"assistant",content:m.content||""}))})});
      if(!resp.ok)throw new Error("Unable to reach Dr. Glen right now.");const reader=resp.body.getReader(),decoder=new TextDecoder();let buf="",done=false;
      while(!done){const chunk=await reader.read();if(chunk.done)break;buf+=decoder.decode(chunk.value,{stream:true});const lines=buf.split("\n");buf=lines.pop();
        for(const line of lines){if(line.slice(0,6)!=="data: ")continue;try{const evt=JSON.parse(line.slice(6));if(evt.token){answer+=evt.token;answerBubble.textContent=answer;msgs.scrollTop=msgs.scrollHeight}
          else if(evt.error)throw new Error(String(evt.error));else if(evt.done)done=true}catch(e){if(e instanceof SyntaxError)continue;throw e}}}
      chatHistory.push({role:"user",content:query},{role:"assistant",content:answer});repopulateChatHistory();speak(answer)
    }catch(e){answerBubble.textContent=(e&&e.message)||"Something went wrong. Please try again.";answerBubble.classList.add("error")}
    finally{sending=false;input.disabled=false;send.disabled=false;input.focus()}}
  send.addEventListener("click",submit);input.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();submit()}});
  window.mentorPageChanged=function(name){window.setTimeout(()=>{setContext();if(!autoGuide.checked||name===lastGuidedPanel)return;lastGuidedPanel=name;
    const text="You’re now viewing "+(panelNames[name]||"this part of your portal")+". Open me if you’d like an explanation or a recommended next step.";
    if(panel.hidden)openMentor(false);append("assistant",text);speak(text)},0)};
})();
