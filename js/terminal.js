// ===== Integrated terminal (tabbed, Codex-style) =====
// A toggleable bottom panel with multiple terminal tabs. Each tab is its own
// shell (PowerShell on Windows, bash elsewhere) opened in the active project
// folder. "+" adds a tab; the × on a tab closes it. Output streams in through
// window.onTermOutput(id, text) / window.onTermDone(id, code) (called by app.py).
let termOpen = false;
let terms = [];        // tab objects: {id,cwd,shell,local,busy,history,histIdx, screen,out,live,prompt,input}
let activeId = null;
let _localSeq = 0;     // id counter for offline/local fallback tabs

function termById(id){ return terms.find(t => t.id === id); }
function activeTerm(){ return termById(activeId); }

function toggleTerminal(){
  termOpen = !termOpen;
  const panel = document.getElementById('termPanel');
  const btn = document.getElementById('termBtn');
  if(panel) panel.classList.toggle('open', termOpen);
  if(btn) btn.classList.toggle('active', termOpen);
  if(termOpen){
    if(!terms.length) termNewTab();
    else focusActive();
  }
}

function closeTerminalPanel(){
  termOpen = false;
  const panel = document.getElementById('termPanel'); if(panel) panel.classList.remove('open');
  const btn = document.getElementById('termBtn'); if(btn) btn.classList.remove('active');
}

async function termNewTab(){
  let meta = null;
  if(backendReady){
    try{ const r = await window.pywebview.api.term_new(); if(r && r.ok) meta = {id:r.id, cwd:r.cwd||'', shell:r.shell||'Terminal'}; }catch(e){}
  }
  if(!meta){ _localSeq++; meta = {id:'local' + _localSeq, cwd:'', shell:'Terminal', local:true}; }
  const t = buildTab(meta);
  terms.push(t);
  setActive(t.id);
  if(t.local) termWriteTo(t, 'The terminal needs a live Copilot connection (launch via app.py).\n', 'term-sys');
}

function buildTab(meta){
  const bodies = document.getElementById('termBodies');
  const screen = document.createElement('div'); screen.className = 'term-screen'; screen.dataset.tid = meta.id;
  const out = document.createElement('div'); out.className = 'term-out';
  const live = document.createElement('div'); live.className = 'term-liveline';
  const prompt = document.createElement('span'); prompt.className = 'term-prompt';
  const input = document.createElement('input');
  input.className = 'term-input'; input.spellcheck = false; input.autocomplete = 'off'; input.setAttribute('autocapitalize','off');
  live.appendChild(prompt); live.appendChild(input);
  out.appendChild(live); screen.appendChild(out); bodies.appendChild(screen);
  const t = Object.assign({}, meta, {busy:false, history:[], histIdx:-1, screen, out, live, prompt, input});
  out.addEventListener('click', ()=>{ if(!input.disabled) input.focus(); });
  input.addEventListener('keydown', (e)=>onTermKey(e, t));
  if(meta.local) input.disabled = true;
  updatePrompt(t);
  return t;
}

function setActive(id){
  activeId = id;
  terms.forEach(t => t.screen.classList.toggle('active', t.id === id));
  const stop = document.getElementById('termStop'); const t = activeTerm();
  if(stop) stop.disabled = !(t && t.busy);
  renderTabs();
  focusActive();
}
function focusActive(){ const t = activeTerm(); if(t && !t.input.disabled) setTimeout(()=>t.input.focus(), 0); }

async function termCloseTab(id, ev){
  if(ev) ev.stopPropagation();
  const t = termById(id); if(!t) return;
  if(!t.local && backendReady){ try{ await window.pywebview.api.term_close(id); }catch(e){} }
  if(t.screen && t.screen.parentNode) t.screen.parentNode.removeChild(t.screen);
  terms = terms.filter(x => x.id !== id);
  if(activeId === id){
    if(terms.length) setActive(terms[terms.length - 1].id);
    else { activeId = null; renderTabs(); closeTerminalPanel(); }
  } else renderTabs();
}

function tabBasename(cwd){ return (cwd || '').replace(/[\\/]+$/,'').split(/[\\/]/).pop() || (cwd || ''); }
function tabLabel(t){ return tabBasename(t.cwd) || t.shell || 'shell'; }

