// ===== Copilot App-style workspace workbench =====
let wbTab = 'changes';
let wbBusy = false;

const WB_TABS = [
  ['changes', 'Changes'],
  ['files', 'Files'],
  ['activity', 'Activity'],
  ['gitlab', 'GitLab'],
  ['github', 'GitHub'],
  ['workflows', 'Workflows'],
  ['troubleshoot', 'Troubleshoot'],
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
    else if(wbTab === 'gitlab') await renderGitlabPanel();
    else if(wbTab === 'github') await renderGithubPanel();
    else if(wbTab === 'workflows') await renderWorkflowsPanel();
    else if(wbTab === 'troubleshoot') await renderTroubleshootPanel();
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

async function renderGitlabPanel(){
  wbSet('<div class="wb-empty">Loading GitLab backlog…</div>');
  const env = await wbCall('get_gitlab_env_status');
  const config = await wbCall('get_gitlab_settings');
  const settings = (config && config.settings) || {};
  const auth = await wbCall('get_gitlab_auth_status');
  const project = await wbCall('get_gitlab_project');
  const target = project && project.project_target ? project.project_target : '';
  const urlValue = settings.url || (env && env.url_source && env.url_source !== 'default' ? env.base_url : '');
  const authType = settings.auth_type || (env && env.auth_type) || 'private-token';
  const projectValue = settings.project || (env && env.default_project) || target || '';
  const groupValue = settings.group || (env && env.default_group) || '';
  const tokenPlaceholder = settings.token_configured
    ? 'Token saved, leave blank to keep'
    : ((env && env.token_source) ? `Token detected from ${env.token_source}` : 'Personal access token');
  const authLine = auth.authenticated
    ? `Signed in as ${escapeHtml(auth.username || auth.name || 'GitLab')} · ${escapeHtml(auth.base_url || '')}`
    : (auth && auth.error
      ? `Not authenticated: ${escapeHtml(auth.error)}`
      : `Not authenticated. Set a token below or with GITLAB_TOKEN, GITLAB_PERSONAL_ACCESS_TOKEN, GL_TOKEN, or GITLAB_PRIVATE_TOKEN.`);
  const envLine = env && env.ok
    ? `GitLab API: ${escapeHtml(env.api_url || env.base_url || '')} · Source: ${escapeHtml(env.url_source || 'default')} · Auth: ${escapeHtml(env.auth_type || 'private-token')} · Token: ${escapeHtml(env.token_source || 'not detected')}${env.default_project ? ' · Project: ' + escapeHtml(env.default_project) : ''}${env.default_group ? ' · Group: ' + escapeHtml(env.default_group) : ''}`
    : '';
  const envFile = (env && env.env_file) || {};
  const envFileLine = envFile.loaded_path
    ? `Env file: ${escapeHtml(envFile.loaded_path)} · Keys: ${escapeHtml((envFile.gitlab_keys || []).join(', ') || 'no GitLab keys')}`
    : (envFile.explicit
      ? `Env file not loaded: ${escapeHtml(envFile.explicit)}${envFile.explicit_exists === false ? ' (not found)' : ''}`
      : 'Env file: not configured');
  const backlog = await wbCall('list_gitlab_backlog', target || null, 'project', 'opened', null, null);
  const issues = (backlog.issues || []).map(issue => {
    const labels = (issue.labels || []).map(l=>`<span class="wb-label">${escapeHtml(l)}</span>`).join('');
    return `<div class="wb-gh-row wb-gitlab-issue">
      <div>
        <b>#${issue.iid} ${escapeHtml(issue.title || '')}</b>
        <span>${escapeHtml(issue.issue_type || 'issue')} · ${escapeHtml(issue.state || '')} · ${escapeHtml(issue.updated_at || '')}</span>
        <div class="wb-labels">${labels}</div>
      </div>
      <div class="wb-actions">
        <button class="wb-mini" onclick="wbGitlabPromptIssue(${issue.iid}, '${escapeJsArg(issue.title || '')}')">Ask</button>
        <button class="wb-mini" onclick="wbGitlabCloseIssue(${issue.iid})">Close</button>
      </div>
    </div>`;
  }).join('') || `<div class="wb-empty">${escapeHtml(backlog.error || 'No open backlog items found.')}</div>`;
  wbSet(`
    <div class="wb-section-title">GitLab connection</div>
    <div class="wb-config">
      <label>URL or API URL<input id="wbGitlabUrl" class="wb-input" placeholder="https://gitlab.example.com/api/v4" value="${escapeAttr(urlValue)}"></label>
      <label>Project<input id="wbGitlabDefaultProject" class="wb-input" placeholder="project id or group/project" value="${escapeAttr(projectValue)}"></label>
      <label>Group<input id="wbGitlabDefaultGroup" class="wb-input" placeholder="group id or path" value="${escapeAttr(groupValue)}"></label>
      <label>Auth<select id="wbGitlabAuthType" class="wb-input">
        <option value="private-token" ${authType === 'private-token' ? 'selected' : ''}>PRIVATE-TOKEN</option>
        <option value="bearer" ${authType === 'bearer' ? 'selected' : ''}>Bearer</option>
        <option value="both" ${authType === 'both' ? 'selected' : ''}>Both</option>
      </select></label>
      <label>Token<input id="wbGitlabToken" class="wb-input" type="password" placeholder="${escapeAttr(tokenPlaceholder)}"></label>
      <div class="wb-actions">
        <button class="wb-primary" onclick="wbGitlabSaveSettings()">Save connection</button>
        ${settings.token_configured ? '<button class="wb-mini danger" onclick="wbGitlabClearToken()">Clear token</button>' : ''}
      </div>
    </div>
    <div class="wb-note">${authLine}</div>
    <div class="wb-note">${envLine}</div>
    <div class="wb-note">${envFileLine}</div>
    <div class="wb-row">
      <input id="wbGitlabTarget" class="wb-input" placeholder="group/project or project id" value="${escapeAttr(target)}">
      <button class="wb-mini" onclick="renderGitlabPanelWithTarget()">Load</button>
    </div>
    <div class="wb-section-title">Backlog</div>
    <div class="wb-list">${issues}</div>
    <div class="wb-section-title">Create issue / story</div>
    <input id="wbGitlabTitle" class="wb-input" placeholder="Title">
    <textarea id="wbGitlabDescription" class="wb-textarea" placeholder="Description"></textarea>
    <input id="wbGitlabLabels" class="wb-input" placeholder="Labels, comma-separated">
    <button class="wb-primary" onclick="wbGitlabCreateIssue()">Create GitLab issue</button>
    <div class="wb-section-title">Epics</div>
    <div class="wb-note">GitLab REST epics are deprecated upstream; this panel uses them only as a compatibility fallback. Prefer Work Items/MCP for new epic automation.</div>
    <div class="wb-row">
      <input id="wbGitlabGroup" class="wb-input" placeholder="group path or id">
      <button class="wb-mini" onclick="wbGitlabLoadEpics()">Load epics</button>
    </div>
    <div id="wbGitlabEpics"></div>`);
}

async function wbGitlabSaveSettings(){
  const token = ((document.getElementById('wbGitlabToken') || {}).value || '').trim();
  const patch = {
    url: ((document.getElementById('wbGitlabUrl') || {}).value || '').trim(),
    auth_type: ((document.getElementById('wbGitlabAuthType') || {}).value || 'private-token').trim(),
    project: ((document.getElementById('wbGitlabDefaultProject') || {}).value || '').trim(),
    group: ((document.getElementById('wbGitlabDefaultGroup') || {}).value || '').trim(),
  };
  if(token) patch.token = token;
  const r = await wbCall('update_gitlab_settings', patch);
  flashBanner(r && r.ok ? 'GitLab connection saved' : ((r && r.error) || 'Could not save GitLab connection'), r && r.ok ? 'ok' : 'warn');
  await renderGitlabPanel();
}

function wbGitlabClearToken(){
  askConfirm('Clear the saved GitLab token?', async()=>{
    const r = await wbCall('update_gitlab_settings', {clear_token:true});
    flashBanner(r && r.ok ? 'GitLab token cleared' : ((r && r.error) || 'Could not clear GitLab token'), r && r.ok ? 'ok' : 'warn');
    await renderGitlabPanel();
  }, 'Clear token');
}

async function renderGitlabPanelWithTarget(){
  const target = ((document.getElementById('wbGitlabTarget') || {}).value || '').trim();
  wbSet('<div class="wb-empty">Loading GitLab backlog…</div>');
  const backlog = await wbCall('list_gitlab_backlog', target || null, 'project', 'opened', null, null);
  if(!backlog.ok){ await renderGitlabPanel(); flashBanner(backlog.error || 'Could not load GitLab backlog', 'warn'); return; }
  await renderGitlabPanel();
}

async function wbGitlabCreateIssue(){
  const target = ((document.getElementById('wbGitlabTarget') || {}).value || '').trim() || null;
  const title = ((document.getElementById('wbGitlabTitle') || {}).value || '').trim();
  const description = (document.getElementById('wbGitlabDescription') || {}).value || '';
  const labels = ((document.getElementById('wbGitlabLabels') || {}).value || '').trim() || null;
  if(!title){ flashBanner('Title is required', 'warn'); return; }
  const r = await wbCall('create_gitlab_issue', target, title, description, labels);
  flashBanner(r.ok ? 'GitLab issue created' : (r.error || 'Could not create issue'), r.ok ? 'ok' : 'warn');
  await renderGitlabPanel();
}

async function wbGitlabCloseIssue(iid){
  const target = ((document.getElementById('wbGitlabTarget') || {}).value || '').trim() || null;
  const r = await wbCall('update_gitlab_issue', target, iid, {state_event:'close'});
  flashBanner(r.ok ? 'Issue closed' : (r.error || 'Could not update issue'), r.ok ? 'ok' : 'warn');
  await renderGitlabPanel();
}

function wbGitlabPromptIssue(iid, title){
  closePanel();
  const input = document.getElementById('input');
  input.value = `Look at GitLab issue #${iid}: ${title}\n\nSummarize the acceptance criteria, risks, missing information, and likely implementation tasks.`;
  autoGrow(input); toggleSend(); input.focus();
}

async function wbGitlabLoadEpics(){
  const group = ((document.getElementById('wbGitlabGroup') || {}).value || '').trim();
  if(!group){ flashBanner('Group path or id is required for epics', 'warn'); return; }
  const r = await wbCall('list_gitlab_epics', group, 'opened');
  const host = document.getElementById('wbGitlabEpics');
  if(!host) return;
  if(!r.ok){ host.innerHTML = wbErr(r); return; }
  host.innerHTML = (r.epics || []).map(e =>
    `<div class="wb-gh-row"><div><b>&${e.iid} ${escapeHtml(e.title || '')}</b><span>${escapeHtml(e.state || '')} · ${escapeHtml(e.updated_at || '')}</span></div></div>`
  ).join('') || '<div class="wb-empty">No open epics found.</div>';
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

async function renderTroubleshootPanel(){
  wbSet('<div class="wb-empty">Loading diagnostics…</div>');
  const summary = await wbCall('get_troubleshooting_summary');
  if(!summary || !summary.ok){ wbSet(wbErr(summary)); return; }
  const logs = (summary.logs || []).map(l =>
    `<button class="wb-file-main wb-file-row" onclick="wbReadLog('${escapeJsArg(l.name)}')">
      <span class="wb-file-name">${escapeHtml(l.name)}</span>
      <span class="wb-muted">${Math.round((l.size||0)/1024)} KB · ${new Date((l.updated_at||0)*1000).toLocaleString()}</span>
    </button>`
  ).join('') || '<div class="wb-empty">No app logs found yet.</div>';
  wbSet(`
    <div class="wb-note">App data: ${escapeHtml(summary.app_dir || '')}<br>Database: ${escapeHtml(summary.db_path || '')}</div>
    <div class="wb-section-title">MCP troubleshooting</div>
    <div class="wb-note">Use Settings → MCP servers to connect log, database, Kubernetes, cloud, or incident tools. Copilot can use enabled MCP servers during chat with your permission rules.</div>
    <button class="wb-mini" onclick="openMcp()">Manage MCP servers</button>
    <div class="wb-section-title">App logs</div>
    <div class="wb-list">${logs}</div>
    <div class="wb-section-title">Read-only app DB query</div>
    <textarea id="wbDbQuery" class="wb-textarea" spellcheck="false">select name from sqlite_master where type = 'table' order by name</textarea>
    <button class="wb-primary" onclick="wbRunDbQuery()">Run query</button>
    <div id="wbDbResult"></div>
    <div class="wb-section-title">Create GitLab issue from findings</div>
    <input id="wbTroubleIssueTitle" class="wb-input" placeholder="Issue title">
    <textarea id="wbTroubleIssueBody" class="wb-textarea" placeholder="What happened, impact, evidence, and next steps"></textarea>
    <button class="wb-primary" onclick="wbTroubleCreateGitlabIssue()">Create GitLab issue</button>`);
}

async function wbReadLog(name){
  const r = await wbCall('read_app_log', name);
  if(!r.ok){ flashBanner(r.error || 'Could not read log', 'warn'); return; }
  openPanel('Log · ' + name, r.content || '', '');
}

async function wbRunDbQuery(){
  const sql = (document.getElementById('wbDbQuery') || {}).value || '';
  const r = await wbCall('query_app_db', sql, 100);
  const host = document.getElementById('wbDbResult');
  if(!host) return;
  if(!r.ok){ host.innerHTML = wbErr(r); return; }
  host.innerHTML = `<pre class="wb-json">${escapeHtml(JSON.stringify(r.rows || [], null, 2))}</pre>`;
}

async function wbTroubleCreateGitlabIssue(){
  const title = ((document.getElementById('wbTroubleIssueTitle') || {}).value || '').trim();
  const body = (document.getElementById('wbTroubleIssueBody') || {}).value || '';
  if(!title){ flashBanner('Title is required', 'warn'); return; }
  const r = await wbCall('create_gitlab_issue', null, title, body, 'troubleshooting');
  flashBanner(r.ok ? 'GitLab issue created' : (r.error || 'Could not create issue'), r.ok ? 'ok' : 'warn');
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
