export default function ({ data, setStateValue }) {
  const controller = new AbortController();
  const selector = '.st-key-' + CSS.escape(data.widgetKey) + ' textarea';
  document.addEventListener('input', event => {
    if (!(event.target instanceof HTMLTextAreaElement) || !event.target.matches(selector)) return;
    setStateValue('draft', {
      thread: data.thread, token: data.token,
      sequence: performance.now(), text: event.target.value,
    });
  }, { capture: true, signal: controller.signal });
  return () => controller.abort();
}
