(function(){
  // drag & drop upload areas (the <label> opens the file dialog natively; this adds drag/drop + a file summary)
  document.querySelectorAll('.drop').forEach(function(z){
    var inp=z.querySelector('input[type=file]'), pick=z.querySelector('.picked');
    function human(n){ return n<1048576 ? Math.max(1,Math.round(n/1024))+' KB' : (n/1048576).toFixed(1)+' MB'; }
    function show(){
      pick.innerHTML=''; var fs=inp.files;
      if(!fs.length){ z.classList.remove('has'); return; }
      z.classList.add('has');
      var total=0, i; for(i=0;i<fs.length;i++) total+=fs[i].size;
      var head=document.createElement('div'); head.className='pk-head';
      head.textContent = fs.length>1 ? fs.length+' files selected \u00b7 '+human(total) : fs[0].name+' \u00b7 '+human(fs[0].size);
      pick.appendChild(head);
      if(fs.length>1){
        var names=[]; for(i=0;i<Math.min(3,fs.length);i++) names.push(fs[i].name);
        var l=document.createElement('div'); l.className='pk-list';
        l.textContent=names.join(', ')+(fs.length>3 ? ' \u2026 +'+(fs.length-3)+' more' : '');
        pick.appendChild(l);
      }
      var row=document.createElement('div'); row.className='pk-prev';
      for(i=0;i<fs.length && row.children.length<8;i++){
        if(/^image\//.test(fs[i].type) && fs[i].size<3000000){
          var im=document.createElement('img'); im.alt=''; im.src=URL.createObjectURL(fs[i]);
          im.onload=(function(u){return function(){URL.revokeObjectURL(u);};})(im.src);
          row.appendChild(im);
        }
      }
      if(row.children.length) pick.appendChild(row);
    }
    ['dragenter','dragover'].forEach(function(ev){z.addEventListener(ev,function(e){e.preventDefault();z.classList.add('over');});});
    ['dragleave','drop'].forEach(function(ev){z.addEventListener(ev,function(e){e.preventDefault();z.classList.remove('over');});});
    z.addEventListener('drop',function(e){ if(e.dataTransfer.files.length){ inp.files=e.dataTransfer.files; show(); } });
    inp.addEventListener('change',show);
  });
  document.querySelectorAll('form[data-busy]').forEach(function(f){
    f.addEventListener('submit',function(){
      var b=f.querySelector('button[type=submit]'); if(b){b.disabled=true;b.dataset.t=b.textContent;b.textContent=f.dataset.busy;}
    });
  });
  // job progress polling: page stays usable, you can leave and come back
  var box=document.querySelector('[data-job]');
  if(box){
    var id=box.dataset.job, st=box.dataset.status, rowsUrl=box.dataset.rows;
    if(st==='queued'||st==='processing'){
      var tick=function(){
        fetch('/api/jobs/'+id).then(function(r){return r.json()}).then(function(j){
          var bar=box.querySelector('.bar>i'); if(bar) bar.style.width=(j.progress||0)+'%';
          var m=box.querySelector('[data-msg]'); if(m) m.textContent=j.message||'';
          var p=box.querySelector('[data-pct]'); if(p) p.textContent=(j.processed_items||0)+' / '+(j.total_items||0);
          var s=box.querySelector('[data-state]'); if(s) s.textContent=j.status.replace(/_/g,' ');
          if(rowsUrl){ fetch(rowsUrl).then(function(r){return r.text()}).then(function(h){var t=document.getElementById('live-rows'); if(t){t.innerHTML=h; applyFilter();}}); }
          if(j.status==='queued'||j.status==='processing'){ setTimeout(tick,1500); } else { location.reload(); }
        }).catch(function(){ setTimeout(tick,3000); });
      };
      setTimeout(tick,900);
    }
  }
  // client-side status filter + search for result tables
  var active='';
  function applyFilter(){
    var q=(document.getElementById('q')||{value:''}).value.toLowerCase();
    document.querySelectorAll('table[data-filterable] tbody tr').forEach(function(tr){
      var okS=!active||(tr.dataset.status||'').split('|').indexOf(active)>-1;
      var okQ=!q||tr.textContent.toLowerCase().indexOf(q)>-1;
      tr.style.display=(okS&&okQ)?'':'none';
    });
  }
  document.addEventListener('click',function(e){
    var c=e.target.closest('.chip[data-chip]');
    if(c){ var was=c.classList.contains('on'); document.querySelectorAll('.chip[data-chip]').forEach(function(x){x.classList.remove('on')});
      active=was?'':c.dataset.chip; if(!was) c.classList.add('on'); applyFilter(); }
    var t=e.target.closest('img.thumb');
    if(t){ var m=document.getElementById('modal'); m.querySelector('img').src=t.dataset.full; m.classList.add('open'); }
    if(e.target.closest('#modal')) document.getElementById('modal').classList.remove('open');
  });
  var q=document.getElementById('q'); if(q) q.addEventListener('input',applyFilter);
  document.addEventListener('keydown',function(e){ if(e.key==='Escape'){var m=document.getElementById('modal'); if(m) m.classList.remove('open');}});
  // digest preview
  var sel=document.getElementById('digest-pick');
  if(sel){ var load=function(){ var v=sel.value.split('||'); fetch(sel.dataset.url+'?fc='+encodeURIComponent(v[0])+'&day='+encodeURIComponent(v[1]))
      .then(function(r){return r.text()}).then(function(h){document.getElementById('digest-out').innerHTML=h;
        document.getElementById('send-fc').value=v[0]; document.getElementById('send-day').value=v[1];});};
    sel.addEventListener('change',load); load(); }
})();