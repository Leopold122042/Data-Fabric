"""Bounded, resumable-per-session cloud discovery; persists definitions, never rows."""
import hashlib
import json
import os
import threading
from pathlib import Path
from datetime import datetime,timezone
from external_sources import (SourceError, SUPPORTED_FORMATS, list_directory, box_url, fetch,
                              file_extension, parse_file, safe_column_names, LogicalSession)
from external_rar import inspect_zip,inspect_rar

DIRECTORY=Path(__file__).parent/'private_catalog'/'discovered'
LOCK=threading.Lock()
LIMIT=100


def owner(username):return hashlib.sha256(username.strip().encode()).hexdigest()
def read_registry(username):
    path=DIRECTORY/(owner(username)+'.json')
    if not path.exists():return {}
    return json.loads(path.read_text(encoding='utf-8'))
def save_registry(username,records):
    DIRECTORY.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=DIRECTORY/(owner(username)+'.json');temp=path.with_suffix('.tmp')
    with open(temp,'w',encoding='utf-8',opener=lambda p,f:os.open(p,f,0o600)) as file:
        json.dump(records,file,ensure_ascii=False,indent=2)
    os.replace(temp,path)
def definitions(username=None):
    with LOCK:
        if username is not None:items=list(read_registry(username).values())
        else:
            items=[]
            if DIRECTORY.exists():
                for path in DIRECTORY.glob('*.json'):
                    items.extend(json.loads(path.read_text(encoding='utf-8')).values())
            items=[{k:v for k,v in item.items() if k not in ('path','member')} for item in items]
        return items


def start(root,recursive,archives):
    box_url(root)
    return {'root':root.strip().strip('/'),'queue':[{'directory':root.strip().strip('/'),'depth':0}],
            'recursive':recursive,'archives':archives,'visited':[], 'processed':0,'registered':0,
            'errors':[],'skipped':[],'table_ids':[],'done':False,'last_path':None,'last_payload':None}

def public_job(job):return {k:v for k,v in job.items() if k not in ('queue','visited','last_path','last_payload','table_ids')}

def step(job,username,token):
    if job['done']:return public_job(job)
    if not job['queue']:
        job['done']=True;job['last_payload']=None;publish_collection(job,username);return public_job(job)
    entry=job['queue'].pop(0)
    try:
        if 'directory' in entry:
            directory=entry['directory']
            if directory in job['visited']:return public_job(job)
            if len(job['visited'])>=20:
                job['skipped'].append('目录数量达到 20，未扫描：'+directory)
            else:
                job['visited'].append(directory)
                listing=list_directory(directory,username,token)
                if len(listing['items'])>=listing.get('limit',1000):job['skipped'].append('目录列表可能截断：'+directory)
                for item in listing['items']:
                    if item['directory']:
                        if job['recursive'] and entry['depth']<3:
                            job['queue'].append({'directory':item['path'],'depth':entry['depth']+1})
                        elif job['recursive']:job['skipped'].append('超过目录深度：'+item['path'])
                    elif file_extension(item['path']) in SUPPORTED_FORMATS | ({'rar','zip'} if job['archives'] else set()):
                        job['queue'].append({'path':item['path']})
                    elif len(job['skipped'])<200:job['skipped'].append('未支持或未选中：'+item['path'])
                if len(job['queue'])>1000:
                    job['queue']=job['queue'][:1000];job['skipped'].append('待扫描项目超过 1000，已截断。')
        else:
            if job['processed']>=LIMIT:
                job['skipped'].append('达到 100 个文件/包内条目上限，其余未扫描。');job['queue']=[]
            else:
                job['processed']+=1
                path=entry['path'];member=entry.get('member');ext=file_extension(member or path)
                if job['last_path']!=path:
                    job['last_payload']=fetch(box_url(path),username=username,token=token);job['last_path']=path
                payload=job['last_payload']
                if ext in ('zip','rar') and not member:
                    archive=(inspect_zip if ext=='zip' else inspect_rar)(payload)
                    candidates=[{'path':path,'member':i['name']} for i in archive['items'] if i['supported'] and not i['name'].startswith('__MACOSX/')]
                    job['queue']=candidates+job['queue']
                else:
                    if member:payload=(inspect_rar if file_extension(path)=='rar' else inspect_zip)(payload,member)['content']
                    tables=parse_file(payload,member or path,'box')
                    with LOCK:
                        records=read_registry(username)
                        for table in tables:
                            key='auto_'+hashlib.sha256((owner(username)+'\0'+path+'\0'+str(member)+'\0'+table[1]).encode()).hexdigest()[:20]
                            base={'id':key,'name':Path(member or path).name+' / '+table[1],
                                  'path':path,'member':member,'sheet':table[1],
                                  'scanned_at':datetime.now(timezone.utc).isoformat()}
                            records[key],_=automatic_mapping(table,base,records.get(key))
                            job.setdefault('table_ids',[]).append(key)
                            job['registered']+=1
                        save_registry(username,records)
    except Exception as exc:
        job['errors'].append({'item':entry.get('path',entry.get('directory','')),'member':entry.get('member'),
                              'error':str(exc) if isinstance(exc,SourceError) else '扫描失败，请检查文件格式或连接环境'})
    if not job['queue']:
        job['done']=True;job['last_payload']=None;job['last_path']=None
        publish_collection(job,username)
    return public_job(job)


