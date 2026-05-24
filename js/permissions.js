// ===== Permission-approval prompts (Copilot wants to run a tool/command/edit) =====
let permQueue = [], permCurrent = null;
function onPermissionRequest(p){ permQueue.push(p); if(!permCurrent) nextPerm(); }
function nextPerm(){
  const modal = document.getElementById('permModal');
  permCurrent = permQueue.shift() || null;
  if(!permCurrent){ modal.classList.remove('open'); return; }
  document.getElementById('permTitle').textContent = permCurrent.title || 'Permission request';
  const r = document.getElementById('permReason');
  r.textContent = permCurrent.reason || ''; r.style.display = permCurrent.reason ? 'block' : 'none';
  const d = document.getElementById('permDetail');
  d.textContent = permCurrent.detail || ''; d.style.display = permCurrent.detail ? 'block' : 'none';
  const dv = document.getElementById('permDiff');
  if(permCurrent.diff){
    dv.innerHTML = renderDiff(permCurrent.diff); dv.style.display = 'block';
    d.style.display = 'none';
    if(permCurrent.file){ lastDiffs[permCurrent.file] = permCurrent.diff; lastDiffs[String(permCurrent.file).split(/[\\/]/).pop()] = permCurrent.diff; }
  } else { dv.style.display = 'none'; }
  document.getElementById('permAllowAll').style.display = (permCurrent.canSession === false) ? 'none' : '';
  modal.classList.add('open');
}
function resolvePerm(decision){
  if(permCurrent && backendReady){
    try{ window.pywebview.api.resolve_permission(permCurrent.id, decision); }catch(e){}
  }
  if(decision === 'approve-all') autoApprove = true;
  nextPerm();
}
