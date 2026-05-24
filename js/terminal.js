// ===== Integrated terminal =====
// A toggleable panel that runs commands in the active project folder via the
// backend (PowerShell on Windows, bash elsewhere). Output streams in through
// window.onTermOutput / window.onTermDone, which app.py calls.
let termOpen = false;
let termBusy = false;
let termCwd = '';
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
      termCwd = r.cwd || '';
      const sh = document.getElementById('termShell'); if(sh) sh.textContent = r.shell || 'Terminal';
      termSetCwd(termCwd);
    }
  }catch(e){ /* non-fatal */ }
  const inp = document.getElementById('termInput'); if(inp) setTimeout(()=>inp.focus(), 30);
}

// Reflect the active folder in the terminal prompt/header.
function termSetCwd(path){
  termCwd = path || '';
  const lbl = document.getElementById('termCwdLabel');
  if(lbl){ lbl.textContent = termCwd; lbl.title = termCwd; }
}

function termClear(){ const o = document.getElementById('termOut'); if(o) o.innerHTML = ''; }

function termWrite(text, cls){
  const o = document.getElementById('termOut'); if(!o) return;
  const span = document.createElement('span');
  if(cls) span.className = cls;
  span.textContent = text;
  o.appendChild(span);
  o.scrollTop = o.scrollHeight;
}

function termSetBusy(on){
  termBusy = on;
  const stop = document.getElementById('termStop'); if(stop) stop.disabled = !on;
  const inp = document.getElementById('termInput'); if(inp) inp.disabled = on;
  if(!on){ const i = document.getElementById('termInput'); if(i) setTimeout(()=>i.focus(), 10); }
}

function runTermCommand(){
  if(termBusy) return;
  const inp = document.getElementById('termInput');
  const cmd = inp.value;
  if(!cmd.trim()){ return; }
  // echo the command with a prompt line, then run it
  termWrite((termCwd ? termCwd + ' ' : '') + '> ', 'term-prompt-echo');
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
  termSetBusy(false);
  // refresh cwd (a `cd` may have changed it) and show a subtle exit marker on failure
  if(typeof code === 'number' && code !== 0){ termWrite('[exit ' + code + ']\n', 'term-sys'); }
  if(backendReady){
    try{ window.pywebview.api.term_cwd().then(r=>{ if(r && r.ok) termSetCwd(r.cwd); }); }catch(e){}
  }
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
