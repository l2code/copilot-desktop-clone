function quickAsk(text){
  const input = document.getElementById('input');
  input.value = text; autoGrow(input); toggleSend(); sendMessage();
}
function newChat(){
  started = false;
  currentConvId = newConvId();
  currentMessages = [];
  document.getElementById('threadInner').innerHTML = document.getElementById('emptyTemplate').innerHTML;
  renderSidebar();
}

function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function addUserMessage(text, _replay, attNames){
  const inner = document.getElementById('threadInner');
  if(!started){ inner.innerHTML=''; started=true; }
  const div = document.createElement('div');
  div.className='msg user';
  let atts = '';
  if(attNames && attNames.length){
    atts = '<div class="msg-atts">' +
      attNames.map(n=>`<span class="chip">${clipSvg()}${escapeHtml(n)}</span>`).join('') + '</div>';
  }
  div.innerHTML = `<div class="m-body">
    <div class="m-content">${text ? `<p>${escapeHtml(text)}</p>` : ''}${atts}</div></div>`;
  inner.appendChild(div);
  scrollDown();
}

function assistantShell(){
  const inner = document.getElementById('threadInner');
  const div = document.createElement('div');
  div.className='msg assistant';
  div.innerHTML = `<div class="m-body">
    <div class="m-content" id="streamTarget"></div></div>`;
  inner.appendChild(div);
  scrollDown();
  return div.querySelector('#streamTarget');
}

let autoScroll = true;
function scrollDown(force){
  const t = document.getElementById('thread');
  if(force) autoScroll = true;
  if(autoScroll) t.scrollTop = t.scrollHeight;
  const b = document.getElementById('scrollBtn');
  if(b) b.classList.toggle('show', !autoScroll);
}

// canned reply with a code block, streamed word-by-word
function buildReply(){
  return {
    intro:"Here's a clean way to do that. The key idea is to wrap the call in a timer that resets every time the function is invoked, so it only fires once activity settles.",
    code:[
      ['com','# debounce.py'],
      ['plain',''],
      ['kw','import '],['plain','threading'],
      ['plain',''],
      ['plain',''],
      ['kw','def '],['fn','debounce'],['plain','(wait):'],
      ['plain','    '],['kw','def '],['fn','decorator'],['plain','(fn):'],
      ['plain','        timer = '],['kw','None'],
      ['plain','        '],['kw','def '],['fn','wrapped'],['plain','(*args, **kwargs):'],
      ['plain','            '],['kw','nonlocal '],['plain','timer'],
      ['plain','            '],['kw','if '],['plain','timer: timer.cancel()'],
      ['plain','            timer = threading.Timer(wait, '],['kw','lambda'],['plain',': fn(*args, **kwargs))'],
      ['plain','            timer.start()'],
      ['plain','        '],['kw','return '],['plain','wrapped'],
      ['plain','    '],['kw','return '],['plain','decorator'],
    ],
    outro:"Call it as a decorator with the delay in seconds, e.g. <code class=\"inline\">@debounce(0.3)</code>. Want me to add a leading-edge option or write tests for it?"
  };
}

function renderCode(lines){
  let html='';
  let line='';
  lines.forEach(([cls,txt])=>{
    if(cls==='plain') line+=escapeHtml(txt);
    else line+=`<span class="${cls}">${escapeHtml(txt)}</span>`;
    // newline boundary: each entry that's pure plain '' or starts fresh handled simply
  });
  // simpler: rebuild line by line
  return null;
}

function sendMessage(){
  const input = document.getElementById('input');
  const text = input.value.trim();
  const atts = pendingAttachments.slice();
  if(!text && !atts.length) return;
  autoScroll = true;
  const attNames = atts.map(a=>a.displayName);
  addUserMessage(text, false, attNames);
  currentMessages.push({role:'user', content:text, attachments:attNames});
  input.value=''; autoGrow(input);
  pendingAttachments = []; renderAttachments(); toggleSend();

  const target = assistantShell();
  target.innerHTML = '<p><span class="cursor-blink"></span></p>';

  if(backendReady){
    // Real Copilot. Deltas arrive via window.onCopilotDelta().
    curTarget = target; curBuf = "";
    setStreaming(true);
    window.pywebview.api.send(text, atts);
  } else {
    // Demo fallback (no backend present).
    setTimeout(()=>streamText(target, buildReply()), 450);
  }
}