def automatic_mapping(table,base,previous=None):
    """Always produce a queryable structural mapping; semantic uncertainty is a note."""
    record={**base,'detected_columns':table[2],'header_row':1,'mapping_mode':'raw',
            'mapping_version':2,'status':'可查询','columns':table[2],'types':table[3],
            'rows':len(table[4]),'reason':'自动保留原始字段；未命名列、多行说明的业务含义未识别'}
    result=table
    if previous and previous.get('mapping_mode')=='manual' and previous.get('detected_columns')==table[2]:
        row=previous.get('header_row',1)
        if row==1 or 0<=row-2<len(table[4]):
            result=table if row==1 else (table[0],table[1],safe_column_names(table[4][row-2],len(table[2])),['VARCHAR']*len(table[2]),table[4][row-1:])
            record.update(header_row=row,mapping_mode='manual',reason='保留用户自定义表头')
    elif any('排名' in row for row in table[4][:2]):
        try:
            candidate=mapped_table(table,{**record,'mapping_mode':'ranking'})
            result=candidate;record.update(mapping_mode='ranking',reason='自动识别明确的指标标题和排名区块；来源口径分别保留')
        except SourceError:
            record['reason']='指标区块未能可靠识别，已回退到原始字段；业务含义未识别'
    record.update(columns=result[2],types=result[3],rows=len(result[4]))
    return record,result


def current_table(record,username,token,cache=None):
    raw=read_table({**record,'mapping_mode':'raw'},username,token,1,cache)
    updated,table=automatic_mapping(raw,record,record)
    with LOCK:
        records=read_registry(username)
        if records.get(record['id'])!=record:raise SourceError('目录正在更新，请重试查询。')
        if updated!=record:
            records[record['id']]=updated;save_registry(username,records)
    return updated,table


def mapped_table(table,record):
    if record.get('mapping_mode')!='ranking':return table
    from external_virtual import normalize,COLUMNS,TYPES,METRICS
    if table[2]!=record['detected_columns']:raise SourceError('来源字段变化，请重新扫描。')
    rows=normalize(table,record,str(Path(record['path']).parent),METRICS|{'短视频'})
    if not rows:raise SourceError('未识别到评分记录，请重新核对来源。')
    return (table[0],table[1],COLUMNS,TYPES,rows)

def read_table(record,username,token,header_row,cache=None):
    cache={} if cache is None else cache
    path=record['path']
    if path not in cache:cache[path]=fetch(box_url(path),username=username,token=token)
    payload=cache[path]
    member=record.get('member')
    if member:payload=(inspect_rar if file_extension(record['path'])=='rar' else inspect_zip)(payload,member)['content']
    tables=parse_file(payload,member or record['path'],'box',selected_sheets=[record['sheet']])
    table=next((t for t in tables if t[1]==record['sheet']),None)
    if table is None:raise SourceError('工作表已不存在，请重新扫描。')
    if header_row==1:return mapped_table(table,record)
    if file_extension(member or record['path']) not in ('xlsx','xls'):raise SourceError('只有 Excel 支持调整表头行。')
    if header_row-2>=len(table[4]):raise SourceError('表头行超出数据范围。')
    cols=safe_column_names(table[4][header_row-2],len(table[2]))
    return (table[0],table[1],cols,['VARCHAR']*len(cols),table[4][header_row-1:])

