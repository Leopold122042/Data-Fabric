import {loginURL} from './workspaces.js?v=0.1.45';
import {actionAllowed,canRead,level,pageResource,roleNames} from './access-model.js?v=0.1.45';
export {actionAllowed,canRead,level,pageResource,roleNames};
let snapshot={principal:null,resources:{},actions:{}},pending;
export const currentAccess=()=>snapshot;
export function role(){return snapshot.principal?.role||'requester';}
export function headers(extra={}){return {...extra};}
function redirectLogin(){
 if(location.pathname.endsWith('/login')||location.pathname==='/static/portal/login.html')return;
 location.replace(loginURL(location));
}
export function ready(){
 if(!pending)pending=(async()=>{
  const response=await fetch('/api/access',{credentials:'same-origin',cache:'no-store'});
  let result={};try{result=await response.json();}catch{}
  if(response.status===401){redirectLogin();throw Error('请先登录平台');}
  if(!response.ok||!result.principal||!result.resources||!result.actions)throw Error(result.error||'无法加载账户权限');
  snapshot=result;return result;
 })();
 return pending;
}
export function allowed(key,record=null){return actionAllowed(snapshot,key,record);}
export function readable(resource){return canRead(snapshot,resource);}
export function bindRole(){
 document.querySelector('#role')?.closest('label')?.remove();
 const identity=document.querySelector('#identity');
 if(identity&&snapshot.principal){identity.textContent=`${snapshot.principal.display_name||snapshot.principal.username} · ${snapshot.principal.level}级`;}
}
export function showAccess(resource){
 const content=document.querySelector('.mf-content');if(!content)return;
 content.dataset.access=level(snapshot,resource);
 document.querySelector('#access-status')?.remove();
 const identity=document.querySelector('#identity');
 if(identity&&snapshot.principal)identity.textContent=`${snapshot.principal.display_name||snapshot.principal.username} · ${snapshot.principal.level}级`;
}
export function denyPage(message='当前账户权限范围内没有可查看的内容。'){
 const content=document.querySelector('.mf-content');if(!content)return false;
 content.replaceChildren();const box=document.createElement('section');box.className='access-denied mf-panel';
 const title=document.createElement('h1');title.textContent='当前账户无权查看';
 const description=document.createElement('p');description.textContent=message;box.append(title,description);content.append(box);return false;
}
export async function enterPage(resource=pageResource(location.href)){
 try{await ready();}catch(error){if(location.pathname==='/static/portal/login.html')return false;return denyPage(error.message+'，正在跳转到账户登录。');}
 bindRole();if(resource&&!readable(resource))return denyPage();if(resource)showAccess(resource);return true;
}
export async function switchRole(){throw Error('身份由登录账户决定，请退出后使用其他账户登录。');}
