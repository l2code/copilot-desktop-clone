// ===== Right-side detail panel =====
let panelRaw = '';
let lastDiffs = {};   // file -> unified diff (from write permission requests)
function openPanel(title, content, lang){
  document.getElementById('spTitle').textContent = title || 'Details';
  panelRaw = content;
  const body = (lang === 'python') ? highlightPython(content) : escapeHtml(content);
  document.getElementById('spBody').innerHTML = `<pre>${body}</pre>`;
  document.getElementById('sidePanel').classList.add('open');
}
function closePanel(){ document.getElementById('sidePanel').classList.remove('open'); }
function copyPanel(){ if(navigator.clipboard && panelRaw) navigator.clipboard.writeText(panelRaw); }
function openPanelFromBlock(el){
  const cb = el.closest('.codeblock');
  const lang = cb.dataset.lang || '';
  openPanel(lang || 'Code', cb.dataset.raw, lang);
}
function renderDiff(text){
  const lines = String(text||'').split('\n');
  const html = lines.map(l=>{
    if(l.startsWith('diff --git') || l.startsWith('index ') || l.startsWith('+++') || l.startsWith('---')) return '';
    let cls = 'dl';
    if(l.startsWith('+')) cls = 'dl diff-add';
    else if(l.startsWith('-')) cls = 'dl diff-del';
    else if(l.startsWith('@@')) cls = 'dl diff-hunk';
    return `<div class="${cls}">${escapeHtml(l || ' ')}</div>`;
  }).join('');
  return `<div class="diff-view">${html}</div>`;
}
function openDiffPanel(file){
  const base = file ? String(file).split(/[\\/]/).pop() : '';
  const diff = lastDiffs[file] || lastDiffs[base];
  if(!diff) return;
  document.getElementById('spTitle').textContent = 'Diff \u00b7 ' + (base || file);
  panelRaw = diff;
  document.getElementById('spBody').innerHTML = renderDiff(diff);
  document.getElementById('sidePanel').classList.add('open');
}
async function openFileInPanel(path, name){
  if(!backendReady) return;
  openPanel(name || path, 'Loading…', '');
  let res;
  try{ res = await window.pywebview.api.read_file(path); }
  catch(e){ document.getElementById('spBody').innerHTML = '<pre>Could not read file.</pre>'; return; }
  if(!res || !res.ok){
    document.getElementById('spBody').innerHTML = `<pre>${escapeHtml((res&&res.error)||'Could not read file')}</pre>`;
    return;
  }
  const ext = (name||path).split('.').pop().toLowerCase();
  const lang = (ext==='py') ? 'python' : '';
  panelRaw = res.content;
  const body = (lang==='python') ? highlightPython(res.content) : escapeHtml(res.content);
  document.getElementById('spBody').innerHTML = `<div class="sp-filemeta">${escapeHtml(path)}</div><pre>${body}</pre>`;
}
function previewAttachment(i){
  const a = pendingAttachments[i]; if(!a) return;
  if(a.type === 'blob'){
    document.getElementById('spTitle').textContent = a.displayName || 'Image';
    panelRaw = '';
    document.getElementById('spBody').innerHTML = `<div style="padding:16px"><img style="max-width:100%;border-radius:8px" src="${a.data}"></div>`;
    document.getElementById('sidePanel').classList.add('open');
    return;
  }
  openFileInPanel(a.path, a.displayName);
}

