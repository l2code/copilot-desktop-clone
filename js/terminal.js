// ===== Integrated terminal =====
// A toggleable panel that runs commands in the active project folder via the
// backend (PowerShell on Windows, bash elsewhere). It behaves like a single
// terminal window: output and the live input line share one scrolling area, so
// the cursor sits inline at the bottom rather than in a separate textbox.
// Output streams in through window.onTermOutput / window.onTermDone (app.py).
let termOpen = false;
let termBusy = false;
let termCwd = '';
let termShellName = 'Terminal';
let termHistory = [];   // command history for Up/Down recall
let termHistIdx = -1;

function toggleTerminal(){
  termOpen = !termOpen;
  const panel = document.getElementById('termPanel');
  const btn = document.getElementById('termBtn');
  if(panel) panel.classList.toggle('open', termOpen);
  if(btn) btn.classList.toggle('active', termOpen);
  if(termOpen) openTerminal();
}

async function openTerminal(){
  if(!backendReady){
    termWrite('The terminal needs a live Copilot connection (launch via app.py).\n', 'term-sys');
    return;
  }
  try{
    const r = await window.pywebview.api.term_init();
    if(r && r.ok){
      termShellName = r.shell || 'Terminal';
      const sh = document.getElementById('termShell'); if(sh) sh.textContent = termShellName;
      termSetCwd(r.cwd || '');
    }
  }catch(e){ /* non-fatal */ }
  termFocus();
}

// PowerShell-style prompt, falls back to $ for bash.
function termPromptText(){
  if(/power|pwsh/i.test(termShellName)) return 'PS ' + (termCwd || '') + '> ';
  return (termCwd || '') + '$ ';
}
function updateLivePrompt(){
  const p = document.getElementById('termPrompt'); if(p) p.textContent = termPromptText();
}

// Reflect the active folder in the prompt + header.
function termSetCwd(path){
  termCwd = path || '';
  const lbl = document.getElementById('termCwdLabel');
  if(lbl){ lbl.textContent = termCwd; lbl.title = termCwd; }
  updateLivePrompt();
}

function termFocus(){
  const inp = document.getElementById('termInput');
  if(inp && !inp.disabled) setTimeout(()=>inp.focus(), 0);
}

function termClear(){
  const o = document.getElementById('termOut'); const live = document.getElementById('termLive');
  if(!o) return;
  while(o.firstChild && o.firstChild !== live) o.removeChild(o.firstChild);
  termFocus();
}

// Append output just above the live input line so the prompt stays at the bottom.
function termWrite(text, cls){
  const o = document.getElementById('termOut'); const live = document.getElementById('termLive');
  if(!o) return;
  const span = document.createElement('span');
  if(cls) span.className = cls;
  span.textContent = text;
  if(live) o.insertBefore(span, live); else o.appendChild(span);
  o.scrollTop = o.scrollHeight;
}

function termSetBusy(on){
  termBusy = on;
  const stop = document.getElementById('termStop'); if(stop) stop.disabled = !on;
  const inp = document.getElementById('termInput'); if(inp) inp.disabled = on;
  const live = document.getElementById('termLive'); if(live) live.style.display = on ? 'none' : 'flex';
  if(!on){ updateLivePrompt(); termFocus(); }
}

function runTermCommand(){
  if(termBusy) return;
  const inp = document.getElementById('termInput');
  const cmd = inp.value;
  if(!cmd.trim()){ return; }
  // freeze the typed line into the scrollback (prompt + command), then run it
  termWrite(termPromptText(), 'term-prompt-echo');
  termWrite(cmd + '\n', 'term-cmd');
  termHistory.push(cmd); termHistIdx = termHistory.length;
  inp.value = '';
  if(!backendReady){ termWrite('Not connected.\n', 'term-sys'); return; }
  termSetBusy(true);
  try{ window.pywebview.api.term_run(cmd); }
  catch(e){ termWrite(String(e) + '\n', 'term-err'); termSetBusy(false); }
}

function termInterrupt(){
  if(!termBusy) return;
  try{ window.pywebview.api.term_interrupt(); }catch(e){}
  termWrite('^C\n', 'term-sys');
}

// Called by app.py as output streams in.
function onTermOutput(text){ termWrite(text); }
function onTermDone(code){
  if(typeof code === 'number' && code !== 0){ termWrite('[exit ' + code + ']\n', 'term-sys'); }
  // a `cd` may have changed the folder -- refresh the prompt before re-enabling
  if(backendReady){
    try{ window.pywebview.api.term_cwd().then(r=>{ if(r && r.ok) termCwd = r.cwd; termSetBusy(false); }); return; }catch(e){}
  }
  termSetBusy(false);
}

function onTermKey(e){
  if(e.key === 'Enter'){ e.preventDefault(); runTermCommand(); return; }
  if(e.ctrlKey && (e.key === 'c' || e.key === 'C')){ e.preventDefault(); termInterrupt(); return; }
  if(e.key === 'ArrowUp'){
    if(termHistory.length){ e.preventDefault(); termHistIdx = Math.max(0, termHistIdx - 1); e.target.value = termHistory[termHistIdx] || ''; }
    return;
  }
  if(e.key === 'ArrowDown'){
    if(termHistory.length){ e.preventDefault(); termHistIdx = Math.min(termHistory.length, termHistIdx + 1); e.target.value = termHistory[termHistIdx] || ''; }
    return;
  }
}

// Ctrl+` toggles the terminal (like VS Code / Codex).
document.addEventListener('keydown', (e)=>{
  if((e.ctrlKey || e.metaKey) && e.key === '`'){ e.preventDefault(); toggleTerminal(); }
});
