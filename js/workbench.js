// ===== Copilot App-style workspace workbench =====
let wbTab = 'changes';
let wbBusy = false;

const WB_TABS = [
  ['changes', 'Changes'],
  ['files', 'Files'],
  ['activity', 'Activity'],
  ['github', 'GitHub'],
  ['workflows', 'Workflows'],
  ['terminal', 'Terminal'],
];

function openWorkbench(tab){
  wbTab = tab || wbTab || 'changes';
  panelRaw = '';
  document.getElementById('spTitle').textContent = 'Workspace';
  document.getElementById('sidePanel').classList.add('open');
  renderWorkbenchShell();
  refreshWorkbench();
}

function renderWorkbenchShell(){
  const tabs = WB_TABS.map(([id,label]) =>
    `<button class="wb-tab ${id===wbTab?'active':''}" onclick="openWorkbench('${id}')">${label}</button>`
  ).join('');
  document.getElementById('spBody').innerHTML = `
    <div class="wb">
      <div class="wb-tabs">${tabs}</div>
      <div class="wb-content" id="wbContent"><div class="wb-empty">Loading…</div></div>
    </div>`;
}

function wbSet(html){
  const el = document.getElementById('wbContent');
  if(el) el.innerHTML = html;
}

async function wbCall(name, ...args){
  if(!window.pywebview || !window.pywebview.api) return {ok:false,error:'Backend is not available'};
  try{ return await window.pywebview.api[name](...args); }
  catch(e){ return {ok:false,error:String(e)}; }
}

async function refreshWorkbench(){
  if(wbBusy) return;
  wbBusy = true;
  try{
    if(wbTab === 'changes') await renderChangesPanel();
    else if(wbTab === 'files') await renderFilesPanel();
    else if(wbTab === 'activity') await renderActivityPanel();
    else if(wbTab === 'github') await renderGithubPanel();
    else if(wbTab === 'workflows') await renderWorkflowsPanel();
    else if(wbTab === 'terminal') renderTerminalPanelTab();
  } finally {
    wbBusy = false;
  }
}

function wbErr(res){
  return `<div class="wb-empty wb-error">${escapeHtml((res && (res.error || res.stderr)) || 'Something went wrong')}</div>`;
}

function fileState(f){
  if(f.kind === 'untracked') return 'Untracked';
  if(f.kind === 'conflicted') return 'Conflict';
  const parts = [];
  if(f.index && f.index !== '.') parts.push('staged ' + f.index);
  if(f.worktree && f.worktree !== '.') parts.push('changed ' + f.worktree);
  return parts.join(' · ') || 'Modified';
}

async function renderChangesPanel(){
  wbSet('<div class="wb-empty">Loading changes…</div>');
  const res = await wbCall('get_git_status');
  if(!res || !res.ok){ wbSet(wbErr(res)); return; }
  const files = res.files || [];
  const rows = files.length ? files.map(f=>{
    const p = escapeJsArg(f.path);
    const isStaged = f.index && f.index !== '.' && f.index !== '?';
    const stageBtn = isStaged
      ? `<button class="wb-mini" onclick="wbUnstage('${p}')">Unstage</button>`
      : `<button class="wb-mini" onclick="wbStage('${p}')">Stage</button>`;
    return `<div class="wb-file">
      <button class="wb-file-main" onclick="wbShowDiff('${p}', ${isStaged?'true':'false'})">
        <span class="wb-file-name">${escapeHtml(f.path)}</span>
        <span class="wb-badge">${escapeHtml(fileState(f))}</span>
      </button>
      <div class="wb-file-actions">
        ${stageBtn}
        <button class="wb-mini danger" onclick="wbDiscard('${p}')">Discard</button>
      </div>
    </div>`;
  }).join('') : '<div class="wb-empty">No local changes.</div>';
  wbSet(`
    <div class="wb-bar">
      <div><b>${escapeHtml(res.branch || 'No branch')}</b><span>${res.defaultBranch ? ' base ' + escapeHtml(res.defaultBranch) : ''}</span></div>
      <div class="wb-actions">
        <button class="wb-mini" onclick="refreshWorkbench()">Refresh</button>
        <button class="wb-mini" onclick="wbFetch()">Fetch</button>
        <button class="wb-mini" onclick="wbPull()">Pull</button>
        <button class="wb-mini" onclick="wbPush()">Push</button>
      </div>
    </div>
    <div class="wb-section-title">Changed files</div>
    <div class="wb-list">${rows}</div>
    <div class="wb-commit">
      <input id="wbCommitSummary" class="wb-input" placeholder="Commit summary">
      <textarea id="wbCommitBody" class="wb-textarea" placeholder="Description"></textarea>
      <div class="wb-actions">
        <button class="wb-mini" onclick="wbStageAll()">Stage all</button>
        <button class="wb-mini" onclick="wbUnstageAll()">Unstage all</button>
        <button class="wb-primary" onclick="wbCommit()">Commit</button>
      </div>
    </div>
    <div class="wb-section-title">Branch</div>
    <div class="wb-row">
      <input id="wbBranchName" class="wb-input" placeholder="new-branch-name">
      <button class="wb-mini" onclick="wbCreateBranch()">Create branch</button>
    </div>`);
}

