"""External connectors are isolated per browser session; credentials are request-only."""
import copy
import hashlib
from external_rar import inspect_rar, inspect_zip
import secrets
import threading
import time
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from external_sources import (LogicalSession, SourceError, DEFAULT_GITHUB, DEFAULT_EXCEL,
                              github_url, box_url, fetch, list_directory, parse_file, file_extension)

class PrivateInputRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            from execution.external_audit import record
            started=time.perf_counter()
            try:
                from execution.external.routes import authorize
                await authorize(request,'before')
                response=await original(request)
                if response.status_code<400:await authorize(request,'delivery')
                await record(request,response,started)
                return response
            except HTTPException as exc:
                await record(request,None,started,exc.status_code)
                raise
            except RequestValidationError:
                # Default validation errors may echo the submitted WebDAV token.
                raise HTTPException(422, '请求参数无效，请检查路径、用户名和令牌长度。') from None
        return handler

router = APIRouter(prefix='/api/external', tags=['外部数据接入'], route_class=PrivateInputRoute)
_sessions = {}
_lock = threading.Lock()
COOKIE = 'medfabric_external'
TTL = 1800

class ArchiveSelection(BaseModel):
    archive_member: str | None = Field(default=None, max_length=2000)
    archive_digest: str | None = Field(default=None, max_length=64)

class GitHubInput(ArchiveSelection):
    url: str = Field(default=DEFAULT_GITHUB, max_length=2000)
    model: str = Field(default='auto', max_length=30)

class BoxInput(ArchiveSelection):
    path: str = Field(default='', max_length=2000)
    username: str = Field(default="", max_length=200)
    token: str = Field(default="", max_length=2000)

class QueryInput(BaseModel):
    sql: str = Field(min_length=1, max_length=10000)


