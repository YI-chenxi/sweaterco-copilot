// Only viewport geometry is retained. No chat text, credentials or business state.
export default function({data}) {
  // The document outlives an isolated component renderer and its cleanup.
  const root=document.documentElement;
  const state={
    get pending(){try{return JSON.parse(root.dataset.scWorkflowViewport || 'null');}catch{return null;}},
    set pending(value){if(value)root.dataset.scWorkflowViewport=JSON.stringify(value);else delete root.dataset.scWorkflowViewport;},
  };
  let disposed = false, frame = 0;
  if (state.pending && state.pending.thread !== data.thread) state.pending = null;
  const capture = event => {
    const control = event.target.closest?.('.st-key-expand_workflows button,.st-key-collapse_workflows button');
    if (!control) {if(event.type==='pointerdown')state.pending=null;return;}
    if (state.pending) return;
    const region = document.querySelector('.st-key-conversation_scroll');
    const dock = region?.querySelector('.st-key-workflow_dock');
    if (!dock) return;
    const top = region.getBoundingClientRect().top;
    const dockBox = dock.getBoundingClientRect();
    const messages = [...region.querySelectorAll('[data-testid="stChatMessage"]')];
    const index = messages.findIndex(e => e.getBoundingClientRect().bottom > top + dockBox.height);
    state.pending = {
      thread:data.thread,
      expanded:!!control.closest('.st-key-expand_workflows'),
      top:region.scrollTop,
      stuck:dockBox.top <= top + 4,
      index,
      anchor:index < 0 ? null : messages[index].getBoundingClientRect().top - top,
    };
  };
  const cancel = event => {
    if (!event.target.closest?.('.st-key-expand_workflows,.st-key-collapse_workflows')) state.pending=null;
  };
  document.addEventListener('pointerdown',capture,true);
  document.addEventListener('click',capture,true);
  document.addEventListener('wheel',cancel,{passive:true,capture:true});
  document.addEventListener('touchmove',cancel,{passive:true,capture:true});
  let attempts=0, stable=0, previous=-1;
  const restore = () => {
    if (disposed || !state.pending || state.pending.thread !== data.thread) return;
    const pending=state.pending;
    // The old renderer captures the event; only the new renderer restores it.
    if (pending.expanded !== data.expanded) return;
    const region=document.querySelector('.st-key-conversation_scroll');
    const dock=region?.querySelector('.st-key-workflow_dock');
    const ready=dock && !!dock.querySelector('.st-key-collapse_workflows') === pending.expanded;
    if (ready) {
      let top=pending.top;
      const message=region.querySelectorAll('[data-testid="stChatMessage"]')[pending.index];
      if (pending.stuck && message && pending.anchor !== null) {
        top=region.scrollTop + message.getBoundingClientRect().top - region.getBoundingClientRect().top - pending.anchor;
      }
      top=Math.max(0,Math.min(top,region.scrollHeight-region.clientHeight));
      region.scrollTo({top,behavior:'instant'});
      stable=Math.abs(top-previous)<1 && !region.querySelector('[data-stale="true"]') ? stable+1 : 0;
      previous=top;
      if(stable>=12){state.pending=null;return;}
    }
    if(++attempts<120) frame=requestAnimationFrame(restore);
    else state.pending=null;
  };
  frame=requestAnimationFrame(restore);
  return ()=>{
    disposed=true;cancelAnimationFrame(frame);
    document.removeEventListener('pointerdown',capture,true);
    document.removeEventListener('click',capture,true);
    document.removeEventListener('wheel',cancel,true);
    document.removeEventListener('touchmove',cancel,true);
  };
}
