// Only routes, record IDs and viewport geometry are stored; never message text or identity.
export default function({data}) {
  const root=document.documentElement;
  let state;
  try{state=JSON.parse(root.dataset.scTaskViewports || 'null');}catch{state=null;}
  if(!state || state.scope!==data.scope)state={scope:data.scope,positions:{},restored:null};
  const save=()=>{root.dataset.scTaskViewports=JSON.stringify(state);};save();
  let region,frame=0,disposed=false,attempts=0,stable=0,previous=-1;
  const target=data.target || {};
  const remember=()=>{
    if(region?.isConnected && (!target.nonce || state.restored===target.nonce)){
      state.positions[data.route]=region.scrollTop;save();
    }
  };
  const restore=()=>{
    if(disposed)return;
    region=document.querySelector('.st-key-conversation_scroll');
    if(!region){if(++attempts<120)frame=requestAnimationFrame(restore);return;}
    if(!target.nonce || state.restored===target.nonce)return;
    const selector=data.page==='watches' ? '.st-key-pa_card_watch_' : '.st-key-pa_card_schedule_';
    const card=target.id ? region.querySelector(selector+CSS.escape(target.id)) : null;
    if(target.id && !card){if(++attempts<120)frame=requestAnimationFrame(restore);return;}
    let top=card ? region.scrollTop+card.getBoundingClientRect().top-region.getBoundingClientRect().top-16
                 : target.top ?? state.positions[data.route] ?? 0;
    top=Math.max(0,Math.min(top,region.scrollHeight-region.clientHeight));
    region.scrollTo({top,behavior:'instant'});
    stable=Math.abs(previous-top)<1 && !region.querySelector('[data-stale="true"]') ? stable+1 : 0;
    previous=top;
    if(stable>=10){
      state.restored=target.nonce;save();
      if(card){card.classList.add('pa-result-highlight');setTimeout(()=>card.classList.remove('pa-result-highlight'),2400);}
      return;
    }
    if(++attempts<120)frame=requestAnimationFrame(restore);
  };
  const scroll=event=>{if(event.target===region)remember();};
  const pointer=()=>remember();
  document.addEventListener('scroll',scroll,true);
  document.addEventListener('pointerdown',pointer,true);
  frame=requestAnimationFrame(restore);
  return ()=>{disposed=true;cancelAnimationFrame(frame);document.removeEventListener('scroll',scroll,true);document.removeEventListener('pointerdown',pointer,true);};
}
