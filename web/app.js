let TOKEN=localStorage.getItem('maxai_token')||'';
let MODE='chat';

async function ensureAuth(){
  if(TOKEN){
    try{
      const r=await fetch('/api/projects',{headers:{'Authorization':'Bearer '+TOKEN}});
      if(r.ok)return true;
    }catch(e){}
    TOKEN='';
    localStorage.removeItem('maxai_token');
  }
  const pw=prompt('รหัสผ่าน:');
  if(!pw)return false;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  if(!r.ok){alert('รหัสไม่ถูก');return false;}
  const d=await r.json();
  TOKEN=d.token;
  localStorage.setItem('maxai_token',TOKEN);
  return true;
}

async function api(url,opts){
  opts=opts||{};
  const h=Object.assign({},opts.headers||{},{'Authorization':'Bearer '+TOKEN});
  const r=await fetch(url,Object.assign({},opts,{headers:h}));
  if(r.status===401){
    TOKEN='';
    localStorage.removeItem('maxai_token');
    location.reload();
  }
  return r;
}

function log(t,c){
  c=c||'info';
  const el=document.getElementById('log');
  const d=document.createElement('div');
  d.className='entry '+c;
  d.textContent=t;
  el.appendChild(d);
  el.scrollTop=el.scrollHeight;
  while(el.children.length>500)el.removeChild(el.firstChild);
}

async function send(){
  const inp=document.getElementById('input');
  const task=inp.value.trim();
  if(!task)return;
  inp.value='';
  rz(inp);
  log('USER: '+task,'user');
  const b=document.getElementById('sendBtn');
  b.disabled=true;
  b.textContent='...';
  try{
    const url=MODE==='chat'?'/api/chat':'/api/run';
    const r=await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:task})});
    const d=await r.json();
    if(MODE==='chat'){
      const reply=d.reply||d.answer||d.log||'';
      log('AI: '+reply,'ok');
    }else{
      if(d.log){
        d.log.split('\n').forEach(function(line){
          if(!line.trim())return;
          let c='info';
          if(line.indexOf('USER')===0)c='user';
          else if(line.indexOf('DONE')===0||line.indexOf('OK')===0)c='ok';
          else if(line.indexOf('error')===0||line.indexOf('fail')===0)c='err';
          log(line,c);
        });
      }
      const m=(d.log||'').match(/\/site\/(\w+)/);
      if(m){
        const proj=m[1];
        const el=document.getElementById('log');
        const bd=document.createElement('div');
        bd.className='entry ok';
        bd.innerHTML='<div>🌐 '+proj+'</div><a class="lnk" onclick="openPreview(\'/site/'+proj+'/index.html\');return false">👁️ ดู</a> <a class="lnk" href="/api/download/'+proj+'?token='+TOKEN+'">📦 ZIP</a>';
        el.appendChild(bd);
        el.scrollTop=el.scrollHeight;
      }
    }
  }catch(e){
    log('error: '+e,'err');
  }finally{
    b.disabled=false;
    b.textContent='▶';
  }
}

function toggleMode(){
  MODE=MODE==='chat'?'agent':'chat';
  const btn=document.getElementById('modeBtn');
  if(btn)btn.textContent=MODE==='chat'?'💬':'🔧';
  const inp=document.getElementById('input');
  if(inp){
    inp.placeholder=MODE==='chat'?'พิมพ์คำถาม...':'บอก AI ว่าต้องการอะไร...';
  }
  log('โหมด: '+(MODE==='chat'?'💬 แชท':'🔧 Agent'),'info');
}

function q(t){
  document.getElementById('input').value=t;
  send();
}

function key(e){
  if(e.key==='Enter'&&!e.shiftKey){
    e.preventDefault();
    send();
  }
}

function rz(el){
  el.style.height='auto';
  el.style.height=Math.min(el.scrollHeight,120)+'px';
}

function toggleMenu(){
  document.getElementById('drawer').classList.toggle('open');
}

document.addEventListener('click',function(e){
  if(e.target.id==='drawer')toggleMenu();
});

async function listProjects(){
  toggleMenu();
  try{
    const r=await api('/api/projects');
    const d=await r.json();
    if(!d.projects.length){log('ว่าง','info');return;}
    let html='';
    d.projects.forEach(function(p){
      html+='<div class="item"><div class="name">📁 '+p.name+'</div><div class="desc">'+p.files.length+' ไฟล์</div>';
      if(p.files.length){
        html+='<div style="margin-top:6px"><a class="lnk" onclick="openPreview(\'/site/'+p.name+'/index.html\');return false">👁️ ดู</a> <a class="lnk" href="/api/download/'+p.name+'?token='+TOKEN+'">📦 ZIP</a></div>';
      }
      html+='</div>';
    });
    openModal('📦 โปรเจกต์ ('+d.projects.length+')',html);
  }catch(e){log('error: '+e,'err');}
}

function clearLog(){
  toggleMenu();
  document.getElementById('log').innerHTML='';
}

function logout(){
  localStorage.removeItem('maxai_token');
  location.reload();
}

function openModal(title,html){
  document.getElementById('modalTitle').textContent=title;
  document.getElementById('modalBody').innerHTML=html;
  document.getElementById('modal').classList.add('show');
}

function closeModal(){
  document.getElementById('modal').classList.remove('show');
}

document.getElementById('modal').addEventListener('click',function(e){
  if(e.target.id==='modal')closeModal();
});

function openPreview(url){
  document.getElementById('previewFrame').src=url;
  document.getElementById('previewOpen').href=url;
  document.getElementById('preview').classList.add('show');
}

function closePreview(){
  document.getElementById('preview').classList.remove('show');
  document.getElementById('previewFrame').src='';
}

async function init(){
  if(!(await ensureAuth()))return;
  try{
    const r=await fetch('/api/health');
    const d=await r.json();
    document.getElementById('status').textContent=(d.ai?'AI':'no AI');
  }catch(e){}
  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js').catch(function(){});
  }
  log('⚡ MAXAI v2 พร้อม','ok');
  log('💬 Chat Mode — พิมพ์คำถามได้เลย','info');
  log('🔧 กดปุ่ม mode เพื่อสลับเป็น Agent (สร้างเว็บ/เขียนโค้ด)','info');
}

document.addEventListener('DOMContentLoaded',init);