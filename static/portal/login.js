import {workspaceFor,workspaceReturn} from '/static/shared/workspaces.js?v=0.1.46';
const $=selector=>document.querySelector(selector);
const query=new URLSearchParams(location.search);
const loginForm=$('#login-form'),registerForm=$('#register-form'),changeForm=$('#change-form');
const area=workspaceFor(location.pathname),returnTo=workspaceReturn(location);
if(area==='user')document.body.classList.add('fabric-auth');
const name={user:'用户工作台',admin:'管理后台',showcase:'课题展示'}[area];
document.title='登录'+name+' · 数据编织平台';
$('#login-title').textContent='登录'+name;
$('#login-switch').hidden=area!=='user';
if(area!=='user'){
 $('#login-switch').hidden=true;
 $('.intro').textContent=area==='admin'?'使用平台账户登录，管理操作按账户权限开放。':'使用平台账户登录，打开四个课题的独立演示。';
}
let account='';

async function request(path,body){
 const response=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',
  headers:body?{'Content-Type':'application/json'}:{Accept:'application/json'},...(body?{body:JSON.stringify(body)}:{})});
 let data={};try{data=await response.json();}catch{}
 if(!response.ok)throw Error(data.error||'平台暂时无法完成此操作');
 return data;
}
function changeMode(username){
 account=username;loginForm.hidden=true;registerForm.hidden=true;$('#login-switch').hidden=true;$('#register-switch').hidden=true;changeForm.hidden=false;$('#new-password').focus();
}
function registerMode(){loginForm.hidden=true;changeForm.hidden=true;$('#login-switch').hidden=true;registerForm.hidden=false;$('#register-switch').hidden=false;$('#register-display-name').focus();}
function loginMode(){registerForm.hidden=true;changeForm.hidden=true;$('#register-switch').hidden=true;loginForm.hidden=false;$('#login-switch').hidden=area!=='user';$('#username').focus();}
function go(){location.replace(returnTo);}

$('#show-register').addEventListener('click',registerMode);
$('#show-login').addEventListener('click',loginMode);

loginForm.addEventListener('submit',async event=>{
 event.preventDefault();const button=$('#login-submit'),error=$('#login-error');
 button.disabled=true;error.textContent='';
 try{
  const result=await request('/api/auth/login',{username:$('#username').value.trim(),password:$('#password').value});
  account=result.principal.username;$('#password').value='';
  if(result.principal.must_change_password)changeMode(account);else go();
 }catch(cause){error.textContent=cause.message;button.disabled=false;}
});

registerForm.addEventListener('submit',async event=>{
 event.preventDefault();const error=$('#register-error'),button=$('#register-submit');error.textContent='';
 const username=$('#register-username').value.trim().toLowerCase();
 const displayName=$('#register-display-name').value.trim();
 const password=$('#register-password').value,confirmation=$('#register-confirm-password').value;
 if(password.length<10){error.textContent='密码至少需要10个字符。';return;}
 if(password!==confirmation){error.textContent='两次输入的密码不一致。';return;}
 button.disabled=true;
 try{
  await request('/api/auth/register',{username,display_name:displayName,password,confirm_password:confirmation});
  $('#register-password').value='';$('#register-confirm-password').value='';go();
 }catch(cause){error.textContent=cause.message;button.disabled=false;}
});

changeForm.addEventListener('submit',async event=>{
 event.preventDefault();const error=$('#change-error');error.textContent='';
 const password=$('#new-password').value,confirmation=$('#confirm-password').value;
 if(password.length<10){error.textContent='新密码至少需要10个字符。';return;}
 if(password!==confirmation){error.textContent='两次输入的新密码不一致。';return;}
 const button=changeForm.querySelector('button');button.disabled=true;
 try{await request('/api/auth/password',{new_password:password,confirm_password:confirmation});go();}
 catch(cause){error.textContent=cause.message;button.disabled=false;}
});

request('/api/auth/session').then(result=>{
 const principal=result.principal;
 if(principal.must_change_password)changeMode(principal.username);else go();
}).catch(()=>{
 if(query.get('change'))$('#login-error').textContent='临时密码已失效，请使用管理员重新发放的密码登录。';
});
