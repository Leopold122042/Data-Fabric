"""Deterministic local fault-injection evaluation; never writes into the active demo database."""
import tempfile
from pathlib import Path
from management.store import Store
from management.catalog.service import Catalog
from management.reconciliation.service import Reconciliation
from execution.evidence import Evidence


def evaluate():
    with tempfile.TemporaryDirectory() as tmp:
        store=Store(Path(tmp)/'management.db');evidence=Evidence(Path(tmp)/'execution.db')
        catalog=Catalog(store);reconcile=Reconciliation(store,evidence,catalog)
        structure={'name':'age','type':'INTEGER'}
        store.save('object','field',{'kind':'field','name':'age','table':'t','source_id':'demo','structure':structure,'semantic_baseline':{'max_distribution_distance':.2,'min_hit_rate':.8}},0)
        store.save('relation','downstream',{'source':'field','target':'consumer'},0)
        for i in range(3):evidence.record({'id':'baseline-'+str(i),'object_kind':'object','object_id':'field','object_version':1,'environment':'demo','status':'success','task_scope':'fixed','latency_ms':10})
        cases=[('normal',{},set()),('structure',{'observed':{'structure':{'type':'TEXT'}}},{'structure'}),
            ('semantic',{'observed':{'distribution_distance':.9,'mapping_hit_rate':.1}},{'semantic'}),
            ('execution',{'latency_ms':100},{'execution'}),
            ('fulfillment',{'authorization':'revoked','delivered':True},{'fulfillment'})]
        rows=[]
        for name,patch,expected in cases:
            run={'id':name,'object_kind':'object','object_id':'field','object_version':1,'environment':'demo',
                 'status':'success','task_scope':'fixed','latency_ms':10,'observed':{'structure':structure},**patch}
            run=evidence.record(run);_,actual=reconcile.assess(run)
            rows.append({'scenario':name,'expected':sorted(expected),'detected':sorted(actual),'correct':set(actual)==expected})
        store.save('object','field',{'kind':'field','name':'age','table':'t','source_id':'demo','structure':{'name':'age','type':'TEXT'}},1)
        old=evidence.record({'id':'version-switch','object_kind':'object','object_id':'field','object_version':1,'environment':'demo',
            'status':'success','task_scope':'fixed','observed':{'structure':structure}})
        _,actual=reconcile.assess(old)
        naive_error=store.require('object','field')['structure']!=old['observed']['structure']
        rows.append({'scenario':'version-switch','expected':[],'detected':list(actual),'correct':not actual})
        return {'scope':'本地确定性扰动试验；不代表真实机构或全量业务验证','sample_size':len(rows),
            'correct_count':sum(r['correct'] for r in rows),'scenarios':rows,
            'injected_deviation_count':4,'type_correct_count':sum(r['correct'] for r in rows[1:5]),
            'impact_expected':['consumer'],'impact_actual':catalog.impact('field')['downstream'],
            'version_switch_comparison':{'baseline':'只与最新结构对照，忽略加载版本的对照实现',
                'baseline_false_positives':int(naive_error),'version_paired_false_positives':int(bool(actual))}}
