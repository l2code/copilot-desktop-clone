
let MODELS = ["auto"];
let modelIdx = 0;
let started = false;

// ===== Backend wiring =====
// Launched via app.py (pywebview) -> window.pywebview.api talks to the real
// Copilot SDK. Opened as a plain .html file -> demo simulation.
let backendReady = false;
let curTarget = null;   // assistant <div.m-content> currently streaming
let curBuf = "";        // accumulated raw markdown for the current reply
let backendStartTimer = null;
let pywebviewReadySeen = false;

window.addEventListener('pywebviewready', ()=>{
  pywebviewReadySeen = true;
  initBackend();
});

setTimeout(()=>{
  if(pywebviewReadySeen) return;
  showConnecting(true, 'Starting WebView bridge');
  try{
    showBanner('warn', 'The WebView bridge is still starting. Close other Copilot Desktop windows or use run-copilot-mode.bat for an isolated profile.');
  }catch(e){}
}, 25000);

async function initBackend(){
  setStatus('warn');
  let startup = {};
  try{ startup = await window.pywebview.api.get_startup_options(); }catch(e){ startup = {}; }
  // Show saved conversations immediately (history file; independent of the session).
  try{ conversations = (await window.pywebview.api.list_conversations()) || []; }catch(e){ conversations = []; }
  renderSidebar();
  await loadCommands();
  if(startup && startup.skip_copilot_start){
    showConnecting(false);
    setStatus('warn');
    setAccount(null);
    showActionBanner('warn', 'GitHub Copilot startup is skipped. Workspace and GitLab tools are available.', 'Connect Copilot', 'connectCopilot');
    return;
  }
  await connectCopilot(false);
}

async function connectCopilot(force){
  if(backendStartTimer){ clearTimeout(backendStartTimer); backendStartTimer = null; }
  backendReady = false;
  setStatus('warn');
  showConnecting(true, 'Connecting to GitHub Copilot');   // spinner + animated dots while start() runs
  try{
    const res = force ? await window.pywebview.api.reconnect_copilot() : await window.pywebview.api.start();
    if(res && res.starting){
      backendStartTimer = setTimeout(()=>{
        if(!backendReady){
          showConnecting(false);
          setStatus('warn');
          showActionBanner('warn', 'Copilot is still connecting in the background. Workspace and GitLab tools are available.', 'Retry', 'reconnectCopilot');
        }
      }, 45000);
    } else if(res && res.ready){
      await handleBackendResult(res);
    } else {
      await handleBackendResult(res);
    }
  }catch(e){
    showConnecting(false);
    setStatus('err');
    showActionBanner('err','Could not reach the backend: ' + e, 'Retry', 'reconnectCopilot');
  }
}

async function reconnectCopilot(){
  await connectCopilot(true);
}

async function handleBackendResult(res){
  if(backendStartTimer){ clearTimeout(backendStartTimer); backendStartTimer = null; }
  showConnecting(false);
  if(res && res.ok){
    backendReady = true;
    setStatus('ok');
    showAuth(false);
    document.getElementById('bannerHost').innerHTML = '';
    if(res.models && res.models.length){
      MODELS = res.models; modelIdx = 0;
      document.getElementById('modelName').textContent = MODELS[0];
    }
    if(res.workdir) setWdDisplay(res.workdir);
    setAccount(res.login);
    await loadConversations();
    await loadCommands();
    newChat();
    refreshUsage();
    if(res.warning){
      showActionBanner('warn', res.warning, 'Open settings', 'openSettings');
    } else if(res.discovery === false){
      showActionBanner('warn', 'Copilot is connected. MCP/config discovery is off for faster startup; turn it on in Settings when you want discovered MCP servers.', 'Open settings', 'openSettings');
    }
    if(typeof checkToolBudget === 'function') checkToolBudget();   // pre-count tools vs the 128 cap
    if(typeof refreshWorkbench === 'function' && typeof wbTab !== 'undefined' && wbTab === 'gitlab'){
      refreshWorkbench();
    }
  } else if(res && res.needsAuth){
    backendReady = false;
    setStatus('warn');
    if(res.host){
      const host = document.getElementById('authHost');
      if(host) host.value = res.host;
    }
    showAuth(true);
  } else {
    backendReady = false;
    setStatus('err');
    showActionBanner('err','Copilot not connected: ' + ((res&&res.error)||'unknown error') + '.', 'Retry', 'reconnectCopilot');
  }
}

async function onBackendReady(res){
  await handleBackendResult(res);
}

// Animated "Connecting…" overlay over the main area.
function showConnecting(show, msg){
  const o = document.getElementById('connectingOverlay'); if(!o) return;
  if(msg){ const t = document.getElementById('connectingText'); if(t) t.textContent = msg; }
  o.classList.toggle('show', !!show);
}

