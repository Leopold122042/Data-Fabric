// Event delegation also covers records, dialogs and disclosures created after load.
const reduced=matchMedia('(prefers-reduced-motion: reduce)'),active=new Map();
function settle(details,state){state.animation?.cancel();details.open=state.open;details.style.removeProperty('overflow');details.style.removeProperty('height');details.querySelector(':scope > summary')?.setAttribute('aria-expanded',String(state.open));active.delete(details);}
document.addEventListener('click',event=>{
 const summary=event.target.closest('summary');
 if(!summary||!summary.closest('.mf-app')||event.target.closest('a,button,input,select,textarea'))return;
 const details=summary.parentElement;if(details.tagName!=='DETAILS')return;
 if(reduced.matches||!details.animate)return;
 event.preventDefault();
 const previous=active.get(details),open=previous?!previous.open:!details.open;
 const start=details.getBoundingClientRect().height;previous?.animation.cancel();details.open=true;
 details.style.height='auto';const end=open?details.getBoundingClientRect().height:summary.getBoundingClientRect().height;
 details.style.overflow='hidden';summary.setAttribute('aria-expanded',String(open));
 const animation=details.animate([{height:start+'px'},{height:end+'px'}],{duration:200,easing:'cubic-bezier(.2,.8,.2,1)',fill:'both'}),state={open,animation};active.set(details,state);
 animation.onfinish=()=>{if(active.get(details)===state)settle(details,state);};
});
reduced.addEventListener('change',()=>{if(reduced.matches)for(const [details,state] of active)settle(details,state);});
document.addEventListener('toggle',event=>{const details=event.target;if(details.tagName==='DETAILS')details.querySelector(':scope > summary')?.setAttribute('aria-expanded',String(active.get(details)?.open??details.open));},true);
// Animate only user-requested tab/detail replacements, never background refreshes.
let requested=false;
document.addEventListener('click',event=>{if(event.target.closest('#subtabs button,#list button,.execution-tabs button')){requested=true;setTimeout(()=>{requested=false;},1000);}});
const watched=['record-preview','execution-preview','detail','page-body'];
const observer=new MutationObserver(records=>{
 if(!requested||reduced.matches)return;
 const targets=new Set(records.map(r=>r.target.nodeType===1?r.target:r.target.parentElement).map(el=>el.closest(watched.map(id=>'#'+id).join(','))).filter(Boolean));
 if(!targets.size)return;requested=false;
 for(const el of targets)if(!el.hidden){el.getAnimations().forEach(a=>a.cancel());el.animate([{opacity:.5,transform:'translateY(3px)'},{opacity:1,transform:'none'}],{duration:180,easing:'ease-out'});}
});
observer.observe(document.body,{childList:true,subtree:true,characterData:true});
window.addEventListener('pagehide',()=>{observer.disconnect();for(const [details,state] of active)settle(details,state);});
