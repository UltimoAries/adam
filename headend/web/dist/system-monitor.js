'use strict';
(() => {
  const get=id=>document.getElementById(id),dashboard=get('dashboard');
  let timer=null,request=null,generation=0;
  const active=()=>!dashboard.hidden&&!document.hidden;
  const bytes=value=>`${(value/1073741824).toFixed(1)} GiB`;
  function metric(name,value,detail){
    const valid=Number.isFinite(value)&&value>=0&&value<=100,bar=get(`resource${name}Bar`);
    get(`resource${name}`).textContent=valid?`${value.toFixed(1)}%`:'Unavailable';
    bar.hidden=!valid;bar.value=valid?value:0;
    bar.classList.toggle('resource-high',valid&&value>=90);
    if(detail)get(`resource${name}Detail`).textContent=detail;
  }
  function clear(){
    metric('Cpu',null);metric('Memory',null,'Host physical memory');
    metric('Storage',null,'Drive containing ADAM data');
  }
  async function refresh(){
    if(!active()||request)return;
    const current=generation,controller=new AbortController();request=controller;
    const timeout=setTimeout(()=>controller.abort(),4000);
    try{
      const response=await fetch('/api/system/resources',{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw Error('Unavailable');
      const data=await response.json();
      if(current!==generation||!active())return;
      metric('Cpu',data.cpu_percent);
      metric('Memory',data.memory?.percent,data.memory?`${bytes(data.memory.used_bytes)} used / ${bytes(data.memory.total_bytes)}`:'Host memory unavailable');
      metric('Storage',data.storage?.percent,data.storage?`${bytes(data.storage.available_bytes)} free / ${bytes(data.storage.total_bytes)}`:'Data drive unavailable');
      get('resourceStatus').textContent=`Updated ${new Date(data.sampled_at*1000).toLocaleTimeString()}`;
    }catch{
      if(current===generation&&active()){
        clear();get('resourceStatus').textContent='Readings unavailable · retrying';
      }
    }finally{
      clearTimeout(timeout);
      if(request===controller)request=null;
      if(active())timer=setTimeout(refresh,5000);
    }
  }
  function sync(){
    generation++;clearTimeout(timer);timer=null;
    if(!active()){
      request?.abort();clear();get('resourceStatus').textContent='Waiting for readings';
    }else if(!request)refresh();
  }
  new MutationObserver(sync).observe(dashboard,{attributes:true,attributeFilter:['hidden']});
  document.addEventListener('visibilitychange',sync);
  sync();
})();