async function wbStage(path){ await wbCall('stage_file', null, path); await renderChangesPanel(); }
async function wbUnstage(path){ await wbCall('unstage_file', null, path); await renderChangesPanel(); }
async function wbStageAll(){ await wbCall('stage_all'); await renderChangesPanel(); }
async function wbUnstageAll(){ await wbCall('unstage_all'); await renderChangesPanel(); }
function wbDiscard(path){ askConfirm('Discard changes in ' + path + '? This cannot be undone.', async()=>{ await wbCall('discard_file', null, path); await renderChangesPanel(); }, 'Discard'); }
async function wbFetch(){ const r=await wbCall('fetch'); flashBanner(r.ok?'Fetched':(r.error||r.stderr||'Fetch failed'), r.ok?'ok':'warn'); await renderChangesPanel(); }
async function wbPull(){ const r=await wbCall('pull'); flashBanner(r.ok?'Pulled':(r.error||r.stderr||'Pull failed'), r.ok?'ok':'warn'); await renderChangesPanel(); }
async function wbPush(){ const r=await wbCall('push'); flashBanner(r.ok?'Pushed':(r.error||r.stderr||'Push failed'), r.ok?'ok':'warn'); await renderChangesPanel(); }
async function wbCommit(){
  const summary = (document.getElementById('wbCommitSummary') || {}).value || '';
  const body = (document.getElementById('wbCommitBody') || {}).value || '';
  const r = await wbCall('commit', null, summary, body);
  flashBanner(r.ok ? 'Commit created' : (r.error || r.stderr || 'Commit failed'), r.ok ? 'ok' : 'warn');
  await renderChangesPanel();
}
async function wbCreateBranch(){
  const name = ((document.getElementById('wbBranchName') || {}).value || '').trim();
  if(!name) return;
  const r = await wbCall('create_branch', null, name, null, true);
  flashBanner(r.ok ? 'Branch created' : (r.error || r.stderr || 'Could not create branch'), r.ok ? 'ok' : 'warn');
  await renderChangesPanel();
}
async function wbShowDiff(path, staged){
  const r = await wbCall('get_file_diff', null, path, staged);
  if(!r || !r.ok){ flashBanner((r && (r.error || r.stderr)) || 'Could not load diff', 'warn'); return; }
  document.getElementById('spTitle').textContent = 'Diff · ' + path;
  panelRaw = r.diff || '';
  document.getElementById('spBody').innerHTML = renderDiff(panelRaw || '(no diff)');
}

async function renderFilesPanel(query){
  const q = query !== undefined ? query : ((document.getElementById('wbFileSearch') || {}).value || '');
  wbSet('<div class="wb-empty">Loading files…</div>');
  const res = q ? await wbCall('search_workspace_files', null, q) : await wbCall('list_workspace_files');
  if(!res || !res.ok){ wbSet(wbErr(res)); return; }
  const rows = (res.files || []).map(f =>
    `<button class="wb-file-main wb-file-row" onclick="wbOpenFile('${escapeJsArg(f.path)}')">
      <span class="wb-file-name">${escapeHtml(f.path)}</span>
      <span class="wb-muted">${Math.round((f.size||0)/1024)} KB</span>
    </button>`
  ).join('') || '<div class="wb-empty">No files found.</div>';
  wbSet(`
    <input id="wbFileSearch" class="wb-input wb-search" placeholder="Search files" value="${escapeAttr(q)}"
      oninput="renderFilesPanel(this.value)">
    <div class="wb-list">${rows}</div>`);
}
async function wbOpenFile(path){
  const r = await wbCall('read_workspace_file', null, path);
  if(!r || !r.ok){ flashBanner((r && r.error) || 'Could not read file', 'warn'); return; }
  const lang = path.endsWith('.py') ? 'python' : '';
  openPanel(path, r.content || '', lang);
}

async function renderActivityPanel(){
  wbSet('<div class="wb-empty">Loading activity…</div>');
  const res = await wbCall('list_activity');
  if(!res || !res.ok){ wbSet(wbErr(res)); return; }
  const rows = (res.activity || []).map(a =>
    `<div class="wb-activity">
      <span class="wb-dot ${escapeAttr(a.kind || '')}"></span>
      <div><b>${escapeHtml(a.title || '')}</b>${a.body ? `<p>${escapeHtml(a.body)}</p>` : ''}<span>${new Date((a.created_at||0)*1000).toLocaleString()}</span></div>
    </div>`
  ).join('') || '<div class="wb-empty">No activity yet.</div>';
  wbSet(`<div class="wb-list">${rows}</div>`);
}