def check_origin(request, response):
    origin = request.headers.get('origin')
    if (request.headers.get('x-medfabric-external') != '1' or
        (origin and urlsplit(origin).netloc != request.headers.get('host')) or
        request.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(403, '请从 MedFabric 同源页面访问外部数据接口。')
    response.headers['Cache-Control'] = 'no-store'


def session(request, response):
    check_origin(request,response)
    with _lock:
        now = time.monotonic()
        for key, value in list(_sessions.items()):
            if now-value.touched > TTL:
                value.close()
                del _sessions[key]
        key = request.cookies.get(COOKIE)
        if key not in _sessions:
            if len(_sessions)>=8: raise HTTPException(429, '会话已满，请稍后重试或释放不用的会话。')
            try: value = LogicalSession()
            except SourceError as exc: raise HTTPException(503,str(exc)) from None
            key = secrets.token_urlsafe(32)
            _sessions[key] = value
        value = _sessions[key]
        value.touched = now
        response.set_cookie(COOKIE,key,httponly=True,samesite='strict',
                            secure=request.url.scheme=='https',max_age=TTL,path='/api/external')
        return value


def perform(callback):
    try: return callback()
    except HTTPException: raise
    except SourceError as exc: raise HTTPException(400,str(exc)) from None
    except Exception: raise HTTPException(400,'外部数据处理失败，请检查文件格式和运行环境。') from None


@router.get('/status')
def status(request: Request, response: Response):
    s = session(request,response)
    return {**s.snapshot(),'defaults':{'github_url':DEFAULT_GITHUB,'box_path':DEFAULT_EXCEL}}


def load_source(s, payload, location, source, body, model='raw'):
    ext = file_extension(location)
    if ext not in ('rar','zip'):
        if body.archive_member is not None: raise SourceError('文件不是 RAR 或 ZIP，请重新读取来源。')
        return s.replace(source,location,parse_file(payload,location,source,model))
    if body.archive_member is not None and body.archive_digest != hashlib.sha256(payload).hexdigest():
        raise SourceError('压缩包已变化，请重新读取文件列表后选择。')
    archive = (inspect_zip if ext=='zip' else inspect_rar)(payload,body.archive_member)
    if body.archive_member is None:
        return {'archive':{'source':source,'location':location,**archive}}
    tables = parse_file(archive['content'],body.archive_member,source,model)
    return s.replace(source,location+' :: '+body.archive_member,tables)


@router.post('/github/load')
def github_load(body: GitHubInput, request: Request, response: Response):
    s = session(request,response)
    def run():
        url = github_url(body.url)
        return load_source(s,fetch(url),url,'github',body,body.model)
    return perform(run)


def cloud_auth(current, body):
    if body.username or body.token:
        if not body.username.strip() or not body.token:
            raise HTTPException(400,'请同时填写云盘用户名和令牌。')
        return body.username.strip(),body.token
    if current.cloud_credentials:
        return current.cloud_credentials
    raise HTTPException(401,'云盘尚未连接或会话已失效，请先连接云盘。')


@router.post('/box/connect')
def box_connect(body: BoxInput, request: Request, response: Response):
    current=session(request,response)
    def run():
        from external_virtual import catalog
        if not body.username.strip() or not body.token:
            raise HTTPException(400,'请填写云盘用户名和令牌。')
        with current.virtual_lock:
            # A failed account switch must not leave the previous account active.
            current.cloud_credentials=None
            current.scan_job=None
            current.virtual_files.clear()
            list_directory(catalog()['root'],body.username,body.token)
            current.cloud_credentials=(body.username.strip(),body.token)
        return {'connected':True,'message':'云盘已连接，授权仅在当前会话内存中保留。'}
    return perform(run)


@router.post('/box/disconnect')
def box_disconnect(request: Request, response: Response):
    current=session(request,response)
    with current.virtual_lock:
        current.cloud_credentials=None
        current.scan_job=None
        current.virtual_files.clear()
    result=current.replace('box','',[])
    return {**result,'connected':False}


@router.get('/virtual/catalog')
def saved_virtual_catalog(request: Request, response: Response):
    check_origin(request,response)
    with _lock:
        current=_sessions.get(request.cookies.get(COOKIE))
        if current is not None and time.monotonic()-current.touched>TTL:current=None
    def run():
        from external_virtual import catalog
        result=copy.deepcopy(catalog())
        connected=False
        if current is not None:
            with current.virtual_lock:
                connected=bool(current.cloud_credentials)
        result['connected']=connected
        if not connected:
            result.pop('root',None)
            for source in result['sources']:
                source['path']='连接云盘后可查看'
                source.pop('member',None)
        return result
    return perform(run)


@router.post('/box/list')
def box_list(body: BoxInput, request: Request, response: Response):
    current=session(request,response)
    def run():
        username,token=cloud_auth(current,body)
        return list_directory(body.path,username,token)
    return perform(run)


@router.post('/box/load')
def box_load(body: BoxInput, request: Request, response: Response):
    s = session(request,response)
    def run():
        username,token=cloud_auth(s,body)
        body.path = body.path.strip()
        url = box_url(body.path)
        return load_source(s,fetch(url,username=username,token=token),body.path,'box',body)
    return perform(run)


@router.post('/query')
def query(body: QueryInput, request: Request, response: Response):
    return perform(lambda:session(request,response).query(body.sql))


@router.post('/clear')
def clear(request: Request, response: Response):
    s = session(request,response)
    with _lock:
        for key, value in list(_sessions.items()):
            if value is s:
                value.close()
                del _sessions[key]
    response.delete_cookie(COOKIE,path='/api/external')
    return {'message':'当前会话的逻辑表已释放。'}


class VirtualQueryInput(BoxInput):
    force_refresh: bool = False
    view_id: str = Field(max_length=80)
    sql: str = Field(min_length=1, max_length=10000)


@router.post('/virtual/catalog')
def virtual_catalog(body: BoxInput, request: Request, response: Response):
    session(request,response)
    def run():
        from external_virtual import catalog
        config=catalog()
        # Verify cloud access before revealing private source metadata.
        list_directory(config['root'],body.username,body.token)
        return config
    return perform(run)


@router.post('/virtual/query')
def virtual_query(body: VirtualQueryInput, request: Request, response: Response):
    current=session(request,response)
    def run():
        from external_virtual import execute_virtual
        with current.virtual_lock:
            username,token=cloud_auth(current,body)
            return execute_virtual(body.view_id,username,token,body.sql,current.virtual_files,body.force_refresh)
    return perform(run)

class DiscoveryStart(BaseModel):
    root: str = Field(default='',max_length=2000)
    recursive: bool = True
    archives: bool = True

class DiscoveryQuery(BaseModel):
    view_id: str = Field(max_length=80)
    sql: str | None = Field(default=None,max_length=10000)
    preview: bool = False
    header_row: int | None = Field(default=None,ge=1,le=50)
    confirm: bool = False

@router.get('/discovery/catalog')
def discovery_catalog(request: Request,response: Response):
    check_origin(request,response)
    with _lock:
        current=_sessions.get(request.cookies.get(COOKIE))
        if current and time.monotonic()-current.touched>TTL:current=None
    def run():
        from external_discovery import definitions,collections
        if current:
            with current.virtual_lock:
                user=current.cloud_credentials[0] if current.cloud_credentials else None
                return {'tables':definitions(user),'collections':collections(user)}
        return {'tables':definitions(),'collections':collections()}
    return perform(run)

@router.post('/discovery/start')
def discovery_start(body:DiscoveryStart,request:Request,response:Response):
    current=session(request,response)
    def run():
        from external_discovery import start,public_job
        with current.virtual_lock:
            cloud_auth(current,BoxInput())
            current.scan_job=start(body.root,body.recursive,body.archives)
            return public_job(current.scan_job)
    return perform(run)

@router.post('/discovery/step')
def discovery_step(request:Request,response:Response):
    current=session(request,response)
    def run():
        from external_discovery import step
        with current.virtual_lock:
            username,token=cloud_auth(current,BoxInput())
            if not current.scan_job:raise SourceError('没有待继续的扫描任务，请先开始扫描。')
            return step(current.scan_job,username,token)
    return perform(run)

@router.post('/discovery/query')
def discovery_query(body:DiscoveryQuery,request:Request,response:Response):
    current=session(request,response)
    def run():
        from external_discovery import query
        with current.virtual_lock:
            username,token=cloud_auth(current,BoxInput())
            return query(body.view_id,username,token,body.sql,body.preview,body.header_row,body.confirm)
    return perform(run)


class CollectionQuery(BaseModel):
    keyword: str | None = Field(default=None,max_length=500)
    view_id: str = Field(max_length=80)
    source_ids: list[str] = Field(min_length=1,max_length=2000)
    sql: str | None = Field(default=None,max_length=10000)

@router.post('/discovery/collection-query')
def discovery_collection_query(body:CollectionQuery,request:Request,response:Response):
    current=session(request,response)
    def run():
        from external_discovery import query_collection
        with current.virtual_lock:
            username,token=cloud_auth(current,BoxInput())
            return query_collection(body.view_id,username,token,body.source_ids,body.sql,body.keyword)
    return perform(run)


class CollectionMembership(BaseModel):
    view_id: str = Field(max_length=80)
    source_id: str = Field(max_length=80)
    restore: bool = False

@router.post('/discovery/collection-membership')
def discovery_collection_membership(body:CollectionMembership,request:Request,response:Response):
    current=session(request,response)
    def run():
        from external_discovery import change_membership
        with current.virtual_lock:
            username,_=cloud_auth(current,BoxInput())
            return change_membership(body.view_id,body.source_id,username,body.restore)
    return perform(run)


class PublishMetadataInput(BaseModel):
    table_ids: list[str] = Field(min_length=1,max_length=100)
    share_metadata: bool = False


@router.post('/platform/publish')
def publish_platform_metadata(body:PublishMetadataInput,request:Request,response:Response):
    principal=request.app.state.identity.principal(request)
    if principal['role']!='platform':raise HTTPException(403,'仅平台方可纳入共享目录')
    if not body.share_metadata:raise HTTPException(400,'需确认表名及字段结构将进入平台共享目录')
    current=session(request,response)
    with current.virtual_lock:
        if not current.cloud_credentials:raise HTTPException(401,'请先在外部接入页连接云盘，再选择当前账户的表')
        from management.catalog.external import publish
        return publish(request.app.state.runtime.management.catalog,current.cloud_credentials[0],body.table_ids,principal['id'])


@router.get('/platform/status')
def platform_connection_status(request:Request,response:Response):
    check_origin(request,response)
    with _lock:
        current=_sessions.get(request.cookies.get(COOKIE))
        if current is not None and time.monotonic()-current.touched>TTL:current=None
    if current is None:return {'connected':False}
    with current.virtual_lock:return {'connected':bool(current.cloud_credentials)}
