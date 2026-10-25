"""Bounded per-browser-session, per-credential in-memory file cache."""
import hashlib
import time
from datetime import datetime,timezone
from external_sources import fetch,box_url,SourceError,dependencies

TTL=300
MAX_CACHE_BYTES=60*1024*1024


def remote_version(path,username,token):
    _,requests,_,certifi=dependencies()
    try:
        r=requests.head(box_url(path),auth=(username.strip(),token),timeout=(10,20),allow_redirects=False,verify=certifi.where())
        try:
            if r.status_code in (401,403,404):raise SourceError('云盘认证失败、无权限或文件不存在，未使用旧缓存。')
            if r.status_code != 200:return None
            etag=r.headers.get('ETag')
            if etag and not etag.startswith('W/'):return ('etag',etag)
            modified,size=r.headers.get('Last-Modified'),r.headers.get('Content-Length')
            return ('modified',modified,size) if modified and size else None
        finally:r.close()
    except requests.RequestException:
        raise SourceError('无法检查云盘文件版本，请检查网络后重试；未返回旧缓存。') from None


class FileCache:
    def __init__(self,store,username,token,force=False):
        self.store=store;self.username=username;self.token=token;self.force=force
        self.identity=hashlib.sha256((username.strip()+'\0'+token).encode()).hexdigest()
        self.used={};self.evidence={}
    def get(self,path):
        if path in self.used:return self.used[path]
        now=time.monotonic()
        # Changed credentials must not reuse the previous identity's private data.
        for key in list(self.store):
            if key[0]!=self.identity or now-self.store[key]['at']>=TTL:del self.store[key]
        key=(self.identity,path);entry=self.store.get(key)
        version=remote_version(path,self.username,self.token)
        if entry and not self.force and entry['version']==version:
            payload=entry['payload'];state='版本未变，使用缓存' if version else '源端无版本信息，使用 5 分钟缓存'
        else:
            # Never retain an invalidated entry when refresh fails.
            self.store.pop(key,None)
            payload=fetch(box_url(path),username=self.username,token=self.token)
            entry={'payload':payload,'at':time.monotonic(),'version':version,'fetched_at':datetime.now(timezone.utc).isoformat()}
            while self.store and sum(len(v['payload']) for v in self.store.values())+len(payload)>MAX_CACHE_BYTES:
                del self.store[min(self.store,key=lambda k:self.store[k]['at'])]
            if len(payload)<=MAX_CACHE_BYTES:self.store[key]=entry
            state='手动刷新下载' if self.force else '重新下载'
        self.used[path]=payload
        self.evidence[path]={'cache_status':state,'downloaded_at':entry['fetched_at'],'bytes':len(payload)}
        return payload
