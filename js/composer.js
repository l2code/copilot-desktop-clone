function autoGrow(el){ el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,160)+'px'; }
function toggleSend(){
  const v = document.getElementById('input').value.trim();
  document.getElementById('send').disabled = (v.length===0 && pendingAttachments.length===0);
}
function onKey(e){
  if(atOpen){
    if(e.key==='ArrowDown'){ e.preventDefault(); atIdx=(atIdx+1)%atItems.length; updateAtHighlight(); return; }
    if(e.key==='ArrowUp'){ e.preventDefault(); atIdx=(atIdx-1+atItems.length)%atItems.length; updateAtHighlight(); return; }
    if(e.key==='Enter' || e.key==='Tab'){ e.preventDefault(); pickAt(atIdx); return; }
    if(e.key==='Escape'){ closeAt(); return; }
  }
  if(slashOpen){
    if(e.key==='ArrowDown'){ e.preventDefault(); slashIdx=(slashIdx+1)%slashItems.length; updateSlashHighlight(); return; }
    if(e.key==='ArrowUp'){ e.preventDefault(); slashIdx=(slashIdx-1+slashItems.length)%slashItems.length; updateSlashHighlight(); return; }
    if(e.key==='Enter' || e.key==='Tab'){ e.preventDefault(); pickSlash(slashIdx); return; }
    if(e.key==='Escape'){ closeSlash(); return; }
  }
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendMessage(); }
}

// ===== Attachments =====
let pendingAttachments = [];  // [{type:'file', path, displayName}]
function clipSvg(){ return '<svg viewBox="0 0 16 16"><path d="M9.6 2.4a3 3 0 014.2 4.2l-6 6a1.9 1.9 0 01-2.7-2.7l5.3-5.3a.75.75 0 011 1l-5.3 5.3a.4.4 0 00.6.6l6-6a1.5 1.5 0 00-2.1-2.1l-6 6a2.6 2.6 0 003.7 3.7l5.3-5.3a.75.75 0 011 1l-5.3 5.3a4.1 4.1 0 01-5.8-5.8l6-6z"/></svg>'; }
function renderAttachments(){
  document.getElementById('attachments').innerHTML = pendingAttachments.map((a,i)=>
    `<span class="chip"><button type="button" class="chip-open" onclick="previewAttachment(${i})" title="Preview file">${clipSvg()}${escapeHtml(a.displayName)}</button><button type="button" class="x" aria-label="Remove attachment" onclick="removeAttachment(${i})">&times;</button></span>`).join('');
}
function removeAttachment(i){ pendingAttachments.splice(i,1); renderAttachments(); toggleSend(); }
async function attachFile(){
  if(!backendReady){ flashBanner('File attachments are available after Copilot connects', 'warn'); return; }
  let f; try{ f = await window.pywebview.api.pick_file(); }catch(e){ return; }
  if(!f || !f.path) return;
  if(/\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name || '')){
    let img; try{ img = await window.pywebview.api.read_image(f.path); }catch(e){ img = null; }
    if(img && img.ok){
      pendingAttachments.push({type:'blob', data:'data:'+img.mimeType+';base64,'+img.data,
                               mimeType:img.mimeType, displayName:img.name || f.name});
      renderAttachments(); toggleSend(); return;
    }
  }
  pendingAttachments.push({type:'file', path:f.path, displayName:f.name});
  renderAttachments(); toggleSend();
}
function insertCodeBlock(){
  const ta = document.getElementById('input');
  const open='```\n', close='\n```';
  const s=ta.selectionStart||0, e=ta.selectionEnd||0, val=ta.value, sel=val.slice(s,e);
  ta.value = val.slice(0,s)+open+sel+close+val.slice(e);
  const pos = s+open.length+sel.length;
  ta.focus(); ta.setSelectionRange(pos,pos);
  autoGrow(ta); toggleSend();
}