// ===== Send / Stop =====
let streaming = false;
function onSendOrStop(){ if(streaming) stopStreaming(); else sendMessage(); }
function setStreaming(on){
  streaming = on;
  const b = document.getElementById('send');
  if(on){
    b.classList.add('stop'); b.disabled = false;
    b.innerHTML = '<svg viewBox="0 0 16 16"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/></svg>';
  } else {
    b.classList.remove('stop');
    b.innerHTML = '<svg viewBox="0 0 16 16"><path d="M1.5 8 14 2 9.5 8 14 14z"/></svg>';
    toggleSend();
  }
}
function stopStreaming(){
  if(backendReady){ try{ window.pywebview.api.abort(); }catch(e){} }
  if(curTarget){
    const raw = curBuf;
    const ans = curTarget.querySelector('.ans');
    const out = raw.trim() ? renderMarkdown(raw) : '<p class="set-val">(stopped)</p>';
    if(ans) ans.innerHTML = out; else curTarget.innerHTML = out;
    if(raw.trim()){ addMessageActions(curTarget, raw); currentMessages.push({role:'assistant', content:raw}); persistCurrent(); }
    curTarget = null; curBuf = "";
  }
  setStreaming(false);
}

// ===== Streaming callbacks invoked by app.py =====
function ensureStream(){
  let ans = curTarget.querySelector('.ans');
  if(!ans){
    if(!curTarget.querySelector('.act')){
      curTarget.innerHTML = '<div class="act"></div><div class="ans stream-live"></div>';
    } else {
      const a=document.createElement('div'); a.className='ans stream-live'; curTarget.appendChild(a);
    }
    ans = curTarget.querySelector('.ans');
  }
  return { act: curTarget.querySelector('.act'), ans };
}
function actSvg(kind){
  if(kind==='tool') return '<svg viewBox="0 0 16 16"><path d="M11.5 1.5a3.5 3.5 0 00-3.2 4.9L1.7 13a1.1 1.1 0 001.5 1.5l6.6-6.6a3.5 3.5 0 004.5-4.4l-2.1 2.1-1.6-1.6 2.1-2.1a3.5 3.5 0 00-1.5-.4z"/></svg>';
  return '<svg viewBox="0 0 16 16"><path d="M1 3.5A1.5 1.5 0 012.5 2h11A1.5 1.5 0 0115 3.5v9A1.5 1.5 0 0113.5 14h-11A1.5 1.5 0 011 12.5v-9zM4 6l2 2-2 2 .9.9L7.8 8 4.9 5.1zm4.5 3.5h3V11h-3z"/></svg>';
}
function onCopilotActivity(d){
  if(!d) return;
  if(d.kind === 'context'){ onContextUsage(d.current, d.limit); return; }
  if(d.kind === 'mcp_status'){ mcpStatus = d.servers || {}; renderMcpList(); return; }
  if(!curTarget) return;
  const { act } = ensureStream();
  if(d.kind==='reasoning_delta'){
    let think = act.querySelector('.thinking');
    if(!think){
      act.insertAdjacentHTML('afterbegin','<details class="thinking" open><summary>Thinking…</summary><div class="think-body"></div></details>');
      think = act.querySelector('.thinking');
    }
    think.querySelector('.think-body').textContent += (d.text||'');
  } else if(d.kind==='reasoning_done'){
    const think = act.querySelector('.thinking');
    if(think){ think.open=false; const s=think.querySelector('summary'); if(s) s.textContent='Thought for a moment'; }
  } else if(d.kind==='tool'){
    const who = d.mcp ? `<b>${escapeHtml(d.mcp)}</b> \u00b7 ` : '';
    const det = (who ? ' '+who : '') + (d.detail ? ` <code>${escapeHtml(d.detail)}</code>` : '');
    const base = d.detail ? String(d.detail).split(/[\\/]/).pop() : '';
    const hasDiff = d.detail && (lastDiffs[d.detail] || lastDiffs[base]);
    const click = hasDiff ? ` onclick="openDiffPanel('${escapeJsArg(d.detail)}')" style="cursor:pointer"` : '';
    const badge = hasDiff ? ' <span class="act-diff-badge">view diff</span>' : '';
    act.insertAdjacentHTML('beforeend',
      `<div class="act-line" data-aid="${escapeAttr(d.id||'')}"${click}>${actSvg('tool')}<span><b>${escapeHtml(d.name||'tool')}</b>${det}${badge}</span><span class="act-status">…</span></div>`);
  } else if(d.kind==='tool_done'){
    const line = act.querySelector(`.act-line[data-aid="${escapeAttr(d.id||'')}"]`);
    if(line){ const st=line.querySelector('.act-status'); if(st){ st.textContent = (d.success===false)?'\u2717':'\u2713'; if(d.success===false) st.style.color='#ff7b72'; } }
  } else if(d.kind==='command'){
    act.insertAdjacentHTML('beforeend',
      `<div class="act-line">${actSvg('cmd')}<code>${escapeHtml(d.cmd||d.name||'')}</code></div>`);
  }
  scrollDown();
}
function onCopilotDelta(chunk){
  if(!curTarget) return;
  curBuf += chunk;
  const { ans } = ensureStream();
  ans.innerHTML = escapeHtml(curBuf) + '<span class="cursor-blink"></span>';
  scrollDown();
}
function onCopilotDone(){
  setStreaming(false);
  if(!curTarget) return;
  const raw = curBuf;
  const ans = curTarget.querySelector('.ans');
  if(ans) ans.innerHTML = renderMarkdown(raw); else curTarget.innerHTML = renderMarkdown(raw);
  const think = curTarget.querySelector('.thinking');
  if(think){ think.open=false; const s=think.querySelector('summary'); if(s) s.textContent='Thought for a moment'; }
  addMessageActions(curTarget, raw);
  curTarget = null; curBuf = "";
  // Save only once we actually have a reply from the LLM -- empty/errored turns
  // never clutter the sidebar.
  if(raw.trim()){ currentMessages.push({role:'assistant', content:raw}); persistCurrent(); }
  if(typeof refreshUsage === 'function') refreshUsage();   // usage updates after each reply
  scrollDown();
}
function addMessageActions(container, raw){
  if(!raw || !raw.trim()) return;
  const bar = document.createElement('div');
  bar.className = 'msg-actions';
  const copyIcon = '<svg viewBox="0 0 16 16"><path d="M5 2a2 2 0 00-2 2v7h1.5V4A.5.5 0 015 3.5h6V2H5z"/><path d="M7 5a2 2 0 00-2 2v6a2 2 0 002 2h5a2 2 0 002-2V7a2 2 0 00-2-2H7z"/></svg>';
  const okIcon = '<svg viewBox="0 0 16 16"><path d="M13.5 4.5l-7 7L3 8l1-1 2.5 2.5 6-6z"/></svg>';
  bar.innerHTML = `<span class="msg-act-btn" title="Copy">${copyIcon}</span>`;
  const btn = bar.querySelector('.msg-act-btn');
  btn.addEventListener('click', ()=>{
    if(navigator.clipboard) navigator.clipboard.writeText(raw);
    btn.innerHTML = okIcon; setTimeout(()=>{ btn.innerHTML = copyIcon; }, 1500);
  });
  container.appendChild(bar);
}
function onCopilotError(msg){
  setStreaming(false);
  if(curTarget){
    curTarget.innerHTML = `<p style="color:#ff7b72">⚠ ${escapeHtml(msg)}</p>`;
    curTarget = null; curBuf = "";
  }
  showBanner('err', msg);
}

