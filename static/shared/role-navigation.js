import {canRead,pageResource} from './access-model.js?v=identity-20261111';

export const userHome='/static/portal/index.html';
export function roleDestination(href,permissions){
 const url=new URL(href,'http://local');
 if(url.pathname==='/static/portal/index.html'&&url.searchParams.get('page')==='admin'
    &&!['platform','auditor'].includes(permissions.principal?.role))return userHome;
 const resource=pageResource(url.href);
 if(resource&&!canRead(permissions,resource))return userHome;
 return url.pathname+url.search+url.hash;
}