// ===== Slash-command autocomplete =====
// App commands always work; any commands Copilot registers (commands.changed)
// get merged in. The SDK does not expose the CLI's built-in REPL slash commands
// for a plain session, so these app commands are the reliable baseline.
// Sourced from GitHub's Copilot CLI slash-command docs. Commands with `run` are
// handled locally in this app; the rest are inserted in the composer and sent to
// Copilot (the agent handles those it supports). Terminal-only CLI commands
// (/theme, /terminal-setup, /keep-alive, ...) are omitted as N/A in a GUI.
// The CLI's exact set varies by version; live-registered commands are merged in.
const SLASH_DEFS = [
  {cmd:'/new',      desc:'Start a new conversation',       run:()=>newChat()},
  {cmd:'/clear',    desc:'Start a new conversation',       run:()=>newChat()},
  {cmd:'/reset',    desc:'Start a new conversation',       run:()=>newChat()},
  {cmd:'/model',    desc:'Select the AI model',            run:()=>openModelMenu()},
  {cmd:'/usage',    desc:'Show usage & quota',             run:()=>openUsage()},
  {cmd:'/copy',     desc:'Copy the last response',         run:()=>copyLastResponse()},
  {cmd:'/share',    desc:'Copy conversation as Markdown',  run:()=>copyConversationMd()},
  {cmd:'/export',   desc:'Copy conversation as Markdown',  run:()=>copyConversationMd()},
  {cmd:'/search',   desc:'Search your chats',              run:()=>{ const e=document.getElementById('chatSearch'); if(e) e.focus(); }},
  {cmd:'/settings', desc:'Open settings',                  run:()=>openSettings()},
  {cmd:'/help',     desc:'List slash commands',            run:()=>showSlashHelp()},
  {cmd:'/plan',     desc:'Create an implementation plan  (sent to Copilot)'},
  {cmd:'/review',   desc:'Code-review the changes  (sent to Copilot)'},
  {cmd:'/research', desc:'Deep research via GitHub + web  (sent to Copilot)'},
  {cmd:'/delegate', desc:'Delegate changes as an AI pull request  (sent to Copilot)'},
  {cmd:'/pr',       desc:'Manage pull requests for the branch  (sent to Copilot)'},
  {cmd:'/diff',     desc:'Review changes in the directory  (sent to Copilot)'},
  {cmd:'/fleet',    desc:'Run parts of a task in parallel  (sent to Copilot)'},
  {cmd:'/compact',  desc:'Summarize history to save context  (sent to Copilot)'},
  {cmd:'/ask',      desc:'Ask a side question  (sent to Copilot)'},
  {cmd:'/init',     desc:'Initialize Copilot for this repo  (sent to Copilot)'},
];
let SLASH = SLASH_DEFS.slice();
let commandsLoaded = false;
let slashOpen=false, slashItems=[], slashIdx=0;
async function loadCommands(){
  let live = [];
  if(backendReady){
    try{
      const cmds = (await window.pywebview.api.get_commands()) || [];
      live = cmds.map(c=>({ cmd: c.name.startsWith('/') ? c.name : ('/' + c.name), desc: c.description || '' }));
    }catch(e){ /* ignore */ }
  }
  const have = new Set(SLASH_DEFS.map(c=>c.cmd));
  SLASH = SLASH_DEFS.concat(live.filter(c=>!have.has(c.cmd)));
  commandsLoaded = true;
}
function copyLastResponse(){
  for(let i=currentMessages.length-1;i>=0;i--){
    if(currentMessages[i].role==='assistant'){
      if(navigator.clipboard) navigator.clipboard.writeText(currentMessages[i].content||''); return;
    }
  }
}
function copyConversationMd(){
  const md = currentMessages.map(m=>(m.role==='user'?'**You:** ':'**Copilot:** ')+(m.content||'')).join('\n\n');
  if(navigator.clipboard) navigator.clipboard.writeText(md);
}
function showSlashHelp(){
  const inner = document.getElementById('threadInner');
  if(!started){ inner.innerHTML=''; started=true; }
  const t = assistantShell();
  t.innerHTML = '<p><strong>Slash commands</strong></p>' +
    SLASH.map(c=>`<p><code class="inline">${escapeHtml(c.cmd)}</code> — ${escapeHtml(c.desc||'Copilot command')}</p>`).join('');
  scrollDown();
}
function closeSlash(){ document.getElementById('slashMenu').classList.remove('open'); slashOpen=false; }
function updateSlash(){
  const v = document.getElementById('input').value;
  const box = document.getElementById('slashMenu');
  if(v.startsWith('/') && !v.includes(' ') && !v.includes('\n')){
    // commands.changed may arrive shortly after connect; refresh lazily once.
    if(!commandsLoaded && backendReady){
      loadCommands().then(()=>{ if(document.getElementById('input').value === v) updateSlash(); });
    }
    const q = v.toLowerCase();
    slashItems = SLASH.filter(c=>c.cmd.toLowerCase().startsWith(q));
    if(slashItems.length){
      if(slashIdx >= slashItems.length) slashIdx = 0;
      box.innerHTML = slashItems.map((c,i)=>
        `<div class="slash-opt ${i===slashIdx?'sel':''}" onmousedown="event.preventDefault();pickSlash(${i})">
           <span class="slash-cmd">${escapeHtml(c.cmd)}</span><span class="slash-desc">${escapeHtml(c.desc)}</span></div>`).join('');
      box.classList.add('open'); slashOpen = true; return;
    }
  }
  closeSlash();
}
function updateSlashHighlight(){
  document.querySelectorAll('#slashMenu .slash-opt').forEach((el,i)=>el.classList.toggle('sel', i===slashIdx));
}
function pickSlash(i){
  const c = slashItems[i]; if(!c) return;
  closeSlash();
  const input = document.getElementById('input');
  if(c.run){ input.value=''; autoGrow(input); toggleSend(); c.run(); }
  else { input.value = c.cmd + ' '; autoGrow(input); input.focus(); toggleSend(); }
}