def query(view_id,username,token,sql=None,preview=False,header_row=None,confirm=False):
    with LOCK:record=read_registry(username).get(view_id)
    if record is None:raise SourceError('未找到当前账户的虚拟表。')
    row=header_row or record.get('header_row',1)
    if not 1<=row<=50:raise SourceError('表头行须为 1 到 50。')
    if preview or confirm:
        table=read_table({**record,'mapping_mode':'raw'},username,token,row)
    else:
        record,table=current_table(record,username,token)
        row=record.get('header_row',1)
    if confirm:
        with LOCK:
            records=read_registry(username)
            if records.get(view_id)!=record:raise SourceError('目录已变化，请重新预览。')
            record.update(columns=table[2],types=table[3],rows=len(table[4]),header_row=row,confirmed=True,mapping_mode='manual',status='可查询',reason='用户自定义表头映射')
            records[view_id]=record;save_registry(username,records)
        return {'message':'已保存字段映射'}
    s=LogicalSession()
    try:
        s.replace('auto',record['path'],[(view_id,record['name'],table[2],table[3],table[4])])
        result=s.query(sql if sql and not preview else 'SELECT * FROM "'+view_id+'" LIMIT 20')
        return {**result,'source':record['path'],'sheet':record['sheet'],'header_row':row,'mode':'表头调整预览（可选）' if preview else '自动映射 · 按需读取'}
    finally:s.close()

# One persistent union definition per scanned root. Raw rows remain at their sources.
PROVENANCE=['来源目录','来源文件','包内文件','来源工作表','来源表编号','来源记录位置']

def collection_path(username):return DIRECTORY/'collections'/(owner(username)+'.json')
def collection_records(username):
    p=collection_path(username)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
def save_collections(username,groups):
    p=collection_path(username);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=p.with_suffix('.tmp')
    with open(tmp,'w',encoding='utf-8',opener=lambda p,f:os.open(p,f,0o600)) as f:json.dump(groups,f,ensure_ascii=False,indent=2)
    os.replace(tmp,p)

def change_membership(view_id,source_id,username,restore=False):
    with LOCK:
        groups=collection_records(username);group=groups.get(view_id)
        if group is None:raise SourceError('未找到当前账户的统一虚拟表。')
        excluded=list(group.get('excluded_source_ids',[]))
        members=list(group['source_ids'])
        if source_id not in members+excluded:raise SourceError('该来源不属于当前虚拟表。')
        if restore:
            if source_id not in read_registry(username):raise SourceError('来源定义已不存在，请重新扫描。')
            excluded=[k for k in excluded if k!=source_id]
            if source_id not in members:members.append(source_id)
        else:
            members=[k for k in members if k!=source_id]
            if source_id not in excluded:excluded.append(source_id)
        group.update(source_ids=members,excluded_source_ids=excluded)
        save_collections(username,groups)
        return {'message':'已恢复来源表' if restore else '已从统一虚拟表移除，源文件未修改'}

def publish_collection(job,username):
    with LOCK:
        records=read_registry(username)
        ids=job.get('table_ids',[])
        if not ids:return
        groups=collection_records(username)
        key='union_'+hashlib.sha256((owner(username)+'\0'+job['root']).encode()).hexdigest()[:20]
        job['collection_id']=key
        excluded=groups.get(key,{}).get('excluded_source_ids',[])
        groups[key]={'excluded_source_ids':excluded,'id':key,'name':(job['root'] or '云盘根目录')+' · 统一虚拟表','root':job['root'],
                     'source_ids':[k for k in dict.fromkeys(ids) if k not in excluded],'scanned_at':datetime.now(timezone.utc).isoformat(),
                     'incomplete':bool(job['errors'] or job['skipped'])}
        save_collections(username,groups)

def describe_collection(group,records,anonymous=False):
    sources=[records[k] for k in group['source_ids'] if k in records]
    # Prefix business columns to prevent collision with provenance columns.
    fields=list(dict.fromkeys(c for r in sources for c in r['columns']))
    result={**group,'columns':PROVENANCE+['数据·'+c for c in fields],
            'types':['VARCHAR']*(len(PROVENANCE)+len(fields)),
            'sources':[{'id':r['id'],'directory':str(Path(r['path']).parent),'path':r['path'],
                        'member':r.get('member') or '', 'sheet':r['sheet'],'status':r['status']} for r in sources]}
    result['excluded_sources']=[{'id':k,'name':records[k]['name']} for k in group.get('excluded_source_ids',[]) if k in records]
    if anonymous:
        result.pop('root',None)
        for r in result['sources']:r.update(directory='连接后可查看',path='连接后可查看',member='')
    return result

