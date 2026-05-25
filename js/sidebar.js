// ===== Conversation state / history =====
let conversations = [];     // [{id,title,updated,cwd}] for the sidebar
let projects = [];          // durable project records from SQLite
let currentConvId = null;
let currentMessages = [];   // [{role:'user'|'assistant', content}]
let autoApprove = false;
let currentCwd = null;      // working folder of the active chat (matches the composer)

function newConvId(){ return 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2,6); }

async function loadConversations(){
  if(!window.pywebview || !window.pywebview.api){ renderSidebar(); return; }
  try{ conversations = (await window.pywebview.api.list_conversations()) || []; }
  catch(e){ conversations = []; }
  try{
    const r = await window.pywebview.api.list_projects();
    projects = (r && r.projects) || [];
  }catch(e){ projects = []; }
  renderSidebar();
}

function relTime(ts){
  if(!ts) return '';
  const s = Date.now()/1000 - ts;
  if(s < 60) return 'now';
  if(s < 3600) return Math.floor(s/60) + 'm';
  if(s < 86400) return Math.floor(s/3600) + 'h';
  if(s < 604800) return Math.floor(s/86400) + 'd';
  return Math.floor(s/604800) + 'w';
}
function projName(cwd){
  if(!cwd) return 'Other';
  return String(cwd).replace(/[\\/]+$/,'').split(/[\\/]/).pop() || cwd;
}
function renderSidebar(){
  const list = document.getElementById('chatList');
  if(!list) return;
  const searchEl = document.getElementById('chatSearch');
  const q = (searchEl ? searchEl.value : '').toLowerCase();
  const items = q ? conversations.filter(c=>(c.title||'').toLowerCase().includes(q)) : conversations.slice();
  // No placeholder draft: a chat only appears once it has a real exchange (saved
  // after the assistant replies), so navigating away never drops a phantom group.
  if(!items.length && !projects.length){ list.innerHTML = '<div class="sb-empty">'+(q?'No matches':'No projects yet')+'</div>'; return; }
  // group by durable project where available, falling back to working folder.
  const groups = [], idx = {};
  projects.forEach(p=>{
    const k = p.id || p.main_repo_path || '';
    idx[k] = groups.length;
    groups.push({key:k, cwd:p.main_repo_path, name:p.name, items:[]});
  });
  items.forEach(c=>{
    const k = c.project_id || c.cwd || '';
    if(idx[k] === undefined){
      idx[k] = groups.length;
      groups.push({key:k, cwd:c.cwd || '', name:c.project_name || projName(c.cwd), items:[]});
    }
    groups[idx[k]].items.push(c);
  });
  const folderIcon = '<svg class="proj-ico" viewBox="0 0 16 16"><path d="M1.75 2.5A1.75 1.75 0 000 4.25v7.5C0 12.99.78 14 1.75 14h12.5A1.75 1.75 0 0016 12.25V5.75A1.75 1.75 0 0014.25 4H7.5L6 2.5z"/></svg>';
  list.innerHTML = groups.map(g=>{
    const head = `<div class="proj-head" title="${escapeAttr(g.cwd||'')}">${folderIcon}<span>${escapeHtml(g.name || projName(g.cwd))}</span></div>`;
    const rows = g.items.map(c=>{
      const del = c._draft ? '' :
        `<span class="ci-del" title="Delete conversation" onclick="event.stopPropagation();deleteConversation('${c.id}')">&times;</span>`;
      return `<div class="chat-item ${c.id===currentConvId?'active':''}" title="${escapeAttr(c.title)}" onclick="openConversation('${c.id}')">
         <span class="ci-title">${escapeHtml(c.title)}</span>
         <span class="ci-time">${relTime(c.updated)}</span>
         ${del}</div>`;
    }).join('') || '<div class="sb-empty sb-project-empty">No sessions yet</div>';
    return head + rows;
  }).join('');
}
function deleteConversation(id){
  const conv = conversations.find(c=>c.id===id);
  const title = (conv && conv.title) ? conv.title : 'this conversation';
  askConfirm('Delete \u201c' + title + '\u201d? This can\u2019t be undone.', ()=>doDeleteConversation(id), 'Delete');
}
async function doDeleteConversation(id){
  if(!window.pywebview || !window.pywebview.api) return;
  try{ await window.pywebview.api.delete_conversation(id); }catch(e){}
  if(id === currentConvId) newChat();
  await loadConversations();
}
function renderThread(){
  const inner = document.getElementById('threadInner');
  if(!currentMessages.length){ inner.innerHTML = document.getElementById('emptyTemplate').innerHTML; started = false; return; }
  inner.innerHTML = ''; started = true;
  currentMessages.forEach(m=>{
    if(m.role === 'user') addUserMessage(m.content, true, m.attachments);
    else { const t = assistantShell(); t.innerHTML = renderMarkdown(m.content); addMessageActions(t, m.content); }
  });
  scrollDown();
}
async function undoLast(){
  if(!backendReady){ flashBanner('Rewind is available after Copilot connects', 'warn'); return; }
  let res; try{ res = await window.pywebview.api.undo(); }catch(e){ return; }
  if(res && res.ok){
    for(let i=currentMessages.length-1;i>=0;i--){ if(currentMessages[i].role==='assistant'){ currentMessages.splice(i,1); break; } }
    for(let i=currentMessages.length-1;i>=0;i--){ if(currentMessages[i].role==='user'){ currentMessages.splice(i,1); break; } }
    renderThread();
    persistCurrent();
    flashBanner('Rewound the conversation (file edits on disk are not reverted)');
  } else {
    flashBanner((res && res.error) || 'Nothing to undo');
  }
}