// ===== Usage / quota panel =====
function closeUsage(){ document.getElementById('usageModal').classList.remove('open'); }
function prettyQuota(k){
  return ({chat:'Chat requests', completions:'Code completions',
           premium_interactions:'Premium requests'})[k] || k.replace(/_/g,' ');
}
function renderQuota(key, q){
  const label = escapeHtml(prettyQuota(key));
  if(q.unlimited){
    return `<div class="set-row"><div class="set-label">${label}</div>
      <div class="set-val">Unlimited &middot; ${Math.round(q.used||0)} used this period</div></div>`;
  }
  const used = Math.round(q.used||0), ent = Math.round(q.entitlement||0);
  const remPct = Math.max(0, Math.min(100, Math.round(q.remaining_percentage||0)));
  const usedPct = 100 - remPct;
  const cls = usedPct>=100 ? 'full' : (usedPct>=80 ? 'warn' : '');
  const reset = q.reset_date ? ` &middot; resets ${escapeHtml(String(q.reset_date).slice(0,10))}` : '';
  const over = q.overage ? ` &middot; ${Math.round(q.overage)} overage` : '';
  return `<div class="set-row"><div class="set-label">${label}</div>
    <div class="usage-bar"><div class="usage-fill ${cls}" style="width:${usedPct}%"></div></div>
    <div class="set-val">${used} / ${ent} used &middot; ${remPct}% remaining${reset}${over}</div></div>`;
}
// Compact footer indicator: headline % used + colored bar. Refreshed on connect
// and after each reply (usage is reported per request).
async function refreshUsage(){
  const w = document.getElementById('sfUsage'); if(!w) return;
  if(!backendReady){ w.style.display = 'none'; return; }
  let res; try{ res = await window.pywebview.api.get_usage(); }catch(e){ return; }
  if(!res || !res.ok){ return; }
  const q = res.quota || {};
  const key = ['premium_interactions','chat','completions'].find(k=>q[k]) || Object.keys(q)[0];
  const item = key ? q[key] : null;
  if(!item){ w.style.display = 'none'; return; }
  const fill = document.getElementById('sfUsageFill');
  const pctEl = document.getElementById('sfUsagePct');
  document.getElementById('sfUsageLabel').textContent =
    ({premium_interactions:'Premium requests',chat:'Chat requests',completions:'Code completions'})[key] || prettyQuota(key);
  if(item.unlimited){
    pctEl.textContent = 'Unlimited';
    fill.style.width = '100%'; fill.className = 'sf-usage-fill';
    w.style.display = 'block'; return;
  }
  let used = null;
  if(typeof item.remaining_percentage === 'number') used = 100 - item.remaining_percentage;
  else if(item.entitlement) used = (item.used || 0) / item.entitlement * 100;
  if(used === null){ w.style.display = 'none'; return; }
  used = Math.max(0, Math.min(100, Math.round(used)));
  pctEl.textContent = used + '% used';
  fill.style.width = used + '%';
  fill.className = 'sf-usage-fill' + (used >= 90 ? ' full' : (used >= 70 ? ' warn' : ''));
  w.style.display = 'block';
}
async function openUsage(){
  const modal = document.getElementById('usageModal');
  const body = document.getElementById('usageBody');
  modal.classList.add('open');
  if(!backendReady){
    body.innerHTML = '<p class="set-val" style="padding:14px 0">Usage stats need a live Copilot connection.</p>';
    return;
  }
  body.innerHTML = '<p class="set-val" style="padding:14px 0">Loading usage…</p>';
  let res;
  try{ res = await window.pywebview.api.get_usage(); }
  catch(e){ body.innerHTML = '<p class="set-val" style="padding:14px 0">Could not load usage.</p>'; return; }
  if(!res || !res.ok){
    body.innerHTML = `<p class="set-val" style="padding:14px 0">Could not load usage: ${escapeHtml((res&&res.error)||'unknown')}</p>`;
    return;
  }
  const quota = res.quota || {};
  const keys = Object.keys(quota);
  if(!keys.length){
    body.innerHTML = '<p class="set-val" style="padding:14px 0">No usage data yet — usage is reported per request. ' +
      'Send a message to Copilot, then reopen this panel.<br><br>' +
      'You can always verify totals at <span class="set-val" style="color:var(--accent)">github.com/settings/billing</span> → Metered usage → Copilot.</p>';
    return;
  }
  body.innerHTML = keys.map(k=>renderQuota(k, quota[k])).join('');
}