def collections(username=None):
    with LOCK:
        if username is not None:return [describe_collection(g,read_registry(username)) for g in collection_records(username).values()]
        result=[]
        for p in (DIRECTORY/'collections').glob('*.json'):
            registry=DIRECTORY/p.name
            records=json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
            result.extend(describe_collection(g,records,True) for g in json.loads(p.read_text(encoding='utf-8')).values())
        return result

def query_collection(view_id,username,token,source_ids,sql=None,keyword=None):
    if keyword is not None and sql:raise SourceError('关键词搜索不能同时提交 SQL。')
    with LOCK:
        group=collection_records(username).get(view_id)
        records=read_registry(username)
    if not group:raise SourceError('未找到当前账户的统一虚拟表，请连接扫描时使用的账户。')
    if not source_ids:raise SourceError('请至少选择一个来源表。')
    if any(k not in group['source_ids'] or k not in records for k in source_ids):raise SourceError('来源不属于该虚拟表，请刷新目录。')
    desc=describe_collection(group,records)
    tables=[];loaded=0;cells=0;cache={}
    for key in dict.fromkeys(source_ids):
        r=records[key]
        r,t=current_table(r,username,token,cache)
        records[key]=r
        cells+=len(t[4])*(len(t[2])+len(PROVENANCE))
        if cells>2000000:raise SourceError('本次读取超过 200 万个有效单元格，请缩小来源范围。')
        rows=[]
        for i,values in enumerate(t[4],1):
            rows.append([str(Path(r['path']).parent),r['path'],r.get('member') or '',r['sheet'],key,str(i)]+[None if v is None else str(v) for v in values])
        loaded+=len(rows)
        if keyword is not None:
            needle=keyword.strip().lower()
            rows=[row for row in rows if not needle or any(needle in value.lower() for value in row[len(PROVENANCE):] if value is not None)]
            if not rows:continue
        columns=PROVENANCE+['数据·'+c for c in t[2]]
        if keyword is not None:
            # Evaluate over all matches, before the 200-row display limit.
            keep=list(range(len(PROVENANCE)))+[i for i in range(len(PROVENANCE),len(columns))
                if any(row[i] is not None and str(row[i]).strip() for row in rows)]
            columns=[columns[i] for i in keep]
            rows=[[row[i] for i in keep] for row in rows]
        tables.append((key,r['name'],columns,['VARCHAR']*len(columns),rows))
    desc=describe_collection(group,records)
    # Keep heterogeneous tables sparse; DuckDB aligns columns in the virtual view.
    s=LogicalSession()
    try:
        from external_sources import quote_id
        s.replace('union',group['root'],tables)
        if keyword is not None:
            # Only matching sources and business fields with nonblank matched values participate.
            if not tables:
                return {'columns':[],'rows':[],'truncated':False,'limit':200,'elapsed_ms':0,
                        'source_count':len(set(source_ids)),'matched_source_count':0,'loaded_rows':loaded}
            parts=['SELECT * FROM '+quote_id(t[0]) for t in tables]
            s.con.execute('CREATE VIEW '+quote_id(view_id)+' AS '+' UNION ALL BY NAME '.join(parts))
            result=s.query('SELECT * FROM '+quote_id(view_id)+' LIMIT 201')
        else:
            # Keep the explicit SQL API's full schema for backwards compatibility.
            schema='SELECT '+', '.join('CAST(NULL AS VARCHAR) AS '+quote_id(c) for c in desc['columns'])+' WHERE FALSE'
            parts=[schema]+['SELECT * FROM '+quote_id(t[0]) for t in tables]
            s.con.execute('CREATE VIEW '+quote_id(view_id)+' AS '+' UNION ALL BY NAME '.join(parts))
            result=s.query(sql or 'SELECT * FROM '+quote_id(view_id)+' LIMIT 201')
        return {**result,'source_count':len(set(source_ids)),'matched_source_count':len(tables),'loaded_rows':loaded}
    finally:s.close()
