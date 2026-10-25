"""Persisted metadata-only views; fetch source workbooks at query time."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from external_sources import SourceError, fetch, box_url, parse_file, LogicalSession
from external_rar import inspect_rar, inspect_zip
from virtual_cache import FileCache
from virtual_planner import choose_sources

CATALOG_PATH = Path(__file__).parent / 'private_catalog' / 'cesai.json'
METRICS = {'APP','微信','双微','新媒体','新媒体指数','网站','省市综合'}
ENTITY_HEADERS = {'直辖市','省份','地市','部委名称'}
COLUMNS = ['资料集','口径','对象名称','指标','排名','数值','来源文件','包内文件','工作表','记录位置']
TYPES = ['VARCHAR']*4+['INTEGER','DOUBLE']+['VARCHAR']*4


def catalog():
    try:
        return json.loads(CATALOG_PATH.read_text())
    except (OSError,ValueError):
        raise SourceError('虚拟表目录尚未配置或无法读取。') from None


def normalize(table, definition, dataset, metrics=None):
    """Recognize explicit ranking headers and metric titles, never infer positional joins."""
    _, label, columns, _, data = table
    matrix = [[None if c.startswith('column_') else c for c in columns]] + data
    groups, titles, output = [], {}, []
    scope = {'地级':'地市','地级市':'地市','地市':'地市'}.get(label,label)
    if label == '省份': scope='省份（原表口径，待确认）'
    if label == '（省份）省+市': scope='省+市汇总'
    for position, row in enumerate(matrix,1):
        row = list(row)
        metric_positions = {i:str(v).strip() for i,v in enumerate(row) if v is not None and str(v).strip() in (metrics or METRICS)}
        if metric_positions:
            titles = metric_positions
            groups = []
            continue
        starts = [i for i in range(len(row)-2) if row[i]=='排名' and row[i+1] in ENTITY_HEADERS]
        if starts:
            groups = []
            for i in starts:
                candidates = [j for j in titles if j<=i]
                if not candidates: raise SourceError(f'工作表 {label} 的指标标题发生变化，请重新检查映射。')
                groups.append((i,titles[max(candidates)]))
            continue
        for i,metric in groups:
            if i+2>=len(row):continue
            rank,name,score=row[i:i+3]
            if name is None or str(name).strip() in ('','平均值','平均分','均值'):continue
            try:
                score=float(score)
                if not math.isfinite(score):continue
                if rank in (None,''): rank=None
                else:
                    number=float(rank)
                    if not number.is_integer() or number<1:continue
                    rank=int(number)
            except (TypeError,ValueError):continue
            output.append([dataset,scope,str(name).strip(),metric,rank,score,definition['path'],definition.get('member',''),label,f'非空记录 {position}，列 {i+1}-{i+3}'])
    return output


def read_definition(definition, username, token, cache):
    path=definition['path']
    payload=cache.get(path)
    member=definition.get('member')
    if member:
        decoder=inspect_rar if path.lower().endswith('.rar') else inspect_zip
        payload=decoder(payload,member)['content']
    return parse_file(payload,member or path,'box',selected_sheets=definition['sheets'])


def execute_virtual(view_id, username, token, sql, file_cache=None, force_refresh=False):
    config=catalog()
    view=next((v for v in config['views'] if v['id']==view_id),None)
    if view is None:raise SourceError('虚拟表不存在，请重新加载目录。')
    selected,limits=choose_sources(config,view,sql)
    cache=FileCache(file_cache if file_cache is not None else {},username,token,force_refresh)
    rows,evidence=[],[]
    for definition in selected:
        tables=read_definition(definition,username,token,cache)
        found={t[1]:t for t in tables}
        for sheet in definition['sheets']:
            if sheet not in found:raise SourceError('来源工作表已变化，请检查虚拟表定义。')
            result=normalize(found[sheet],definition,config['dataset'])
            capability=definition.get('sheet_capabilities',{}).get(sheet,{})
            result=[row for row in result if
                    ('指标' not in capability or row[3] in capability['指标']) and
                    ('口径' not in capability or row[1] in capability['口径'])]
            if not result:raise SourceError(f'工作表 {sheet} 没有识别到评分记录，请检查表头或源端公式缓存。')
            rows.extend(result)
            if len(rows)>50000:raise SourceError('虚拟表映射结果超过 50000 行，请缩小来源范围。')
        evidence.append({'path':definition['path'],'member':definition.get('member'),'sheets':definition['sheets'],**cache.evidence.get(definition['path'],{})})
    session=LogicalSession()
    try:
        session.replace('virtual',config['dataset'],[(view['id'],view['name'],COLUMNS,TYPES,rows)])
        result=session.query(sql)
        return {**result,'view':view['id'],'loaded_rows':len(rows),'fetched_at':datetime.now(timezone.utc).isoformat(),
                'sources':evidence,'plan':{'selected_sources':len(selected),'total_sources':len(view['sources']),'constraints':{k:sorted(v) for k,v in limits.items()}},
                'cache':'文件在当前会话内最多缓存 5 分钟；有版本信息时先校验，SQL 结果每次重新计算。'}
    finally:session.close()