function openSettings(){ renderSettings(); loadPermRulesUI(); loadDiscoveryUI(); document.getElementById('settingsModal').classList.add('open'); }
let _discoveryOn = true;
async function loadDiscoveryUI(){
  if(backendReady){ try{ const r = await window.pywebview.api.get_config_discovery(); _discoveryOn = !(r && r.on === false); }catch(e){} }
  renderDiscovery();
}
function renderDiscovery(){
  const on = document.getElementById('discOn'), off = document.getElementById('discOff');
  if(on) on.classList.toggle('on', _discoveryOn);
  if(off) off.classList.toggle('on', !_discoveryOn);
}
async function setDiscovery(on){
  _discoveryOn = !!on; renderDiscovery();
  if(backendReady){ try{ await window.pywebview.api.set_config_discovery(_discoveryOn); }catch(e){} }
  flashBanner(_discoveryOn ? 'Discovering ~/.copilot config' : 'Discovery off — fewer MCP tools');
  newChat();   // session was recreated
}
const PERM_KINDS = [['write','File edits'],['shell','Shell commands'],['url','Network / URLs'],['mcp','MCP tools']];
let permRules = {};
async function loadPermRulesUI(){
  if(backendReady){ try{ const r = await window.pywebview.api.get_perm_rules(); permRules = (r && r.rules) || {}; }catch(e){} }
  renderPermRules();
}
function renderPermRules(){
  const host = document.getElementById('permRules'); if(!host) return;
  host.innerHTML = PERM_KINDS.map(([k,label])=>{
    const v = permRules[k] || 'ask';
    const segs = ['allow','ask','deny'].map(p=>`<button class="seg ${v===p?'on':''}" onclick="setPerm('${k}','${p}')">${p[0].toUpperCase()+p.slice(1)}</button>`).join('');
    return `<div class="perm-rule"><span class="perm-rule-label">${escapeHtml(label)}</span><span class="seg-group">${segs}</span></div>`;
  }).join('');
}
function setPerm(k,p){ permRules[k]=p; if(backendReady){ try{ window.pywebview.api.set_perm_rules(permRules); }catch(e){} } renderPermRules(); }
function closeSettings(){ document.getElementById('settingsModal').classList.remove('open'); }
// Custom instructions and MCP servers are now two separate, focused dialogs.
let _instrOrig = '';   // instructions text as loaded, to detect unsaved changes
async function openInstructions(){
  // Slide over Settings (leave it open underneath); closing reveals Settings.
  document.getElementById('instrModal').classList.add('open');
  if(backendReady){
    try{ const r = await window.pywebview.api.get_instructions(); _instrOrig = (r && r.text) || ''; }catch(e){ _instrOrig = ''; }
  } else { _instrOrig = document.getElementById('instrText').value || ''; }
  document.getElementById('instrText').value = _instrOrig;
  instrDirtyCheck();
}
// Save is enabled only when the text differs from what was loaded.
function instrDirtyCheck(){
  const btn = document.getElementById('instrSave'); if(!btn) return;
  btn.disabled = (document.getElementById('instrText').value === _instrOrig);
}
function closeInstructions(){ document.getElementById('instrModal').classList.remove('open'); }
function cancelInstructions(){ closeInstructions(); openSettings(); }  // discard edits, return to Settings
async function openMcp(){
  // Slide over Settings (leave it open underneath); closing reveals Settings.
  document.getElementById('mcpModal').classList.add('open');
  document.getElementById('mcpErr').textContent = '';
  document.getElementById('mcpForm').style.display = 'none';
  document.getElementById('mcpJsonWrap').style.display = 'none';
  if(backendReady){
    try{ const m = await window.pywebview.api.get_mcp(); mcpServers = (m && m.servers) || {}; }catch(e){ mcpServers = {}; }
    try{ const st = await window.pywebview.api.get_mcp_status(); mcpStatus = (st && st.status) || {}; mcpDisabled = (st && st.disabled) || []; }catch(e){}
    renderMcpList();   // show app servers immediately
    try{ const d = await window.pywebview.api.discover_mcp(); mcpDiscovered = (d && d.servers) || []; }catch(e){ mcpDiscovered = []; }
    await fetchTools();   // pre-use tool list + count (no message sent)
  }
  renderMcpList();
}
function closeMcp(){ document.getElementById('mcpModal').classList.remove('open'); }
function flashBanner(msg, kind){
  showBanner(kind || 'ok', msg);
  setTimeout(()=>{ const b=document.getElementById('bannerHost'); if(b) b.innerHTML=''; }, 3000);
}
let _confirmCb = null;
function askConfirm(msg, onYes, okLabel){
  document.getElementById('confirmMsg').textContent = msg;
  document.getElementById('confirmOk').textContent = okLabel || 'Delete';
  _confirmCb = onYes || null;
  document.getElementById('confirmModal').classList.add('open');
}
function confirmCancel(){ document.getElementById('confirmModal').classList.remove('open'); _confirmCb = null; }
function confirmAccept(){ const cb = _confirmCb; confirmCancel(); if(cb) cb(); }
async function saveInstructions(){
  if(!backendReady){ flashBanner('Custom instructions are available after Copilot connects', 'warn'); return; }
  const t = document.getElementById('instrText').value;
  try{ await window.pywebview.api.set_instructions(t); }catch(e){}
  _instrOrig = t;
  closeInstructions(); newChat(); flashBanner('Custom instructions saved'); openSettings();
}
async function saveMcp(){
  const raw = document.getElementById('mcpText').value.trim();
  let obj = {};
  if(raw){
    try{ obj = JSON.parse(raw); }
    catch(e){ document.getElementById('mcpErr').textContent = 'Invalid JSON: ' + e.message; return; }
    if(typeof obj !== 'object' || Array.isArray(obj)){
      document.getElementById('mcpErr').textContent = 'Expected an object mapping server name to config.'; return;
    }
  }
  document.getElementById('mcpErr').textContent = '';
  mcpServers = obj;
  if(backendReady){ try{ await window.pywebview.api.set_mcp(obj); }catch(e){} }
  document.getElementById('mcpJsonWrap').style.display = 'none';
  renderMcpList(); flashBanner('MCP servers saved');
}
// ===== MCP server manager =====
let mcpServers = {}, mcpStatus = {}, mcpDisabled = [], mcpDiscovered = [], mcpTools = [];
const TOOL_CAP = 128;   // Copilot API max tools per request
// Pre-use tool list (built-in + namespaced MCP) via tools.list — no message sent.
async function fetchTools(){
  if(!backendReady){ mcpTools = []; return; }
  try{ const r = await window.pywebview.api.list_tools(); mcpTools = (r && r.tools) || []; }
  catch(e){ mcpTools = []; }
}
// Group tools by MCP server namespace ('' = built-in).
function toolsByServer(){
  const m = {};
  (mcpTools||[]).forEach(t=>{ const k = t.server || '(built-in)'; (m[k] = m[k] || []).push(t.name); });
  return m;
}
// Connect-time + post-toggle guard: warn before a send hits the 128-tool cap.
async function checkToolBudget(){
  await fetchTools();
  const n = (mcpTools||[]).length;
  if(n > TOOL_CAP){
    showBanner('err', n + ' tools loaded — over Copilot’s ' + TOOL_CAP + '-tool limit. Disable MCP servers in Settings → MCP, or requests may fail.');
  } else if(n >= TOOL_CAP - 18){
    flashBanner(n + ' tools loaded (limit ' + TOOL_CAP + ').', 'warn');
  }
}
function parseKV(text, sep){
  const o = {};
  String(text||'').split('\n').forEach(l=>{ const i=l.indexOf(sep); if(i>0){ const k=l.slice(0,i).trim(); if(k) o[k]=l.slice(i+1).trim(); } });
  return o;
}
function renderMcpList(){
  const host = document.getElementById('mcpList'); if(!host) return;
  const names = Object.keys(mcpServers);
  // Discovered servers (.github / ~/.copilot / plugins / builtin) that aren't app-defined,
  // from mcp.discover(); merge in any names only seen via runtime status events.
  const discNames = (mcpDiscovered || []).map(d=>d.name);
  for(const n of Object.keys(mcpStatus)){ if(!(n in mcpServers) && discNames.indexOf(n)===-1){ discNames.push(n); mcpDiscovered.push({name:n, enabled:true, source:'', type:''}); } }
  const discovered = (mcpDiscovered || []).filter(d => !(d.name in mcpServers));
  const connected = Object.keys(mcpStatus).filter(n=>(mcpStatus[n]||{}).status==='connected').length;
  const total = names.length + discovered.length;
  const hb = document.getElementById('mcpHealth'); if(hb) hb.textContent = total ? `(${connected}/${total} connected)` : '';
  // Tool budget (pre-counted via tools.list): show total vs the 128 cap, colored.
  const byServer = toolsByServer();
  const toolTotal = (mcpTools||[]).length;
  const toolHint = (n) => { const ts = byServer[n]; return ts ? ' · ' + ts.length + ' tool' + (ts.length===1?'':'s') : ''; };
  const toolNames = (n) => { const ts = byServer[n]; return ts ? ts.join(', ') : ''; };
  let budget = '';
  if(toolTotal){
    const cls = toolTotal > TOOL_CAP ? 'full' : (toolTotal >= TOOL_CAP - 18 ? 'warn' : '');
    const note = toolTotal > TOOL_CAP ? ' — over the limit; disable some servers' : (toolTotal >= TOOL_CAP - 18 ? ' — approaching the limit' : '');
    budget = `<div class="mcp-budget ${cls}">Tools loaded: ${toolTotal} / ${TOOL_CAP}${escapeHtml(note)}</div>`;
  }
  if(!total){ host.innerHTML = budget + '<div class="mcp-empty">No MCP servers configured or discovered.</div>'; return; }
  const ownRows = names.map(n=>{
    const cfg = mcpServers[n] || {};
    const t = cfg.url ? 'http' : 'stdio';
    const disabled = mcpDisabled.indexOf(n) !== -1;
    const st = disabled ? 'disabled' : ((mcpStatus[n]||{}).status || 'pending');
    const err = (mcpStatus[n]||{}).error || '';
    const meta = (t==='http' ? (cfg.url||'') : ((cfg.command||'') + ' ' + ((cfg.args||[]).join(' ')))) + toolHint(n);
    return `<div class="mcp-row">
      <span class="mcp-name">${escapeHtml(n)}</span>
      <span class="mcp-badge ${escapeHtml(st)}" title="${escapeAttr(err)}">${escapeHtml(st)}</span>
      <span class="mcp-meta" title="${escapeAttr(toolNames(n) || meta)}">${escapeHtml(meta)}</span>
      <span class="mcp-spacer"></span>
      <span class="mcp-actions">
        <button class="seg" onclick="mcpToggle('${escapeJsArg(n)}', ${disabled})">${disabled?'Enable':'Disable'}</button>
        <button class="seg" onclick="mcpEdit('${escapeJsArg(n)}')">Edit</button>
        <button class="seg" onclick="mcpRemove('${escapeJsArg(n)}')">Remove</button>
      </span></div>`;
  }).join('');
  // Read-only rows for discovered servers (managed in their config, not editable here).
  const srcLabel = {user:'~/.copilot', workspace:'.github', plugin:'plugin', builtin:'builtin'};
  const discRows = discovered.map(d=>{
    const n = d.name;
    const live = mcpStatus[n] || {};
    const st = !d.enabled ? 'disabled' : (live.status || 'pending');
    const err = live.error || '';
    const meta = (srcLabel[d.source] || d.source || 'discovered') + (d.type ? ' · ' + d.type : '') + toolHint(n);
    return `<div class="mcp-row">
      <span class="mcp-name">${escapeHtml(n)}</span>
      <span class="mcp-badge ${escapeHtml(st)}" title="${escapeAttr(err)}">${escapeHtml(st)}</span>
      <span class="mcp-meta" title="${escapeAttr(toolNames(n) || meta)}">${escapeHtml(meta)}</span>
      <span class="mcp-spacer"></span>
      <span class="mcp-actions">
        <button class="seg" onclick="mcpDiscToggle('${escapeJsArg(n)}', ${d.enabled})">${d.enabled?'Disable':'Enable'}</button>
      </span></div>`;
  }).join('');
  host.innerHTML = budget + ownRows + (discRows ? '<div class="mcp-sub">Discovered (from .github / ~/.copilot)</div>' + discRows : '');
}
function mcpAddNew(){ mcpRenderForm(''); }
function mcpEdit(name){ mcpRenderForm(name); }
function mcpCancelForm(){ document.getElementById('mcpForm').style.display='none'; }
function mcpRenderForm(name){
  document.getElementById('mcpErr').textContent = '';
  const cfg = name ? (mcpServers[name]||{}) : {};
  const isHttp = !!cfg.url;
  const f = document.getElementById('mcpForm');
  f.style.display = 'block';
  f.innerHTML = `
    <label>Server name</label>
    <input id="mcpfName" value="${escapeAttr(name||'')}" ${name?'readonly':''} placeholder="e.g. fetch">
    <label>Transport</label>
    <span class="seg-group">
      <button type="button" class="seg ${!isHttp?'on':''}" onclick="mcpSetTransport('stdio')">stdio (local)</button>
      <button type="button" class="seg ${isHttp?'on':''}" onclick="mcpSetTransport('http')">http / sse</button>
    </span>
    <div id="mcpStdio" style="display:${isHttp?'none':'block'}">
      <label>Command</label><input id="mcpfCmd" value="${escapeAttr(cfg.command||'')}" placeholder="npx">
      <label>Arguments (space-separated)</label><input id="mcpfArgs" value="${escapeAttr((cfg.args||[]).join(' '))}" placeholder="-y @modelcontextprotocol/server-fetch">
      <label>Env (KEY=VALUE per line)</label><textarea id="mcpfEnv" rows="2">${escapeHtml(Object.entries(cfg.env||{}).map(kv=>kv[0]+'='+kv[1]).join('\n'))}</textarea>
    </div>
    <div id="mcpHttp" style="display:${isHttp?'block':'none'}">
      <label>URL</label><input id="mcpfUrl" value="${escapeAttr(cfg.url||'')}" placeholder="https://example.com/mcp">
      <label>Headers (KEY: VALUE per line)</label><textarea id="mcpfHeaders" rows="2">${escapeHtml(Object.entries(cfg.headers||{}).map(kv=>kv[0]+': '+kv[1]).join('\n'))}</textarea>
    </div>
    <label>Tools — limit which this server exposes (comma-separated; blank = all)</label>
    <input id="mcpfTools" value="${escapeAttr((cfg.tools && cfg.tools[0] !== '*') ? (cfg.tools||[]).join(', ') : '')}" placeholder="e.g. search_code, get_file_contents  (helps stay under the 128-tool limit)">
    <div class="cz-actions" style="gap:6px;margin-top:8px">
      <button type="button" class="seg" onclick="mcpCancelForm()">Cancel</button>
      <button type="button" class="seg on" onclick="mcpSaveForm()">Save server</button>
    </div>`;
}
function mcpSetTransport(t){
  document.getElementById('mcpStdio').style.display = t==='stdio' ? 'block' : 'none';
  document.getElementById('mcpHttp').style.display = t==='http' ? 'block' : 'none';
  const segs = document.querySelectorAll('#mcpForm .seg-group .seg');
  if(segs[0]) segs[0].classList.toggle('on', t==='stdio');
  if(segs[1]) segs[1].classList.toggle('on', t==='http');
}
async function mcpSaveForm(){
  const name = document.getElementById('mcpfName').value.trim();
  if(!name){ document.getElementById('mcpErr').textContent = 'Server name is required.'; return; }
  const httpVisible = document.getElementById('mcpHttp').style.display !== 'none';
  // Per-server tool allowlist: blank = all ("*"), otherwise only the named tools
  // load (fewer tools = easier to stay under Copilot's 128-tool-per-request cap).
  const toolsRaw = (document.getElementById('mcpfTools').value || '').trim();
  const tools = toolsRaw ? toolsRaw.split(',').map(s=>s.trim()).filter(Boolean) : ['*'];
  let cfg;
  if(httpVisible){
    const url = document.getElementById('mcpfUrl').value.trim();
    if(!url){ document.getElementById('mcpErr').textContent = 'URL is required for HTTP servers.'; return; }
    cfg = {type:'http', url, tools};
    const h = parseKV(document.getElementById('mcpfHeaders').value, ':'); if(Object.keys(h).length) cfg.headers = h;
  } else {
    const command = document.getElementById('mcpfCmd').value.trim();
    if(!command){ document.getElementById('mcpErr').textContent = 'Command is required for local servers.'; return; }
    cfg = {type:'local', command,
           args: document.getElementById('mcpfArgs').value.trim().split(/\s+/).filter(Boolean), tools};
    const e = parseKV(document.getElementById('mcpfEnv').value, '='); if(Object.keys(e).length) cfg.env = e;
  }
  mcpServers[name] = cfg;
  document.getElementById('mcpForm').style.display = 'none';
  if(backendReady){ try{ await window.pywebview.api.set_mcp(mcpServers); }catch(e){} }
  renderMcpList(); flashBanner('MCP server saved');
}
// Enable/disable a discovered (~/.copilot/.github) server — persisted, recreates session.
async function mcpDiscToggle(name, currentlyEnabled){
  if(!backendReady) return;
  const want = !currentlyEnabled;
  try{ await window.pywebview.api.set_discovered_mcp_enabled(name, want); }catch(e){}
  const d = (mcpDiscovered||[]).find(x=>x.name===name); if(d) d.enabled = want;
  await fetchTools();   // tool count changed (session recreated)
  renderMcpList();
  flashBanner((want?'Enabled ':'Disabled ') + name);
  newChat();   // session was recreated to apply the change
}
async function mcpToggle(name, enable){
  if(backendReady){ try{ await window.pywebview.api.set_mcp_enabled(name, enable); }catch(e){} }
  if(enable) mcpDisabled = mcpDisabled.filter(x=>x!==name); else if(mcpDisabled.indexOf(name)===-1) mcpDisabled.push(name);
  await fetchTools();
  renderMcpList();
}
async function mcpRemove(name){
  delete mcpServers[name];
  if(backendReady){ try{ await window.pywebview.api.set_mcp(mcpServers); }catch(e){} }
  await fetchTools();
  renderMcpList();
}
function toggleMcpJson(){
  const w = document.getElementById('mcpJsonWrap');
  const show = w.style.display === 'none';
  w.style.display = show ? 'block' : 'none';
  if(show){ document.getElementById('mcpText').value = Object.keys(mcpServers).length ? JSON.stringify(mcpServers, null, 2) : ''; }
}
function renderSettings(){
  const name = (document.getElementById('userName') || {}).textContent || '';
  document.getElementById('settingsAccount').textContent =
    backendReady ? ('Connected' + (name && name !== 'GitHub account' ? ' as ' + name : ' to GitHub Copilot')) : 'Demo mode (no backend)';
}
function clearHistory(){
  askConfirm('Delete all conversations? This can\u2019t be undone.', doClearHistory, 'Delete all');
}
async function doClearHistory(){
  if(window.pywebview && window.pywebview.api){
    try{ await window.pywebview.api.clear_history(); }catch(e){}
    await loadConversations();
  }
  closeSettings();
  newChat();
}
function setAutoApprove(v){
  autoApprove = v;
  if(backendReady){ try{ window.pywebview.api.set_auto_approve(v); }catch(e){} }
  renderSettings();
}