async function persistCurrent(){
  if(!backendReady || !currentMessages.length) return;
  const firstUser = currentMessages.find(m=>m.role==='user');
  const title = (firstUser ? firstUser.content : 'New chat').slice(0,60);
  try{
    await window.pywebview.api.save_conversation(currentConvId, title, currentMessages);
    await loadConversations();
  }catch(e){ /* non-fatal */ }
}

async function openConversation(id){
  if(!window.pywebview || !window.pywebview.api) return;
  let conv;
  try{ conv = await window.pywebview.api.get_conversation(id); }catch(e){ return; }
  if(!conv) return;
  currentConvId = id;
  currentMessages = conv.messages || [];
  // Switch the working folder to this chat's project so the composer and the
  // sidebar's active project stay consistent with the conversation you opened.
  if(conv.cwd && conv.cwd !== currentCwd && backendReady){
    // remember=false: viewing a past chat switches the live folder but must NOT
    // overwrite the default folder that gets restored on next launch.
    try{ const r = await window.pywebview.api.set_working_dir(conv.cwd, false); if(r && r.ok) setWdDisplay(conv.cwd); }catch(e){}
  }
  started = true;
  const inner = document.getElementById('threadInner');
  inner.innerHTML = '';
  currentMessages.forEach(m=>{
    if(m.role === 'user') addUserMessage(m.content, true, m.attachments);
    else { const t = assistantShell(); t.innerHTML = renderMarkdown(m.content); addMessageActions(t, m.content); }
  });
  renderSidebar();
  scrollDown();
}

// ===== Top-bar buttons =====
function toggleSidebar(){ document.querySelector('.sidebar').classList.toggle('collapsed'); }
function initResponsiveSidebar(){
  const sb = document.querySelector('.sidebar');
  if(sb && window.innerWidth <= 640) sb.classList.add('collapsed');
}
initResponsiveSidebar();
function onContextUsage(cur, lim){
  if(!lim){ return; }
  const item = document.getElementById('ctxItem'); if(!item) return;
  const pct = Math.max(0, Math.min(100, Math.round((cur||0)/lim*100)));
  item.style.display = 'flex';
  document.getElementById('ctxLabel').textContent = 'Context ' + pct + '%';
  const fill = document.getElementById('ctxFill');
  fill.style.width = pct + '%';
  fill.className = 'ctx-fill' + (pct>=90 ? ' full' : (pct>=70 ? ' warn' : ''));
}
async function doCompact(){
  if(!backendReady) return;
  flashBanner('Compacting conversation\u2026');
  try{ await window.pywebview.api.compact(); }catch(e){}
  flashBanner('Conversation compacted');
}

