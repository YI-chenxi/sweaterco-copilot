// Trusted UI code only. Fits each opening to its content; geometry stays in memory.
(() => {
  const language = __SC_DIALOG_LANGUAGE__;
  const key = '__sweatercoWorkflowDialogV4';
  if (window[key]) { window[key].refresh(language); return; }
  let locale = language, active = null, queued = false;
  const words = () => locale === 'zh' ? {
    move: '拖动标题移动；也可聚焦标题后使用方向键', resize: '调整弹窗大小',
  } : {
    move: 'Drag to move; focus the title and use arrow keys', resize: 'Resize dialog',
  };
  const viewport = () => ({width: window.innerWidth, height: window.innerHeight});
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
  const bounded = box => {
    const v = viewport(), edge = 12;
    const width = clamp(box.width, Math.min(360, v.width - 2 * edge), v.width - 2 * edge);
    const height = clamp(box.height, Math.min(180, v.height - 2 * edge), v.height - 2 * edge);
    return {width, height, left: clamp(box.left, edge, Math.max(edge, v.width - width - edge)),
      top: clamp(box.top, edge, Math.max(edge, v.height - height - edge))};
  };
  const centered = (height = 180) => {
    const v = viewport();
    const width = Math.min(720, v.width * .88);
    return bounded({width, height, left: (v.width-width)/2, top: (v.height-height)/2});
  };
  const apply = (state, box) => {
    state.box = bounded(box);
    for (const [name, value] of Object.entries(state.box)) {
      state.panel.style.setProperty(name, value + 'px', 'important');
    }
  };
  const fit = state => {
    if (!state.autoSize || state.drag || !state.dialog.isConnected) return;
    const body = [...state.dialog.children].find(node => node.tagName === 'DIV');
    if (!body) return;
    // Measure normal document flow, rather than the stretched viewport-height body.
    state.panel.style.setProperty('height', 'auto', 'important');
    state.dialog.style.setProperty('height', 'auto', 'important');
    body.style.setProperty('flex', '0 0 auto', 'important');
    const height = Math.ceil(state.dialog.getBoundingClientRect().height) + 12;
    body.style.removeProperty('flex');
    state.dialog.style.removeProperty('height');
    const box = {...state.box, height};
    if (!state.moved) box.top = (viewport().height-height)/2;
    apply(state, box);
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
      originalPanelStyle:panel.getAttribute('style'),originalDialogStyle:dialog.getAttribute('style'),
      box: centered(), drag: null, moved: false, autoSize: true};
    const begin = (event, edge) => {
      if (event.button !== 0 || event.target.closest('button,input,select,textarea')) return;
      const close = dialog.querySelector(':scope > button');
      if (close) {
        const r = close.getBoundingClientRect(), safe = 8;
        if (event.clientX >= r.left-safe && event.clientX <= r.right+safe &&
            event.clientY >= r.top-safe && event.clientY <= r.bottom+safe) return;
      }
      event.preventDefault(); event.stopPropagation();
      if (edge === 'move') state.moved = true;
      else state.autoSize = false;
      state.drag = {edge, x:event.clientX, y:event.clientY, box:{...state.box}, target:event.currentTarget};
      event.currentTarget.setPointerCapture(event.pointerId);
      panel.classList.add('sc-dialog-adjusting');
    };
    // Delegate from the live dialog: Streamlit can replace the heading after a rerun.
    dialog.addEventListener('pointerdown', event => {
      if (event.target.closest('.sc-dialog-resize')) return;
      const currentTitle = dialog.querySelector('h2');
      if (!currentTitle) return;
      const r = dialog.getBoundingClientRect(), heading = currentTitle.getBoundingClientRect();
      if (event.clientY >= r.top + 7 && event.clientY <= heading.bottom + 4) begin(event, 'move');
    }, {...options, capture: true});
    for (const edge of ['n','ne','e','se','s','sw','w','nw']) {
      const handle = document.createElement('span');
      handle.className = 'sc-dialog-resize sc-dialog-resize-' + edge;
      handle.dataset.edge = edge; handle.tabIndex = 0; handle.setAttribute('role', 'separator');
      handle.addEventListener('pointerdown', e => begin(e, edge), options);
      handle.addEventListener('keydown', e => {
        if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
        e.preventDefault(); e.stopPropagation();
        state.autoSize = false;
        const step = e.shiftKey ? 48 : 16, box = {...state.box};
        if (e.key === 'ArrowLeft') box.width -= step;
        if (e.key === 'ArrowRight') box.width += step;
        if (e.key === 'ArrowUp') box.height -= step;
        if (e.key === 'ArrowDown') box.height += step;
        apply(state, box);
      }, options);
      dialog.append(handle); state.handles.push(handle);
    }
    dialog.addEventListener('keydown', e => {
      if (!e.target.closest('h2')) return;
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
      e.preventDefault(); e.stopPropagation();
      state.moved = true;
      const step = e.shiftKey ? 48 : 16, box = {...state.box};
      if (e.key === 'ArrowLeft') box.left -= step;
      if (e.key === 'ArrowRight') box.left += step;
      if (e.key === 'ArrowUp') box.top -= step;
      if (e.key === 'ArrowDown') box.top += step;
      apply(state, box);
    }, options);
    window.addEventListener('pointermove', event => {
      if (!state.drag) return;
      const {edge, x, y, box} = state.drag;
      const dx = event.clientX - x, dy = event.clientY - y, v = viewport();
      const next = {...box};
      if (edge === 'move') { next.left += dx; next.top += dy; }
      else {
        if (edge.includes('e')) next.width = clamp(box.width+dx, Math.min(360,v.width-24), v.width-box.left-12);
        if (edge.includes('s')) next.height = clamp(box.height+dy, Math.min(180,v.height-24), v.height-box.top-12);
        if (edge.includes('w')) {
          next.left = clamp(box.left+dx,12,box.left+box.width-Math.min(360,v.width-24));
          next.width = box.width + box.left - next.left;
        }
        if (edge.includes('n')) {
          next.top = clamp(box.top+dy,12,box.top+box.height-Math.min(180,v.height-24));
          next.height = box.height + box.top - next.top;
        }
      }
      apply(state, next);
    }, options);
    const finish = () => {
      if (!state.drag) return;
      state.drag = null; panel.classList.remove('sc-dialog-adjusting');
    };
    window.addEventListener('pointerup', finish, options);
    window.addEventListener('pointercancel', finish, options);
    window.addEventListener('blur', finish, options);
    setLabels(state); apply(state, state.box); fit(state);
    const content = dialog.querySelector(':scope > div > [data-testid="stVerticalBlock"]');
    if (content) {
      state.observer = new ResizeObserver(() => requestAnimationFrame(() => fit(state)));
      state.observer.observe(content);
    }
    return state;
  };
  const scan = () => {
    queued = false;
    if (active && (!active.dialog.isConnected || !active.dialog.querySelector('.sc-resizable-dialog-marker'))) {
      active.controller.abort();active.handles.forEach(h=>h.remove());
      active.observer?.disconnect();
      active.panel.classList.remove('sc-workflow-window');active.dialog.classList.remove('sc-workflow-dialog');
      for(const [node,style] of [[active.panel,active.originalPanelStyle],[active.dialog,active.originalDialogStyle]]){
        if(style===null)node.removeAttribute('style');else node.setAttribute('style',style);
      }
      active=null;
    }
    const dialog = document.querySelector('[data-testid="stDialog"] [role="dialog"]:has(.sc-resizable-dialog-marker)');
    if (!active && dialog) active = bind(dialog);
    else if (active) fit(active);
  };
  const schedule = () => {if (!queued) {queued = true; requestAnimationFrame(scan);}};
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, {childList:true, subtree:true});
  window.addEventListener('resize', () => {if (active) {apply(active, active.box); fit(active);}});
  window[key] = {refresh: next => {locale = next; if (active) setLabels(active); schedule();}};
  schedule();
})();
