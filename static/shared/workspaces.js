export function workspaceFor(pathname) {
 const path=pathname.replace(/\/+$/,'');
 if(['/static/index.html','/static/workspace.html','/static/portal/accounts.html','/admin'].includes(path)||
    ['/admin/','/static/management/','/static/platform/','/static/execution/'].some(prefix=>path.startsWith(prefix)))return 'admin';
 if(path==='/showcase'||path.startsWith('/showcase/')||path.startsWith('/static/report/'))return 'showcase';
 return 'user';
}

export function loginURL(location, keepDestination=true) {
 const path='/'+workspaceFor(location.pathname)+'/login';
 return keepDestination ? path+'?return='+encodeURIComponent(location.pathname+location.search+location.hash) : path;
}

export function workspaceReturn(location) {
 const area=workspaceFor(location.pathname);
 const fallback='/'+area;
 const requested=new URLSearchParams(location.search).get('return');
 if(!requested?.startsWith('/')||requested.startsWith('//'))return fallback;
 try {
  const url=new URL(requested,location.origin);
  const path=url.pathname.replace(/\/+$/,'');
  if(url.origin!==location.origin||workspaceFor(path)!==area)return fallback;
  const sameAreaEntry=path===fallback||path==='/user/services'||path==='/admin/accounts';
  if(!sameAreaEntry&&!(path.startsWith('/static/')&&path.endsWith('.html')&&path!=='/static/portal/login.html'))return fallback;
  return url.pathname+url.search+url.hash;
 }catch{
  return fallback;
 }
}
