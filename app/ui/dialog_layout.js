// Trusted UI code only. Stores geometry, never request text or business results.
(() => {
  const language = __SC_DIALOG_LANGUAGE__;
  const key = '__sweatercoWorkflowDialogV2';
  if (window[key]) { window[key].refresh(language); return; }
  const storageKey = 'sweaterco.workflow-dialog.geometry.v1';
  let locale = language, active = null, queued = false, last = null;
  const words = () => locale === 'zh' ? {
    move: '拖动标题移动；也可聚焦标题后使用方向键', resize: '调整弹窗大小',
  } : {
    move: 'Drag to move; focus the title and use arrow keys', resize: 'Resize dialog',
  };
  const viewport = () => ({width: window.innerWidth, height: window.innerHeight});
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
  const validBox = box => box && ['left','top','width','height'].every(k => Number.isFinite(box[k]));
  const bounded = box => {
    const v = viewport(), edge = 12;
    const width = clamp(box.width, Math.min(360, v.width - 2 * edge), v.width - 2 * edge);
    const height = clamp(box.height, Math.min(300, v.height - 2 * edge), v.height - 2 * edge);
    return {width, height, left: clamp(box.left, edge, Math.max(edge, v.width - width - edge)),
      top: clamp(box.top, edge, Math.max(edge, v.height - height - edge))};
  };
  const centered = (width = 720, height = 560) => {
    const v = viewport();
    return bounded({width, height, left: (v.width-width)/2, top: (v.height-height)/2});
  };
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    if (validBox(saved?.box)) last = saved;
  } catch (_) { /* Geometry can still work when browser storage is unavailable. */ }
  const save = state => {
    last = {box: {...state.box}};
    try { sessionStorage.setItem(storageKey, JSON.stringify(last)); } catch (_) {}
  };
  const apply = (state, box, persist = false) => {
    state.box = bounded(box);
    for (const [name, value] of Object.entries(state.box)) {
      state.panel.style.setProperty(name, value + 'px', 'important');
    }
    if (persist) save(state);
  };
  const setLabels = state => {
    const w = words();
    state.title.removeAttribute('title');
    state.title.setAttribute('aria-description', w.move);
    state.handles.forEach(handle => handle.setAttribute('aria-label', w.resize + ' (' + handle.dataset.edge + ')'));
  };
  const bind = dialog => {
    const panel = dialog.parentElement;
    const title = document.getElementById(dialog.getAttribute('aria-labelledby')) || dialog.querySelector('h2');
    if (!title) return null;
    const controller = new AbortController(), options = {signal: controller.signal};
    panel.classList.add('sc-workflow-window');
    dialog.classList.add('sc-workflow-dialog');
    title.classList.add('sc-dialog-drag-title'); title.tabIndex = 0;
    const state = {panel, dialog, title, handles: [], controller,
      box: last?.box || centered(), drag: null};
    const begin = (event, edge) => {
      if (event.button !== 0 || event.target.closest('button,input,select,textarea')) return;
      const close = dialog.querySelector(':scope > button');
      if (close) {
        const r = close.getBoundingClientRect(), safe = 8;
        if (event.clientX >= r.left-safe && event.clientX <= r.right+safe &&
            event.clientY >= r.top-safe && event.clientY <= r.bottom+safe) return;
      }
      event.preventDefault(); event.stopPropagation();
      state.drag = {edge, x:event.clientX, y:event.clientY, box:{...state.box}, target:event.currentTarget};
      event.currentTarget.setPointerCapture(event.pointerId);
      panel.classList.add('sc-dialog-adjusting');
    };
    title.addEventListener('pointerdown', e => begin(e, 'move'), options);
    for (const edge of ['n','ne','e','se','s','sw','w','nw']) {
      const handle = document.createElement('span');
      handle.className = 'sc-dialog-resize sc-dialog-resize-' + edge;
      handle.dataset.edge = edge; handle.tabIndex = 0; handle.setAttribute('role', 'separator');
      handle.addEventListener('pointerdown', e => begin(e, edge), options);
      handle.addEventListener('keydown', e => {
        if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
        e.preventDefault(); e.stopPropagation();
        const step = e.shiftKey ? 48 : 16, box = {...state.box};
        if (e.key === 'ArrowLeft') box.width -= step;
        if (e.key === 'ArrowRight') box.width += step;
        if (e.key === 'ArrowUp') box.height -= step;
        if (e.key === 'ArrowDown') box.height += step;
        apply(state, box, true);
      }, options);
      dialog.append(handle); state.handles.push(handle);
    }
    title.addEventListener('keydown', e => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
      e.preventDefault(); e.stopPropagation();
      const step = e.shiftKey ? 48 : 16, box = {...state.box};
      if (e.key === 'ArrowLeft') box.left -= step;
      if (e.key === 'ArrowRight') box.left += step;
      if (e.key === 'ArrowUp') box.top -= step;
      if (e.key === 'ArrowDown') box.top += step;
      apply(state, box, true);
    }, options);
    window.addEventListener('pointermove', event => {
      if (!state.drag) return;
      const {edge, x, y, box} = state.drag;
      const dx = event.clientX - x, dy = event.clientY - y, v = viewport();
      const next = {...box};
      if (edge === 'move') { next.left += dx; next.top += dy; }
      else {
        if (edge.includes('e')) next.width = clamp(box.width+dx, Math.min(360,v.width-24), v.width-box.left-12);
        if (edge.includes('s')) next.height = clamp(box.height+dy, Math.min(300,v.height-24), v.height-box.top-12);
        if (edge.includes('w')) {
          next.left = clamp(box.left+dx,12,box.left+box.width-Math.min(360,v.width-24));
          next.width = box.width + box.left - next.left;
        }
        if (edge.includes('n')) {
          next.top = clamp(box.top+dy,12,box.top+box.height-Math.min(300,v.height-24));
          next.height = box.height + box.top - next.top;
        }
      }
      apply(state, next);
    }, options);
    const finish = () => {
      if (!state.drag) return;
      state.drag = null; panel.classList.remove('sc-dialog-adjusting'); save(state);
    };
    window.addEventListener('pointerup', finish, options);
    window.addEventListener('pointercancel', finish, options);
    window.addEventListener('blur', finish, options);
    setLabels(state); apply(state, state.box);
    return state;
  };
  const scan = () => {
    queued = false;
    if (active && !active.dialog.isConnected) {active.controller.abort(); active = null;}
    const dialog = document.querySelector('[data-testid="stDialog"] [role="dialog"]');
    if (!active && dialog) active = bind(dialog);
  };
  const schedule = () => {if (!queued) {queued = true; requestAnimationFrame(scan);}};
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, {childList:true, subtree:true});
  window.addEventListener('resize', () => {if (active) apply(active, active.box, true);});
  window[key] = {refresh: next => {locale = next; if (active) setLabels(active); schedule();}};
  schedule();
})();