// ===== @-file mentions =====
let atOpen=false, atItems=[], atIdx=0, atBase='';
function closeAt(){ const b=document.getElementById('atMenu'); if(b) b.classList.remove('open'); atOpen=false; }
function updateAtHighlight(){ document.querySelectorAll('#atMenu .slash-opt').forEach((el,i)=>el.classList.toggle('sel', i===atIdx)); }
function updateAt(){
  if(!backendReady){ closeAt(); return; }
  const v = document.getElementById('input').value;
  const m = v.match(/(^|\s)@([^\s@]*)$/);
  if(!m){ closeAt(); return; }
  window.pywebview.api.list_files(m[2]).then(r=>{
    const box = document.getElementById('atMenu');
    if(!r || !r.ok){ closeAt(); return; }
    atBase = r.base || ''; atItems = (r.files || []).slice(0,25);
    if(!atItems.length){ closeAt(); return; }
    if(atIdx >= atItems.length) atIdx = 0;
    box.innerHTML = atItems.map((f,i)=>`<div class="slash-opt ${i===atIdx?'sel':''}" onmousedown="event.preventDefault();pickAt(${i})"><span class="slash-cmd">@${escapeHtml(f)}</span></div>`).join('');
    box.classList.add('open'); atOpen = true;
  }).catch(()=>closeAt());
}
function pickAt(i){
  const f = atItems[i]; if(f === undefined) return;
  closeAt();
  const ta = document.getElementById('input');
  ta.value = ta.value.replace(/(^|\s)@([^\s@]*)$/, '$1');
  const sep = atBase.indexOf('\\') !== -1 ? '\\' : '/';
  const path = atBase ? (atBase.replace(/[\\/]+$/,'') + sep + f.split('/').join(sep)) : f;
  pendingAttachments.push({type:'file', path:path, displayName:f});
  renderAttachments(); autoGrow(ta); ta.focus(); toggleSend();
}
