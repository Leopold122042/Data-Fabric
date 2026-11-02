import {icon} from './shared/icons.js';
import {distribution,format,stagePresentation,authorizationSummary} from './dashboard-model.js';

export const node=(tag,text='',cls='')=>{const el=document.createElement(tag);el.textContent=text;el.className=cls;return el;};
export const link=(text,url,cls='')=>{const el=node('a',text,cls);el.href=url;return el;};
export function iconBox(name,tone='blue'){const el=node('span','','dashboard-icon');el.dataset.tone=tone;el.append(icon(name));return el;}
export function action(label,url,iconName='arrow'){
  const el=url?link('',url,'dashboard-action'):node('button','','dashboard-action');
  if(!url)el.type='button';el.append(node('span',label.replace(/\s*→$/,'')),icon(iconName));return el;
}
export function badge(text,tone='slate',symbol){const el=node('span','','dashboard-badge');el.dataset.tone=tone;if(symbol)el.append(icon(symbol));el.append(node('span',text));return el;}
export function panel(title,url,label='查看全部',symbol='grid'){
  const el=node('section','','dashboard-panel'),head=node('header'),heading=node('h2');
  heading.append(iconBox(symbol),node('span',title));head.append(heading);if(url)head.append(action(label,url));el.append(head);return el;
}
export function empty(parent,text='暂无记录',symbol='clipboard'){
  const el=node('div','','dashboard-empty');el.append(iconBox(symbol,'slate'),node('p',text));parent.append(el);return el;
}
export function row(parent,title,detail,url,label='查看',symbol='file',tone='blue'){
  const el=node('div','','dashboard-row'),body=node('div','','dashboard-row-body'),heading=node('b',title);
  heading.title=title;const desc=node('p',detail,'dashboard-muted');desc.title=detail;
  body.append(heading,desc);el.append(iconBox(symbol,tone),body);if(url)el.append(action(label,url));parent.append(el);return el;
}
export function metric(parent,label,value,url,note='当前记录',symbol='chart',tone='blue'){
  const el=link('',url,'dashboard-metric'),body=node('div','','dashboard-metric-body');el.dataset.tone=tone;
  body.append(node('span',label),node('strong',format(value)),node('small',note));el.append(iconBox(symbol,tone),body,icon('arrow','dashboard-metric-arrow'));parent.append(el);
}

// Values stay visible. The tooltip supplements the chart for pointer and keyboard users.
export function bars(parent,values,labels={},options={}){
  const rows=distribution(values),box=node('div','','dashboard-bars');box.setAttribute('aria-label',options.label||'数量分布');
  const total=rows.reduce((sum,r)=>sum+r.value,0);
  rows.forEach(r=>{
    const name=labels[r.label]||r.label,share=total?`${(r.value/total*100).toFixed(1)}%`:'0%',item=node('button','','dashboard-bar'),top=node('span','','dashboard-bar-label'),track=node('span','','dashboard-track'),fill=node('span','','dashboard-fill');
    item.type='button';item.dataset.tone=options.tone||options.tones?.[r.label]||'slate';
    const nameEl=node('span',name,'dashboard-bar-name');
    const value=node('span','','dashboard-bar-value');value.append(node('b',format(r.value)),node('small',share));top.append(nameEl,value);fill.style.width=r.width+'%';track.append(fill);
    const description=`${name}：${format(r.value)} ${options.unit||'个对象'}，占当前分布 ${share}`;
    item.setAttribute('aria-label',description+'；点击查看摘要');
    const tooltip=node('span',description,'dashboard-chart-tooltip');tooltip.setAttribute('aria-hidden','true');
    item.append(top,track,tooltip);item.onclick=()=>options.onDetail?.(name,r.value,share);
    item.onkeydown=e=>{if(e.key==='Escape')item.dataset.dismissed='true';};
    item.onpointerleave=item.onblur=()=>delete item.dataset.dismissed;
    box.append(item);
  });
  parent.append(box);if(!rows.length)empty(box,'暂无可统计的分布','chart');return box;
}
export function stages(items){
  const list=node('ol','','dashboard-stages');list.setAttribute('aria-label','申请到反馈的处理进展');
  items.forEach((stage,index)=>{
    const presentation=stagePresentation(stage.state),item=node('li'),point=node('span','','dashboard-stage-point');
    item.dataset.state=presentation.state;item.title=stage.detail||`${stage.label} · ${presentation.label}`;
    if(presentation.state==='current')item.setAttribute('aria-current','step');
    if(presentation.icon)point.append(icon(presentation.icon));else point.textContent=index+1;
    const label=node('span',stage.label,'dashboard-stage-label');label.append(node('span','，'+presentation.label,'dashboard-sr-only'));item.append(point,label);list.append(item);
  });return list;
}
export function authorizationChart(auth){
  const summary=authorizationSummary(auth),chart=node('div','','dashboard-donut-wrap'),ring=node('div','','dashboard-donut');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 140 140');svg.setAttribute('aria-hidden','true');
  for(const [cls,ratio,offset] of [['ring-track',1,0],['ring-pass',summary.rate??0,0],['ring-reject',summary.total?summary.rejected/summary.total:0,summary.rate??0]]){
    const c=document.createElementNS(svg.namespaceURI,'circle');for(const [k,v]of Object.entries({cx:70,cy:70,r:56,fill:'none','stroke-width':12,pathLength:100,class:cls}))c.setAttribute(k,v);
    if(cls!=='ring-track'){c.setAttribute('stroke-dasharray',`${ratio*100} ${100-ratio*100}`);c.setAttribute('stroke-dashoffset',-offset*100);}svg.append(c);
  }
  const center=node('div','','dashboard-donut-center');center.append(node('strong',summary.rate==null?'—':(summary.rate*100).toFixed(1)+'%'),node('span','核验通过率'));
  ring.append(svg,center);ring.setAttribute('role','img');ring.setAttribute('aria-label',`已保存 ${format(summary.total)} 次核验，通过 ${format(summary.passed)} 次，拒绝 ${format(summary.rejected)} 次`);
  const legend=node('div','','dashboard-ring-legend');legend.append(badge('通过 '+format(summary.passed),'teal','check'),badge('拒绝 '+format(summary.rejected),'amber','alert'));chart.append(ring,legend);return chart;
}
