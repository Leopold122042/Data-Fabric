import tempfile
import unittest
from pathlib import Path
from management.facade import Management
from management.store import Store,DomainError
from execution.evidence import Evidence


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.e=Evidence(self.root/'execution.db');self.m=Management(self.root/'management.db',self.e)
        self.s=self.m.store
        self.m.catalog.ingest('demo',{'tables':{'t':{'columns':['age','name'],'column_details':[{'name':'age','type':'INTEGER'},{'name':'name','type':'TEXT'}]}}})
        self.age=next(x for x in self.s.list('object') if x['name']=='age')
        self.name=next(x for x in self.s.list('object') if x['name']=='name')
    def tearDown(self):self.tmp.cleanup()
    def concept(self):return self.m.semantic.concept({'name':'年龄','meaning':'年龄','domain':'demo','data_type':'integer'},'tester')
    def mapping(self,c):return self.m.semantic.mapping({'concept_id':c['id'],'fields':[self.age['id']],'mapping_type':'direct','status':'confirmed','confirmation_note':'核对类型和含义'},'tester')
    def service(self):
        return self.m.services.save({'name':'测试服务','service_type':'query','mode':'regular','visibility':'internal',
            'purpose':'research','output':'汇总','audience':'researcher','conditions':'授权','provider':'demo-provider',
            'source_scope':'demo','update_period':'daily','quality_commitment':'complete','restrictions':'no detail','metering':'calls',
            'input_schema':{'type':'object'},'output_schema':{'type':'object'},'sample_input':{},'source_objects':[self.age['id']]},'tester')
    def run_record(self,**extra):
        return self.e.record({'object_kind':'object','object_id':self.age['id'],'object_version':1,'environment':'demo',
            'status':'success','task_scope':'same cohort',**extra})

    def test_versions_persist_and_stale_update_rejected(self):
        edited=self.m.catalog.annotate(self.age['id'],{'meaning':'岁','expected_version':1},'tester')
        other=Store(self.root/'management.db')
        self.assertEqual(other.require('object',self.age['id'])['version'],2)
        self.assertNotEqual(other.require('object',self.age['id'],1)['meaning'],edited['meaning'])
        with self.assertRaises(DomainError):self.m.catalog.annotate(self.age['id'],{'meaning':'x','expected_version':1},'tester')
        self.assertGreater(len(other.events()),0)

    def test_confirmed_rename_preserves_id(self):
        self.m.catalog.confirm_rename(self.age['id'],'age_years',1,'tester')
        self.m.catalog.ingest('demo',{'tables':{'t':{'columns':['age_years','name'],'column_details':[{'name':'age_years','type':'INTEGER'},{'name':'name','type':'TEXT'}]}}})
        obj=self.s.require('object',self.age['id']);self.assertEqual(obj['status'],'active');self.assertEqual(obj['name'],'age_years')
        self.assertEqual(sum(x['name']=='age_years' for x in self.s.list('object')),1)

    def test_change_invalidates_mapping_but_old_version_retained(self):
        c=self.concept();m=self.mapping(c)
        self.m.catalog.ingest('demo',{'tables':{'t':{'columns':['age'],'column_details':[{'name':'age','type':'TEXT'}]}}})
        self.assertEqual(self.s.require('mapping',m['id'])['status'],'review_required')
        self.assertEqual(self.s.require('object',self.name['id'])['status'],'deleted')
        self.assertEqual(self.s.require('mapping',m['id'],1)['status'],'confirmed')
        with self.assertRaises(DomainError):self.m.semantic.resolve(c['id'])

    def test_hard_type_conflict_cannot_be_confirmed(self):
        c=self.concept()
        with self.assertRaises(DomainError):self.m.semantic.mapping({'concept_id':c['id'],'fields':[self.name['id']],
            'mapping_type':'direct','status':'confirmed','confirmation_note':'同名'},'tester')
        hit=next(x for x in self.m.semantic.candidates(c['id']) if x['field_id']==self.name['id'])
        self.assertEqual(hit['status'],'rejected')

    def test_impact_traverses_field_mapping_concept(self):
        c=self.concept();m=self.mapping(c)
        impact=self.m.catalog.impact(self.age['id'])
        self.assertIn(m['id'],impact['downstream']);self.assertIn(c['id'],impact['downstream'])

    def test_no_direct_mapping_to_aggregate_parent(self):
        c=self.m.semantic.concept({'name':'机构','meaning':'归集','domain':'demo','data_type':'text','aggregate_only':True},'tester')
        with self.assertRaises(DomainError):self.m.semantic.mapping({'concept_id':c['id'],'fields':[self.name['id']],'mapping_type':'direct'},'tester')

    def test_publish_needs_actual_matching_validation(self):
        s=self.service();s=self.m.services.transition(s['id'],{'target':'review','expected_version':s['version'],'reason':'审核'},'tester')
        with self.assertRaises(DomainError):self.m.services.transition(s['id'],{'target':'trial','expected_version':s['version'],'reason':'试用'},'tester')
        with self.assertRaises(DomainError):self.m.services.validate(s['id'],'tester')
        self.m.services.validator=lambda _: {k:{'passed':True} for k in ('semantic','schema','sample','permission','reachable','consistency')}
        val=self.m.services.validate(s['id'],'tester')
        s=self.m.services.transition(s['id'],{'target':'trial','expected_version':s['version'],'validation_id':val['id'],'reason':'试用'},'tester')
        s=self.m.services.transition(s['id'],{'target':'published','expected_version':s['version'],'validation_id':val['id'],'reason':'发布'},'tester')
        self.assertEqual(s['lifecycle'],'published')
        with self.assertRaises(DomainError):self.m.services.save({**s,'name':'无声替换','expected_version':s['version']},'tester')

    def test_definition_change_invalidates_validation(self):
        s=self.service();self.m.services.validator=lambda _: {k:{'passed':True} for k in ('semantic','schema','sample','permission','reachable','consistency')}
        val=self.m.services.validate(s['id'],'tester')
        s=self.m.services.save({**s,'output':'different','expected_version':s['version']},'tester')
        s=self.m.services.transition(s['id'],{'target':'review','expected_version':s['version'],'reason':'review'},'tester')
        with self.assertRaises(DomainError):self.m.services.transition(s['id'],{'target':'trial','expected_version':s['version'],'validation_id':val['id'],'reason':'trial'},'tester')

    def test_joint_publication_requires_responsibility(self):
        s=self.service()
        with self.assertRaises(DomainError):self.m.services.check_definition({**s,'mode':'joint'})

    def test_manual_confirmation_and_new_demand_version(self):
        d=self.m.intake.save({'title':'研究','object_scope':'成人','time_start':'2026-01-01','time_end':'2026-02-01',
            'purpose':'research','quality':'完整','deadline':'2026-11-01','delivery':'summary'},'demo-requester')
        with self.assertRaises(DomainError):self.m.intake.confirm(d['id'],{'expected_version':1},'platform')
        spec=self.m.intake.confirm(d['id'],{'expected_version':1,'confirmations':{'content':True,'attachments':True,'gaps':True},'note':'已确认'},'platform')
        self.assertFalse(spec['execution_authorized']);self.assertTrue(spec['gaps']['authorization'])
        d=self.s.require('demand',d['id']);self.m.intake.save({**d,'purpose':'changed','expected_version':d['version']},'demo-requester')
        self.assertEqual(self.s.require('demand',d['id'])['state'],'draft')
        self.assertEqual(self.s.require('spec',spec['id'],1)['specification']['purpose'],'research')

    def test_invalid_time_window_rejected(self):
        with self.assertRaises(DomainError):self.m.intake.save({'title':'x','time_start':'2026-03-01','time_end':'2026-01-01'},'tester')

    def test_pair_uses_loaded_version_not_latest(self):
        self.m.catalog.annotate(self.age['id'],{'meaning':'new','expected_version':1},'tester')
        run=self.run_record(observed={'structure':self.age['structure']})
        self.m.reconciliation.run();pair=self.s.require('pair','PAIR-'+run['id'])
        self.assertTrue(pair['paired']);self.assertEqual(pair['deviations'],{})

    def test_missing_version_is_unpaired_not_healthy(self):
        run=self.run_record(object_version=999)
        self.m.reconciliation.run();self.assertFalse(self.s.require('pair','PAIR-'+run['id'])['paired'])
        metrics=self.m.reconciliation.metrics();self.assertEqual(metrics['coverage'],0);self.assertIsNone(metrics['deviation_density'])

    def test_deviation_close_requires_later_matching_evidence(self):
        bad=self.run_record(observed={'structure':{'name':'age','type':'TEXT'}})
        self.m.reconciliation.run();case=self.s.list('case')[0]
        with self.assertRaises(DomainError):self.m.reconciliation.action(case['id'],{'action':'close','note':'fixed','expected_version':case['version'],'evidence_id':bad['id']},'tester')
        good=self.run_record(observed={'structure':self.age['structure']})
        closed=self.m.reconciliation.action(case['id'],{'action':'close','note':'retested','expected_version':case['version'],'evidence_id':good['id']},'tester')
        self.assertEqual(closed['status'],'closed')
        self.run_record(observed={'structure':{}});self.m.reconciliation.run()
        self.assertEqual(self.s.require('case',case['id'])['reopen_count'],1)
        old=self.s.require('case',case['id'])['version'];self.m.reconciliation.run();self.assertEqual(self.s.require('case',case['id'])['version'],old)

    def test_control_not_connected_does_not_claim_paused(self):
        self.run_record(observed={'structure':{}});self.m.reconciliation.run();case=self.s.list('case')[0]
        with self.assertRaises(DomainError):self.m.reconciliation.action(case['id'],{'action':'pause_review','note':'pause','expected_version':1},'tester')
        self.assertEqual(self.s.require('case',case['id'])['status'],'open')

    def test_feedback_and_unimproved_retest_remain(self):
        before=self.run_record(latency_ms=10);after=self.run_record(status='failed',latency_ms=20)
        imp=self.m.feedback.compare({'before':[before['id']],'after':[after['id']],'change_note':'修订'},'tester')
        self.assertFalse(imp['success_rate_improved']);self.assertEqual(imp['retest']['denominator'],1)
        self.assertIsNone(imp['retest']['missing_rate'])
        other=self.run_record(task_scope='different')
        with self.assertRaises(DomainError):self.m.feedback.compare({'before':[before['id']],'after':[other['id']],'change_note':'x'},'tester')

    def test_event_dedup_and_environment_isolation(self):
        r=self.run_record(id='stable');self.run_record(id='stable');self.assertEqual(len(self.e.list()),1)
        other=Store(self.root/'management.db','other');self.assertEqual(other.list('object'),[])

    def test_all_four_deviations_detected(self):
        obj=self.s.save('object',self.age['id'],{**self.age,'semantic_baseline':{'max_distribution_distance':.2,'min_hit_rate':.8}},1)
        for _ in range(3):self.run_record(object_version=obj['version'],latency_ms=10)
        r=self.run_record(object_version=obj['version'],status='failed',latency_ms=100,
            observed={'structure':{},'distribution_distance':.9,'mapping_hit_rate':.1},authorization='revoked',delivered=True)
        _,issues=self.m.reconciliation.assess(r)
        self.assertEqual(set(issues),{'structure','semantic','execution','fulfillment'})

    def test_failed_transaction_leaves_no_partial_versions(self):
        with self.assertRaises(RuntimeError):
            with self.s.atomic():
                self.s.save('concept','rollback',{'name':'temporary'},0)
                raise RuntimeError('abort')
        self.assertIsNone(self.s.get('concept','rollback'))

    def test_external_design_immutable_and_used_by_reconciliation(self):
        data={'object_kind':'workflow','object_id':'flow','object_version':'v1','environment':'demo','definition':{'structure':{'nodes':2}},'dependencies':[self.age['id']]}
        self.m.register_design(data)
        with self.assertRaises(DomainError):self.m.register_design({**data,'definition':{'structure':{'nodes':3}}})
        run=self.run_record(object_kind='workflow',object_id='flow',object_version='v1',observed={'structure':{'nodes':2}})
        design,issues=self.m.reconciliation.assess(run);self.assertIsNotNone(design);self.assertEqual(issues,{})
        self.assertIn('flow',self.m.catalog.impact(self.age['id'])['downstream'])

    def test_local_connector_confinement_and_csv_discovery(self):
        from management.catalog.sources import SourceRegistry
        root=self.root/'sources';root.mkdir();(root/'sample.csv').write_text('age,name\n30,A\n40,B\n')
        registry=SourceRegistry(self.s,root)
        registry.register({'source_id':'csv_demo','connector':'csv','path':'sample.csv'},'tester')
        snapshot=registry.adapters()['csv_demo'].snapshot()
        self.assertEqual(snapshot['tables']['sample']['rows'],2)
        with self.assertRaises(DomainError):registry.register({'source_id':'escape','connector':'csv','path':'../management.db'},'tester')

    def test_fault_injection_and_unversioned_baseline(self):
        from management.reconciliation.experiment import evaluate
        report=evaluate()
        self.assertEqual(report['correct_count'],6)
        self.assertEqual(report['impact_actual'],report['impact_expected'])
        self.assertEqual(report['version_switch_comparison']['baseline_false_positives'],1)
        self.assertEqual(report['version_switch_comparison']['version_paired_false_positives'],0)
