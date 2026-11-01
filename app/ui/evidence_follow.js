// Trusted UI-only component. No business text or model output is evaluated.
export default function(component) {
  const {data, setTriggerValue} = component;
  if (!data.enabled) return;
  let disposed = false, region = null, timer = null, frames = 0;
  let armedUntil = 0, last = data.selected;
  const allowed = new Set(data.answers);
  const paint = id => {
    if (!region) return;
    for (const marker of region.querySelectorAll('[data-sc-answer]')) {
      const selected = marker.dataset.scAnswer === id;
      marker.classList.toggle('sc-evidence-selected', selected);
      marker.classList.toggle('sc-answer-reference', !selected);
      marker.textContent = marker.textContent.replace(/ · (Selected|已选中)$/, '')
        + (selected ? data.selectedLabel : '');
    }
  };
  const cancel = () => {
    clearTimeout(timer); timer = null; armedUntil = 0;
    if (region) region.dataset.scFollowIntent = '0';
  };
  const arm = () => {
    armedUntil = performance.now() + 1200;
    region.dataset.scFollowIntent = String(armedUntil);
  };
  const key = event => {
    if (event.target.closest('input,textarea,select,[contenteditable="true"],button')) return;
    if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)) arm();
  };
  const pointer = event => {
    const box = region.getBoundingClientRect();
    if (event.clientX >= box.right - 18) arm();
    else if (event.target.closest('button,input,textarea,select')) cancel();
  };
  const choose = () => {
    if (disposed || performance.now() > armedUntil || !region.isConnected) return;
    const viewport = region.getBoundingClientRect();
    const readingLine = viewport.top + Math.min(120, viewport.height * 0.25);
    let chosen = null, best = 0;
    for (const start of region.querySelectorAll('[data-sc-answer]')) {
      const id = start.dataset.scAnswer;
      if (!allowed.has(id)) continue;
      const end = document.getElementById('sc-answer-end-' + id);
      if (!end || !region.contains(end)) continue;
      const top = start.getBoundingClientRect().top;
      const bottom = end.getBoundingClientRect().bottom;
      const visible = Math.max(0, Math.min(bottom, viewport.bottom) - Math.max(top, viewport.top));
      if (!visible) continue;
      if (top <= readingLine && bottom > readingLine) { chosen = id; break; }
      if (visible > best) { chosen = id; best = visible; }
    }
    if (chosen && chosen !== last) {
      last = chosen;
      paint(chosen);
      setTriggerValue('visible_answer', {thread: data.thread, answer: chosen, token: data.token});
    }
  };
  const scroll = () => {
    if (performance.now() > armedUntil) return;
    if (timer !== null) return;
    timer = setTimeout(() => { timer = null; choose(); }, 70);
  };
  const bind = () => {
    if (disposed) return;
    region = document.querySelector('.st-key-conversation_scroll');
    if (!region) { if (++frames < 120) requestAnimationFrame(bind); return; }
    if (region.dataset.scFollowEpoch !== String(data.epoch)) {
      region.dataset.scFollowEpoch = String(data.epoch);
      region.dataset.scFollowIntent = '0';
    }
    paint(data.selected);
    armedUntil = Number(region.dataset.scFollowIntent || 0);
    if (performance.now() < armedUntil) scroll();
    region.addEventListener('wheel', arm, {passive:true});
    region.addEventListener('touchmove', arm, {passive:true});
    region.addEventListener('keydown', key);
    region.addEventListener('pointerdown', pointer);
    region.addEventListener('scroll', scroll, {passive:true});
  };
  bind();
  return () => {
    disposed = true;
    clearTimeout(timer); timer = null;
    if (!region) return;
    region.removeEventListener('wheel', arm);
    region.removeEventListener('touchmove', arm);
    region.removeEventListener('keydown', key);
    region.removeEventListener('pointerdown', pointer);
    region.removeEventListener('scroll', scroll);
  };
}