// Rebuild the tab chips (kept before the "+" button in the strip).
function renderTabs(){
  const strip = document.getElementById('termTabs');
  const plus = document.getElementById('termNewTab');
  if(!strip || !plus) return;
  strip.querySelectorAll('.term-tab').forEach(el => el.remove());
  terms.forEach(t => {
    const tab = document.createElement('div');
    tab.className = 'term-tab' + (t.id === activeId ? ' active' : '');
    tab.title = t.cwd || '';
    tab.onclick = ()=>setActive(t.id);
    tab.innerHTML =
      '<svg viewBox="0 0 16 16"><path d="M3.7 5.5l-.9.9L4.4 8 2.8 9.6l.9.9L6.2 8 3.7 5.5zM7.5 9.5V11h4V9.5h-4z"/></svg>' +
      '<span class="tab-cwd">' + escapeHtml(tabLabel(t)) + '</span>' +
      '<span class="tab-x" title="Close terminal">×</span>';
    tab.querySelector('.tab-x').onclick = (e)=>termCloseTab(t.id, e);
    strip.insertBefore(tab, plus);
  });
}

function promptText(t){
  if(/power|pwsh/i.test(t.shell || '')) return 'PS ' + (t.cwd || '') + '> ';
  return (t.cwd || '') + '$ ';
}
function updatePrompt(t){ if(t && t.prompt) t.prompt.textContent = promptText(t); }

function termWriteTo(t, text, cls){
  if(!t) return;
  const span = document.createElement('span');
  if(cls) span.className = cls;
  span.textContent = text;
  t.out.insertBefore(span, t.live);
  t.out.scrollTop = t.out.scrollHeight;
}

function setBusy(t, on){
  if(!t) return;
  t.busy = on;
  t.live.style.display = on ? 'none' : 'flex';
  t.input.disabled = on;
  if(t.id === activeId){ const stop = document.getElementById('termStop'); if(stop) stop.disabled = !on; }
  if(!on){ updatePrompt(t); if(t.id === activeId) focusActive(); }
}

function runTermCommand(t){
  if(!t || t.busy) return;
  const cmd = t.input.value;
  if(!cmd.trim()) return;
  termWriteTo(t, promptText(t), 'term-prompt-echo');
  termWriteTo(t, cmd + '\n', 'term-cmd');
  t.history.push(cmd); t.histIdx = t.history.length;
  t.input.value = '';
  if(t.local || !backendReady){ termWriteTo(t, 'Not connected.\n', 'term-sys'); return; }
  setBusy(t, true);
  try{ window.pywebview.api.term_run(t.id, cmd); }
  catch(e){ termWriteTo(t, String(e) + '\n', 'term-err'); setBusy(t, false); }
}

function termInterrupt(){
  const t = activeTerm();
  if(!t || !t.busy) return;
  try{ window.pywebview.api.term_interrupt(t.id); }catch(e){}
  termWriteTo(t, '^C\n', 'term-sys');
}

function termClear(){
  const t = activeTerm(); if(!t) return;
  while(t.out.firstChild && t.out.firstChild !== t.live) t.out.removeChild(t.out.firstChild);
  focusActive();
}

// Streamed from app.py.
function onTermOutput(id, text){ const t = termById(id); if(t) termWriteTo(t, text); }
function onTermDone(id, code){
  const t = termById(id); if(!t) return;
  if(typeof code === 'number' && code !== 0) termWriteTo(t, '[exit ' + code + ']\n', 'term-sys');
  if(!t.local && backendReady){
    try{ window.pywebview.api.term_cwd(id).then(r=>{ if(r && r.ok) t.cwd = r.cwd; setBusy(t, false); renderTabs(); }); return; }catch(e){}
  }
  setBusy(t, false);
}

function onTermKey(e, t){
  if(e.key === 'Enter'){ e.preventDefault(); runTermCommand(t); return; }
  if(e.ctrlKey && (e.key === 'c' || e.key === 'C')){ e.preventDefault(); termInterrupt(); return; }
  if(e.key === 'ArrowUp'){ if(t.history.length){ e.preventDefault(); t.histIdx = Math.max(0, t.histIdx - 1); e.target.value = t.history[t.histIdx] || ''; } return; }
  if(e.key === 'ArrowDown'){ if(t.history.length){ e.preventDefault(); t.histIdx = Math.min(t.history.length, t.histIdx + 1); e.target.value = t.history[t.histIdx] || ''; } return; }
}

// Ctrl+` toggles the terminal (like VS Code / Codex).
document.addEventListener('keydown', (e)=>{
  if((e.ctrlKey || e.metaKey) && e.key === '`'){ e.preventDefault(); toggleTerminal(); }
});
