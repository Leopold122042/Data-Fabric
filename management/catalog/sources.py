"""Pluggable local metadata connectors. Paths are confined to the configured data root."""
import csv
import json
import sqlite3
from pathlib import Path
from management.store import DomainError


def sql_identifier(name):return '"'+str(name).replace('"','""')+'"'


class SQLiteSource:
    def __init__(self,path):self.path=Path(path)
    def connection(self):return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
    def snapshot(self):
        con=self.connection()
        try:
            result={}
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                columns=[{'name':r[1],'type':r[2],'nullable':not bool(r[3]),'default':r[4],'primary_key':bool(r[5])}
                         for r in con.execute('PRAGMA table_info('+sql_identifier(name)+')')]
                result[name]={'columns':[c['name'] for c in columns],'column_details':columns,
                    'rows':con.execute('SELECT COUNT(*) FROM '+sql_identifier(name)).fetchone()[0]}
            return {'tables':result}
        finally:con.close()
    def profile(self,table,column):
        snap=self.snapshot()
        if table not in snap['tables'] or column not in snap['tables'][table]['columns']:raise DomainError('字段不存在')
        con=self.connection()
        try:
            dtype=next(c['type'] for c in snap['tables'][table]['column_details'] if c['name']==column)
            from management.semantic.service import family
            count,nonnull=con.execute(f'SELECT COUNT(*),COUNT({sql_identifier(column)}) FROM {sql_identifier(table)}').fetchone()
            result={'row_count':count,'non_null_count':nonnull,'null_ratio':(count-nonnull)/count if count else None,'type':dtype}
            if family(dtype)=='number':
                low,high=con.execute(f'SELECT MIN({sql_identifier(column)}),MAX({sql_identifier(column)}) FROM {sql_identifier(table)}').fetchone()
                result.update(min=low,max=high)
            return result
        finally:con.close()


class FileSource:
    def __init__(self,path,kind):self.path,self.kind=Path(path),kind
    def snapshot(self):
        with self.path.open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f)) if self.kind=='csv' else json.load(f)
        if isinstance(rows,dict):rows=[rows]
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise DomainError('JSON源必须是对象或对象数组')
        columns=sorted({k for row in rows for k in row})
        details=[]
        for k in columns:
            vals=[r[k] for r in rows if r.get(k) is not None]
            typ='unknown'
            if vals:
                typ='boolean' if all(type(v)==bool for v in vals) else 'INTEGER' if all(type(v)==int for v in vals) else 'REAL' if all(type(v) in (int,float) for v in vals) else 'TEXT'
            details.append({'name':k,'type':typ,'nullable':len(vals)<len(rows)})
        return {'tables':{self.path.stem:{'columns':columns,'column_details':details,'rows':len(rows)}}}


class SourceRegistry:
    def __init__(self,store,data_root):
        self.store,self.root=store,Path(data_root).resolve()
        self.factories={'sqlite':SQLiteSource,'csv':lambda p:FileSource(p,'csv'),'json':lambda p:FileSource(p,'json')}
    def register_factory(self,kind,factory):self.factories[kind]=factory
    def resolve_path(self,path):
        target=(self.root/path).resolve()
        if not target.is_relative_to(self.root):raise DomainError('源文件必须位于MEDFABRIC_DATA_DIR内')
        if not target.is_file():raise DomainError('源文件不存在')
        return target
    def register(self,body,actor):
        kind=body.get('connector');sid=body.get('source_id','')
        if not sid or not sid.replace('-','').replace('_','').isalnum():raise DomainError('数据源ID只能包含字母、数字、连字符和下划线')
        if sid in ('his','lis','pacs','iot','emr'):raise DomainError('不能覆盖现有演示源')
        if kind not in self.factories:raise DomainError('连接器未安装')
        path=self.resolve_path(body.get('path',''));self.factories[kind](path).snapshot()
        return self.store.save('source',sid,{'source_id':sid,'connector':kind,'path':str(path.relative_to(self.root)),
            'name':body.get('name',sid)},body.get('expected_version',0),actor,'登记本地数据源')
    def adapters(self):
        class FailedSource:
            def __init__(self,error):self.error=error
            def snapshot(self):raise DomainError(self.error)
        result={}
        for source in self.store.list('source'):
            try:result[source['id']]=self.factories[source['connector']](self.resolve_path(source['path']))
            except (DomainError,KeyError) as exc:result[source['id']]=FailedSource(str(exc))
        return result
