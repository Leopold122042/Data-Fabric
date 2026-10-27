import {actionAllowed,canRead,level,pageResource,roleNames} from './access-model.js';
export {actionAllowed,canRead,level,pageResource,roleNames};
let snapshot={principal:null,resources:{},actions:{}},pending;
export const currentAccess=()=>snapshot;
export function role(){const value=globalThis.localStorage?.getItem('mf-demo-role');return Object.hasOwn(roleNames,value)?value:'platform';}
export function headers(extra={}){return {'X-Demo-Role':role(),...extra};}
export function ready(){
 if(!pending)pending=(async()=>{const response=await fetch('/api/access',{headers:headers(),cache:'no-store'});const result=await response.json();if(!response.ok||!result.principal||!result.resources||!result.actions)throw Error(result.error||'无法加载身份权限');snapshot=result;return result;})();
 return pending;
}
export function allowed(key,record=null){return actionAllowed(snapshot,key,record);}
export function readable(resource){return canRead(snapshot,resource);}
export function bindRole(){const select=document.querySelector('#role');if(!select)return;select.value=role();for(const option of select.options)if(roleNames[option.value])option.textContent=roleNames[option.value];const label=select.closest('label');if(label)for(const child of label.childNodes)if(child.nodeType===Node.TEXT_NODE&&child.textContent.includes('身份'))child.textContent='当前身份 ';const identity=document.querySelector('#identity');if(identity)identity.textContent='本机身份 · 本地权限已启用';select.onchange=()=>{localStorage.setItem('mf-demo-role',select.value);location.reload();};}
export function showAccess(resource){
 const content=document.querySelector('.mf-content');if(!content)return;
 let note=document.querySelector('#access-status');if(!note){note=document.createElement('p');note.id='access-status';note.className='access-status';content.prepend(note);}
 const mode=level(snapshot,resource);note.dataset.access=mode;
 note.textContent=(roleNames[snapshot.principal?.role]||'当前身份')+' · '+({write:'可查看、可操作（按记录归属与授权范围）',read:'只读 · 可查看，不可修改',none:'无查看权限'}[mode]);
 const identity=document.querySelector('#identity');if(identity)identity.textContent='本机身份 · 本地权限已启用';
}
export function denyPage(message='当前身份不能查看此目录，请从左侧选择可访问的目录。'){
 const content=document.querySelector('.mf-content');if(!content)return false;
 content.replaceChildren();const box=document.createElement('section');box.className='access-denied mf-panel';const title=document.createElement('h1');title.textContent='无查看权限';const description=document.createElement('p');description.textContent=message;box.append(title,description);content.append(box);return false;
}
export async function enterPage(resource=pageResource(location.href)){
 bindRole();try{await ready();}catch(error){return denyPage(error.message+'，已隐藏数据与操作入口。');}
 if(resource&&!readable(resource))return denyPage();
 if(resource)showAccess(resource);return true;
}