// connection status dot next to the plan name (lower-left)
function setStatus(kind){
  const d = document.getElementById('statusDot');
  if(d) d.className = 'status-dot ' + (kind || '');
}
function setAccount(login){
  const name = login || 'GitHub account';
  const user = document.getElementById('userName');
  const avatar = document.getElementById('userAvatar');
  if(user) user.textContent = name;
  if(avatar){
    if(!login){ avatar.textContent = 'GH'; return; }
    const parts = String(name).replace(/[^a-zA-Z0-9 _.-]/g,'').split(/[\s._-]+/).filter(Boolean);
    const initials = parts.length > 1 ? (parts[0][0] + parts[1][0]) : String(name).slice(0,2);
    avatar.textContent = (initials || 'GH').toUpperCase();
  }
}
// error/info banner (only used for failures now, not the connected state)
function showBanner(kind, text){
  document.getElementById('bannerHost').innerHTML =
    `<div class="banner ${kind}"><span class="dot"></span><span>${escapeHtml(text)}</span></div>`;
}
function showActionBanner(kind, text, label, action){
  const safeAction = /^[a-zA-Z_$][\w$]*$/.test(action || '') ? action : '';
  const button = safeAction ? `<button type="button" onclick="${safeAction}()">${escapeHtml(label || 'Open')}</button>` : '';
  document.getElementById('bannerHost').innerHTML =
    `<div class="banner ${kind}"><span class="dot"></span><span>${escapeHtml(text)}</span>${button}</div>`;
}
// ===== In-app sign-in (device flow) =====
function showAuth(needed, msg){
  const o = document.getElementById('authOverlay'); if(!o) return;
  o.classList.toggle('show', !!needed);
  if(msg) document.getElementById('authMsg').textContent = msg;
}
async function startSignIn(){
  const btn = document.getElementById('authBtn');
  const host = (document.getElementById('authHost') || {}).value || '';
  btn.disabled = true; btn.textContent = 'Opening sign-in\u2026';
  document.getElementById('authMsg').textContent = 'A GitHub sign-in window is opening \u2014 choose GitHub.com, Enterprise (GHE.com), or your provider there. (If a code appears below, enter it at the shown link.)';
  try{
    const res = await window.pywebview.api.sign_in(host.trim());
    if(res && res.ok === false){
      btn.disabled = false; btn.textContent = 'Try again';
      document.getElementById('authMsg').textContent = 'Sign-in could not start: ' + (res.error || 'unknown error') + '.';
    }
  }catch(e){
    btn.disabled=false; btn.textContent='Sign in';
    document.getElementById('authMsg').textContent = 'Sign-in could not start: ' + e + '.';
  }
}
// Re-check auth without launching the in-app device flow -- for when the user has
// already signed in elsewhere (e.g. the Copilot CLI). Just re-runs start().
async function recheckAuth(){
  const link = document.getElementById('authRecheck');
  if(link){ link.disabled = true; link.textContent = 'Checking…'; }
  let res;
  try{ res = await window.pywebview.api.start(); }catch(e){ res = null; }
  if(res && (res.ok || res.starting)){
    if(res.starting){
      showConnecting(true, 'Checking GitHub Copilot sign-in');
      if(link){ link.textContent = 'Checking…'; }
      return;
    }
    await handleBackendResult(res);
    if(res.ok) flashBanner('Signed in' + (res.login ? ' as ' + res.login : ''));
    return;
  }
  if(link){ link.disabled = false; link.textContent = 'Already signed in (e.g. via the Copilot CLI)? Re-check'; }
  document.getElementById('authMsg').textContent =
    'Still not signed in. If you just logged in with the Copilot CLI, give it a moment, then re-check — or restart the app.';
}
function onAuthCode(url, code){
  const c = document.getElementById('authCode'); c.style.display = 'block';
  c.innerHTML = `Open <a href="${escapeAttr(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a><br>and enter code:&nbsp; <b>${escapeHtml(code)}</b>`;
}
function onAuthStatus(msg){ const m=document.getElementById('authMsg'); if(m) m.textContent = msg || ''; }
async function onAuthDone(res){
  const btn = document.getElementById('authBtn');
  if(res && res.ok){
    await handleBackendResult(res);
    flashBanner('Signed in' + (res.login ? ' as ' + res.login : ''));
  } else {
    btn.disabled = false; btn.textContent = 'Try again';
    document.getElementById('authCode').style.display = 'none';
    document.getElementById('authMsg').textContent = 'Sign-in failed: ' + ((res && res.error) || 'unknown') + '.';
  }
}

