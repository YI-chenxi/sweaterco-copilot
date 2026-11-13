// Text is passed as data, never evaluated as HTML or code.
export default function({data, parentElement}) {
  const button = parentElement.querySelector('button');
  const status = parentElement.querySelector('[role="status"]');
  const words = data.language === 'zh'
    ? {copy:'复制完整分析', copied:'已复制', error:'复制失败，请选中文字复制'}
    : {copy:'Copy full analysis', copied:'Copied', error:'Could not copy. Select the text to copy.'};
  const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>';
  const check = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4 10-10"/></svg>';
  let timer;
  const label = text => {button.setAttribute('aria-label',text);};
  button.innerHTML=icon; label(words.copy);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data.text);
      button.innerHTML=check; label(words.copied); status.textContent=words.copied;
      clearTimeout(timer);
      timer=setTimeout(()=>{button.innerHTML=icon; label(words.copy); status.textContent='';},1800);
    } catch (_) {label(words.error); status.textContent=words.error;}
  };
  button.addEventListener('click',copy);
  return () => {clearTimeout(timer); button.removeEventListener('click',copy);};
}