async function renderGithubPanel(){
  wbSet('<div class="wb-empty">Loading GitHub context…</div>');
  const auth = await wbCall('get_github_auth_status');
  let authLine = auth.authenticated ? `Signed in as ${escapeHtml(auth.login || 'GitHub')}` :
    'Not authenticated. Set GITHUB_TOKEN, GH_TOKEN, or sign in with gh.';
  const prs = await wbCall('list_pull_requests');
  const issues = await wbCall('list_issues');
  const prRows = (prs.pull_requests || []).map(pr =>
    `<div class="wb-gh-row"><b>#${pr.number} ${escapeHtml(pr.title)}</b><span>${escapeHtml(pr.head_branch || '')} → ${escapeHtml(pr.base_branch || '')}</span>
      <button class="wb-mini" onclick="wbOpenPrSession(${pr.number})">Open session</button></div>`
  ).join('') || `<div class="wb-empty">${escapeHtml(prs.error || 'No open pull requests.')}</div>`;
  const issueRows = (issues.issues || []).map(issue =>
    `<div class="wb-gh-row"><b>#${issue.number} ${escapeHtml(issue.title)}</b><span>${escapeHtml(issue.author || '')}</span>
      <button class="wb-mini" onclick="wbOpenIssueSession(${issue.number})">Open session</button></div>`
  ).join('') || `<div class="wb-empty">${escapeHtml(issues.error || 'No open issues.')}</div>`;
  wbSet(`
    <div class="wb-note">${authLine}</div>
    <div class="wb-section-title">Pull requests</div>
    <div class="wb-list">${prRows}</div>
    <div class="wb-section-title">Issues</div>
    <div class="wb-list">${issueRows}</div>
    <div class="wb-section-title">Create pull request</div>
    <input id="wbPrTitle" class="wb-input" placeholder="Title">
    <textarea id="wbPrBody" class="wb-textarea" placeholder="Body"></textarea>
    <button class="wb-primary" onclick="wbCreatePr()">Create PR</button>`);
}
async function wbOpenIssueSession(number){
  const r = await wbCall('open_issue_session', null, number);
  if(r && r.ok){ await loadConversations(); await openConversation(r.session.id); }
}
async function wbOpenPrSession(number){
  const r = await wbCall('open_pr_session', null, number);
  if(r && r.ok){ await loadConversations(); await openConversation(r.session.id); }
}
async function wbCreatePr(){
  const title = (document.getElementById('wbPrTitle') || {}).value || '';
  const body = (document.getElementById('wbPrBody') || {}).value || '';
  const r = await wbCall('create_pull_request', null, title, body);
  flashBanner(r.ok ? 'Pull request created' : (r.error || 'Could not create PR'), r.ok ? 'ok' : 'warn');
}

async function renderWorkflowsPanel(){
  wbSet('<div class="wb-empty">Loading workflows…</div>');
  const res = await wbCall('list_workflows');
  if(!res || !res.ok){ wbSet(wbErr(res)); return; }
  const rows = (res.workflows || []).map(w =>
    `<div class="wb-gh-row"><b>${escapeHtml(w.name)}</b><span>${escapeHtml((w.definition || {}).command || '')}</span>
      <button class="wb-mini" onclick="wbRunWorkflow('${escapeJsArg(w.id)}')">Run</button></div>`
  ).join('') || '<div class="wb-empty">No workflows saved.</div>';
  wbSet(`
    <div class="wb-section-title">Saved workflows</div>
    <div class="wb-list">${rows}</div>
    <div class="wb-section-title">New workflow</div>
    <input id="wbWorkflowName" class="wb-input" placeholder="Name">
    <textarea id="wbWorkflowCommand" class="wb-textarea" placeholder="Command, e.g. npm test"></textarea>
    <button class="wb-primary" onclick="wbSaveWorkflow()">Save workflow</button>`);
}
async function wbSaveWorkflow(){
  const name = (document.getElementById('wbWorkflowName') || {}).value || '';
  const command = (document.getElementById('wbWorkflowCommand') || {}).value || '';
  const r = await wbCall('save_workflow', null, {name, definition:{command}});
  flashBanner(r.ok ? 'Workflow saved' : (r.error || 'Could not save workflow'), r.ok ? 'ok' : 'warn');
  await renderWorkflowsPanel();
}
async function wbRunWorkflow(id){
  const r = await wbCall('run_workflow', null, id);
  openPanel('Workflow run', (r.output || r.error || '').trim() || r.status || '', '');
}

function renderTerminalPanelTab(){
  wbSet(`
    <div class="wb-note">The integrated terminal runs in the active workspace folder.</div>
    <button class="wb-primary" onclick="toggleTerminal()">Toggle terminal</button>
    <button class="wb-mini" onclick="termNewTab()">New terminal tab</button>`);
}