// ===== Working folder (what Copilot can read / run commands in) =====
function setWdDisplay(path){
  currentCwd = path || null;        // keep the sidebar's active project in sync
  const w = document.getElementById('wdName'); if(!w) return;
  const base = String(path).replace(/[\\/]+$/,'').split(/[\\/]/).pop() || path;
  w.textContent = base;
  const b = document.getElementById('wdBtn'); if(b) b.title = 'Working folder: ' + path;
  renderSidebar();
}
const MODES = [
  {id:'interactive', name:'Interactive', desc:'Works step by step'},
  {id:'plan',        name:'Plan',        desc:'Plans the approach before coding'},
  {id:'autopilot',   name:'Autopilot',   desc:'Runs autonomously \u2014 you still approve actions'},
];
let agentMode = 'interactive';
function renderModeMenu(){
  document.getElementById('modeMenu').innerHTML = MODES.map(m=>
    `<div class="foot-opt ${m.id===agentMode?'sel':''}" onclick="selectMode('${m.id}')"><b>${m.name}</b><span>${m.desc}</span></div>`).join('');
}
function toggleModeMenu(e){ e.stopPropagation(); renderModeMenu(); document.getElementById('modeDD').classList.toggle('open'); }
async function selectMode(id){
  document.getElementById('modeDD').classList.remove('open');
  if(id===agentMode) return;
  agentMode = id;
  const m = MODES.find(x=>x.id===id);
  document.getElementById('modeName').textContent = m ? m.name : id;
  if(backendReady){
    try{ await window.pywebview.api.set_mode(id); }catch(e){}
    flashBanner('Mode: ' + (m ? m.name : id) + (id==='plan' ? ' (read-only)' : ''));
  }
}
async function pickFolder(){
  if(!backendReady){ flashBanner('Choose a folder after Copilot connects', 'warn'); return; }
  let f; try{ f = await window.pywebview.api.pick_folder(); }catch(e){ return; }
  if(!f || !f.path) return;
  let res; try{ res = await window.pywebview.api.set_working_dir(f.path); }catch(e){ return; }
  if(res && res.ok){
    setWdDisplay(f.path);
    await loadConversations();
    newChat();   // session is recreated for the new folder
    showBanner('ok','Copilot is now working in ' + f.path);
    setTimeout(()=>{ const bn=document.getElementById('bannerHost'); if(bn) bn.innerHTML=''; }, 4000);
  }
}

// ===== New project menu (next to the Projects header) =====
function toggleProjMenu(e){ if(e) e.stopPropagation(); const m=document.getElementById('projMenu'); if(m) m.classList.toggle('open'); }
function closeProjMenu(){ const m=document.getElementById('projMenu'); if(m) m.classList.remove('open'); }
async function newProjectScratch(){
  closeProjMenu();
  if(!backendReady){ flashBanner('Create a project after Copilot connects', 'warn'); return; }
  let r; try{ r = await window.pywebview.api.new_project(); }catch(e){ return; }
  if(r && r.ok){
    setWdDisplay(r.path);
    await loadConversations();
    newChat();
    showBanner('ok','New project created at ' + r.path);
    setTimeout(()=>{ const bn=document.getElementById('bannerHost'); if(bn) bn.innerHTML=''; }, 5000);
  } else {
    flashBanner((r && r.error) || 'Could not create project', 'warn');
  }
}