// ===== Model dropdown =====
function renderModelMenu(){
  const menu = document.getElementById('modelMenu');
  menu.innerHTML = MODELS.map((m,i)=>
    `<div class="model-opt ${i===modelIdx?'sel':''}" onclick="selectModel(${i})">
       <span class="check"><svg viewBox="0 0 16 16"><path d="M6.5 11.6 3.4 8.5l1-1 2.1 2.1L11.6 4.4l1 1z"/></svg></span>
       <span>${escapeHtml(m)}</span></div>`).join('');
}
function toggleModelMenu(e){
  e.stopPropagation();
  renderModelMenu();
  document.getElementById('modelDropdown').classList.toggle('open');
}
function openModelMenu(){ renderModelMenu(); document.getElementById('modelDropdown').classList.add('open'); }
function selectModel(i){
  modelIdx = i;
  const name = MODELS[i];
  document.getElementById('modelName').textContent = name;
  document.getElementById('modelDropdown').classList.remove('open');
  if(backendReady) window.pywebview.api.set_model(name);
}
// close the menu when clicking elsewhere
document.addEventListener('click', (e)=>{
  const dd = document.getElementById('modelDropdown');
  if(dd && !dd.contains(e.target)) dd.classList.remove('open');
  const comp = document.querySelector('.composer');
  if(comp && !comp.contains(e.target)){ closeSlash(); closeAt(); }
  const md = document.getElementById('modeDD');
  if(md && !md.contains(e.target)) md.classList.remove('open');
  const pmw = document.getElementById('projMenuWrap');
  if(pmw && !pmw.contains(e.target)) closeProjMenu();
});

// ===== Keyboard shortcuts =====
function closeTopOverlay(){
  if(slashOpen){ closeSlash(); return true; }
  if(atOpen){ closeAt(); return true; }
  const dd = document.getElementById('modelDropdown');
  if(dd && dd.classList.contains('open')){ dd.classList.remove('open'); return true; }
  const mdd = document.getElementById('modeDD');
  if(mdd && mdd.classList.contains('open')){ mdd.classList.remove('open'); return true; }
  const cm = document.getElementById('confirmModal');
  if(cm && cm.classList.contains('open')){ confirmCancel(); return true; }
  const pm = document.getElementById('permModal');
  if(pm && pm.classList.contains('open')){ resolvePerm('reject'); return true; }  // Esc on a permission prompt = reject
  for(const id of ['instrModal','mcpModal','usageModal','settingsModal']){
    const m = document.getElementById(id);
    if(m && m.classList.contains('open')){ m.classList.remove('open'); return true; }
  }
  const sp = document.getElementById('sidePanel');
  if(sp && sp.classList.contains('open')){ sp.classList.remove('open'); return true; }
  return false;
}
document.addEventListener('keydown', (e)=>{
  const mod = e.ctrlKey || e.metaKey;
  if(e.key === 'Escape'){ if(closeTopOverlay()) e.preventDefault(); return; }
  if(mod && e.key === 'Enter'){
    if(e.target && e.target.id === 'input') return;   // composer's own handler sends it
    e.preventDefault();
    const b = document.getElementById('send'); if(b && !b.disabled) onSendOrStop();
    return;
  }
  if(mod && (e.key === 'n' || e.key === 'N')){ e.preventDefault(); newChat(); return; }
  if(mod && (e.key === 'k' || e.key === 'K')){
    e.preventDefault();
    const s = document.getElementById('chatSearch'); if(s){ s.focus(); s.select(); }
    return;
  }
  if(mod && (e.key === 'b' || e.key === 'B')){ e.preventDefault(); toggleSidebar(); return; }
  // Up-arrow in an empty composer recalls the last message for editing/resend.
  if(e.key === 'ArrowUp' && e.target && e.target.id === 'input'
     && !e.target.value.trim() && !slashOpen && !atOpen){
    const last = [...currentMessages].reverse().find(m=>m.role==='user');
    if(last){
      e.preventDefault();
      e.target.value = last.content; autoGrow(e.target); toggleSend();
      e.target.setSelectionRange(e.target.value.length, e.target.value.length);
    }
    return;
  }
});
// Paste an image from the clipboard -> BlobAttachment
(function(){
  const ta = document.getElementById('input'); if(!ta) return;
  ta.addEventListener('paste', (e)=>{
    const items = e.clipboardData && e.clipboardData.items; if(!items) return;
    for(const it of items){
      if(it.type && it.type.indexOf('image/') === 0){
        const file = it.getAsFile(); if(!file) continue;
        const reader = new FileReader();
        reader.onload = ()=>{
          const ext = (file.type || 'image/png').split('/')[1] || 'png';
          pendingAttachments.push({type:'blob', data:reader.result, mimeType:file.type || 'image/png', displayName:'pasted-image.' + ext});
          renderAttachments(); toggleSend();
        };
        reader.readAsDataURL(file);
        e.preventDefault();
      }
    }
  });
})();
(function(){
  const t = document.getElementById('thread');
  if(!t) return;
  t.addEventListener('scroll', ()=>{
    const atBottom = t.scrollHeight - t.scrollTop - t.clientHeight < 60;
    autoScroll = atBottom;
    const b = document.getElementById('scrollBtn');
    if(b) b.classList.toggle('show', !atBottom);
  });
})();