// ===== Minimal Markdown renderer (fenced code + inline code + bold + paragraphs) =====
function renderMarkdown(md){
  const parts = md.split(/```/);
  let html = '';
  parts.forEach((seg, i)=>{
    if(i % 2 === 1){
      // code block: first line may be a language tag
      let lang = '', code = seg;
      const nl = seg.indexOf('\n');
      if(nl !== -1){
        const first = seg.slice(0, nl).trim();
        if(/^[a-zA-Z0-9_+-]+$/.test(first)){ lang = first; code = seg.slice(nl+1); }
      }
      code = code.replace(/\n$/, '');
      const body = (lang === 'python') ? highlightPython(code) : escapeHtml(code);
      const id = 'cb' + Math.random().toString(36).slice(2);
      html += `<div class="codeblock" data-raw="${escapeAttr(code)}" data-lang="${escapeAttr(lang||'')}">
        <div class="cb-head"><span>${escapeHtml(lang||'code')}</span>
        <div class="cb-head-actions">
        <span class="cb-expand" onclick="openPanelFromBlock(this)" title="Open in side panel"><svg viewBox="0 0 16 16"><path d="M2 2h5v1.5H3.5v3.5H2zM14 14H9v-1.5h3.5V9H14z"/></svg>Expand</span>
        <span class="cb-copy" onclick="copyCode(this)"><svg viewBox="0 0 16 16"><path d="M5 2a2 2 0 00-2 2v7h1.5V4A.5.5 0 015 3.5h6V2H5z"/><path d="M7 5a2 2 0 00-2 2v6a2 2 0 002 2h5a2 2 0 002-2V7a2 2 0 00-2-2H7zm-.5 2A.5.5 0 017 6.5h5a.5.5 0 01.5.5v6a.5.5 0 01-.5.5H7a.5.5 0 01-.5-.5V7z"/></svg>Copy</span>
        </div></div>
        <pre>${body}</pre></div>`;
    } else {
      html += renderTextBlock(seg);
    }
  });
  return html || '<p></p>';
}
function inlineMd(s){
  return escapeHtml(s)
    .replace(/`([^`]+)`/g,'<code class="inline">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g,'$1<em>$2</em>');
}
function renderTextBlock(text){
  const lines = text.split('\n');
  let html='', para=[], listType=null, listBuf=[];
  const flushPara=()=>{ if(para.length){ html+=`<p>${para.map(inlineMd).join('<br>')}</p>`; para=[]; } };
  const flushList=()=>{ if(listType){ html+=`<${listType}>`+listBuf.map(li=>`<li>${inlineMd(li)}</li>`).join('')+`</${listType}>`; listType=null; listBuf=[]; } };
  lines.forEach(raw=>{
    const line = raw.replace(/\s+$/,'');
    let m;
    if(!line.trim()){ flushPara(); flushList(); return; }
    if((m=line.match(/^(#{1,6})\s+(.*)$/))){ flushPara(); flushList();
      const lvl=Math.min(6, m[1].length+2); html+=`<h${lvl}>${inlineMd(m[2])}</h${lvl}>`; return; }
    if((m=line.match(/^\s*[-*+]\s+(.*)$/))){ flushPara(); if(listType&&listType!=='ul') flushList();
      listType='ul'; listBuf.push(m[1]); return; }
    if((m=line.match(/^\s*\d+\.\s+(.*)$/))){ flushPara(); if(listType&&listType!=='ol') flushList();
      listType='ol'; listBuf.push(m[1]); return; }
    flushList(); para.push(line);
  });
  flushPara(); flushList();
  return html;
}
function escapeAttr(s){ return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escapeJsArg(s){
  return String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/\r/g,'\\r').replace(/\n/g,'\\n').replace(/</g,'\\x3c');
}

function streamText(target, reply){
  const words = reply.intro.split(' ');
  let i=0;
  target.innerHTML = '<p id="p1"></p>';
  const p1 = target.querySelector('#p1');
  const iv = setInterval(()=>{
    if(i<words.length){
      p1.innerHTML = words.slice(0,i+1).join(' ') + ' <span class="cursor-blink"></span>';
      i++; scrollDown();
    } else {
      clearInterval(iv);
      p1.innerHTML = reply.intro;
      appendCode(target, reply);
    }
  }, 32);
}

function appendCode(target, reply){
  // assemble code text honoring newlines
  const codeText =
`# debounce.py
import threading

def debounce(wait):
    def decorator(fn):
        timer = None
        def wrapped(*args, **kwargs):
            nonlocal timer
            if timer: timer.cancel()
            timer = threading.Timer(wait, lambda: fn(*args, **kwargs))
            timer.start()
        return wrapped
    return decorator`;

  const highlighted = highlightPython(codeText);
  const cb = document.createElement('div');
  cb.className='codeblock';
  cb.innerHTML = `<div class="cb-head"><span>python</span>
    <span class="cb-copy" onclick="copyCode(this)"><svg viewBox="0 0 16 16"><path d="M5 2a2 2 0 00-2 2v7h1.5V4A.5.5 0 015 3.5h6V2H5z"/><path d="M7 5a2 2 0 00-2 2v6a2 2 0 002 2h5a2 2 0 002-2V7a2 2 0 00-2-2H7zm-.5 2A.5.5 0 017 6.5h5a.5.5 0 01.5.5v6a.5.5 0 01-.5.5H7a.5.5 0 01-.5-.5V7z"/></svg>Copy</span></div>
    <pre>${highlighted}</pre>`;
  cb.dataset.raw = codeText;
  target.appendChild(cb);

  const outro = document.createElement('p');
  outro.style.marginTop='10px';
  outro.innerHTML = reply.outro;
  target.appendChild(outro);
  scrollDown();
}

function highlightPython(code){
  // Highlight line-by-line so a comment span can't be re-scanned by the keyword
  // pass (which previously matched "class" inside class="com" and broke the HTML).
  return escapeHtml(code).split('\n').map(line=>{
    const hash = line.indexOf('#');
    let codePart = hash === -1 ? line : line.slice(0, hash);
    let comPart  = hash === -1 ? ''   : line.slice(hash);
    codePart = codePart.replace(
      /\b(import|def|return|if|nonlocal|lambda|None|class|for|while|in|from)\b/g,
      '<span class="kw">$1</span>');
    if(comPart) comPart = `<span class="com">${comPart}</span>`;
    return codePart + comPart;
  }).join('\n');
}

function copyCode(el){
  const raw = el.closest('.codeblock').dataset.raw;
  navigator.clipboard && navigator.clipboard.writeText(raw);
  const orig = el.innerHTML;
  el.innerHTML = '<svg viewBox="0 0 16 16"><path d="M13.5 4.5l-7 7L3 8"/></svg>Copied';
  setTimeout(()=>{ el.innerHTML = orig; }, 1500);
}

// stash empty-state markup so "New chat" can restore it
const tmpl = document.createElement('template');
tmpl.id='emptyTemplate';
tmpl.innerHTML = document.getElementById('threadInner').innerHTML;
document.body.appendChild(tmpl);
