export default function ({ data, setStateValue }) {
  const controller = new AbortController();
  let disposed = false;
  const timer = setTimeout(() => controller.abort(), 15000);
  const endpoint = new URL(data.endpoint, new URL('.', window.location.href));
  async function post(payload) {
    const response = await fetch(endpoint, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: controller.signal,
    });
    if (!response.ok) throw new Error('Browser session request failed');
    return response.json();
  }
  (async () => {
    try {
      const first = await post({ command: data.command });
      const checked = await post({ verification: first.verification });
      if (!disposed) {
        setStateValue('result', { command: data.command, receipt: checked.receipt });
      }
    } catch (_) {
      if (!disposed) setStateValue('result', { command: data.command, error: true });
    } finally { clearTimeout(timer); }
  })();
  return () => { disposed = true; clearTimeout(timer); controller.abort(); };
}
