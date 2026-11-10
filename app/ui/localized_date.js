// Dates stay ISO strings on the bridge; all calendar labels use the page locale.
export default function({parentElement, data, setStateValue}) {
  const root = parentElement.querySelector('.date-field');
  const zh = data.language === 'zh', locale = zh ? 'zh-CN' : 'en-GB';
  const parse = iso => new Date(iso + 'T12:00:00');
  const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const selected = parse(data.value);
  let year = selected.getFullYear(), month = selected.getMonth();
  const label = document.createElement('label');
  label.className = 'date-label'; label.textContent = data.label;
  const open = document.createElement('button');
  open.type = 'button'; open.className = 'date-open'; open.setAttribute('aria-label', data.label);
  open.setAttribute('aria-haspopup','dialog'); open.setAttribute('aria-expanded','false');
  const value = document.createElement('span');
  value.textContent = new Intl.DateTimeFormat(locale,{year:'numeric',month:'2-digit',day:'2-digit'}).format(selected);
  open.append(value);
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const path = document.createElementNS(svg.namespaceURI,'path');
  path.setAttribute('d','M5 5h14v15H5z M8 3v4 M16 3v4 M5 10h14');svg.append(path);open.append(svg);
  const panel = document.createElement('div'); panel.className='calendar';
  panel.setAttribute('popover','auto');panel.setAttribute('role','dialog');
  panel.setAttribute('aria-label',zh?'选择日期':'Choose a date');
  const place = () => {
    const r=open.getBoundingClientRect();
    panel.style.left=Math.max(10,Math.min(r.left,window.innerWidth-panel.offsetWidth-10))+'px';
    const top = r.bottom+6+panel.offsetHeight < window.innerHeight-10 ? r.bottom+6 : r.top-panel.offsetHeight-6;
    panel.style.top=Math.max(10,top)+'px';
  };
  const choose = date => {panel.hidePopover();open.focus();setStateValue('value',iso(date));};
  const button = (text, name, click) => {
    const b=document.createElement('button');b.type='button';b.textContent=text;
    b.setAttribute('aria-label',name);b.addEventListener('click',click);return b;
  };
  const render = () => {
    panel.replaceChildren();
    const header=document.createElement('header');
    header.append(button('‹',zh?'上个月':'Previous month',()=>{month--;if(month<0){month=11;year--;}render();}));
    const months=document.createElement('select');months.setAttribute('aria-label',zh?'月份':'Month');
    for(let m=0;m<12;m++){
      const o=document.createElement('option');o.value=String(m);
      o.textContent=new Intl.DateTimeFormat(locale,{month:'long'}).format(new Date(2026,m,1));
      o.selected=m===month;months.append(o);
    }
    months.onchange=()=>{month=Number(months.value);render();};header.append(months);
    const years=document.createElement('select');years.setAttribute('aria-label',zh?'年份':'Year');
    for(let y=year-100;y<=year+100;y++){
      if(y<1||y>9999)continue;
      const o=document.createElement('option');o.value=String(y);o.textContent=zh?`${y}年`:String(y);
      o.selected=y===year;years.append(o);
    }
    years.onchange=()=>{year=Number(years.value);render();};header.append(years);
    header.append(button('›',zh?'下个月':'Next month',()=>{month++;if(month>11){month=0;year++;}render();}));
    panel.append(header);
    const grid=document.createElement('div');grid.className='grid';grid.setAttribute('role','grid');
    grid.setAttribute('aria-label',new Intl.DateTimeFormat(locale,{month:'long',year:'numeric'}).format(new Date(year,month,1)));
    for(let i=0;i<7;i++){
      const day=document.createElement('span');day.className='weekday';
      day.textContent=new Intl.DateTimeFormat(locale,{weekday:'short'}).format(new Date(2026,0,5+i));
      grid.append(day);
    }
    const offset=(new Date(year,month,1).getDay()+6)%7;
    for(let i=0;i<offset;i++)grid.append(document.createElement('span'));
    const count=new Date(year,month+1,0).getDate();
    const today=iso(new Date());
    for(let n=1;n<=count;n++){
      const day=new Date(year,month,n,12), key=iso(day);
      const b=button(String(n),new Intl.DateTimeFormat(locale,{dateStyle:'full'}).format(day),()=>choose(day));
      b.className='day';b.dataset.date=key;
      b.setAttribute('aria-selected',String(key===data.value));
      if(key===today)b.classList.add('today');
      b.addEventListener('keydown',event=>{
        const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];
        if(delta===undefined)return;
        event.preventDefault();const next=new Date(year,month,n+delta,12);
        year=next.getFullYear();month=next.getMonth();render();
        panel.querySelector(`[data-date="${iso(next)}"]`)?.focus();
      });grid.append(b);
    }
    panel.append(grid);
    const footer=document.createElement('footer');footer.append(button(zh?'今天':'Today',zh?'选择今天':'Select today',()=>choose(new Date())));
    panel.append(footer);
    if(panel.matches(':popover-open'))place();
  };
  render();root.replaceChildren(label,open,panel);
  open.onclick=()=>{panel.togglePopover();if(panel.matches(':popover-open')){place();panel.querySelector('[aria-selected="true"]')?.focus();}};
  label.onclick=()=>open.click();
  panel.addEventListener('toggle',()=>open.setAttribute('aria-expanded',String(panel.matches(':popover-open'))));
  const close = ()=>{if(panel.matches(':popover-open'))panel.hidePopover();};
  document.addEventListener('scroll',close,true);window.addEventListener('resize',close);
  return ()=>{document.removeEventListener('scroll',close,true);window.removeEventListener('resize',close);panel.remove();};
}
