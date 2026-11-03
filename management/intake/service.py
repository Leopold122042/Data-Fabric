from datetime import datetime
from management.store import DomainError,new_id
from contracts.execution.constraints import has_cost_constraint

REQUIRED=('object_scope','time_start','time_end','purpose','quality','deadline','delivery')


class Intake:
    def __init__(self,store):self.store=store

    def _cost_source(self,demand):
        """Resolve cost provenance, including legacy records without a marker."""
        if demand.get('cost_source') in ('manual','model','none'):
            return demand['cost_source']
        history=self.store.history('demand',demand['id'])
        previous=object();source='none'
        for item in reversed(history):
            value=item.get('cost')
            if value!=previous:
                if not has_cost_constraint(value):
                    source='none'
                elif item.get('cost_source') in ('manual','model','none'):
                    source=item['cost_source']
                elif (item.get('change_reason')=='记录LLM结构化草案，待规则校验及人工确认'
                      and value==(item.get('llm_draft') or {}).get('cost')):
                    source='model'
                else:
                    source='manual'
                previous=value
            elif item.get('cost_source') in ('manual','model','none'):
                source=item['cost_source']
        return source

    def _model_cost_was_requested(self,demand):
        if demand.get('cost_explicit') is not None:
            return demand.get('cost_explicit') is True
        # Legacy model drafts predate explicit provenance. Recover the original
        # natural-language request from its flow record before deciding whether
        # the model's number is a real user constraint.
        from management.intake.ollama import explicitly_mentions_cost
        matches=[flow for flow in self.store.list('intake_flow')
                 if flow.get('demand_id')==demand['id']
                 and (flow.get('draft') or {}).get('cost')==demand.get('cost')]
        matches.sort(key=lambda flow:(flow.get('updated_at',''),flow.get('version',0)))
        if not matches:
            return None
        return explicitly_mentions_cost(matches[-1].get('request_text',''))

    def _latest_flow_mentions_cost(self,demand):
        from management.intake.ollama import explicitly_mentions_cost
        flows=[flow for flow in self.store.list('intake_flow') if flow.get('demand_id')==demand['id']]
        flows.sort(key=lambda flow:(flow.get('updated_at',''),flow.get('version',0)))
        if not flows:
            return None
        return explicitly_mentions_cost(flows[-1].get('request_text',''))

    def save(self,body,actor):
        if not body.get('title'):raise DomainError('需求标题必填')
        if body.get('entry','goal') not in ('goal','service'):raise DomainError('入口应为goal或service')
        if body.get('time_start') and body.get('time_end'):
            try:
                if datetime.fromisoformat(body['time_start'])>datetime.fromisoformat(body['time_end']):raise DomainError('时间窗起点不得晚于终点')
            except (ValueError,TypeError):raise DomainError('时间窗格式或范围非法')
        oid=body.get('id') or new_id('DEM');old=self.store.get('demand',oid)
        if old and old.get('state')=='confirmed':
            # New demand version invalidates confirmation; immutable specs remain historical.
            body={**body,'supersedes_version':old['version']}
        data={k:v for k,v in body.items() if k not in ('confirmed_by','confirmation','spec_id','state','cost_source','cost_explicit')}
        data['state']='draft';data['created_by']=(old or {}).get('created_by',actor)
        old_cost=(old or {}).get('cost')
        if old and 'cost' not in data and 'cost' in old:
            data['cost']=old_cost
        if old:
            cost_source=self._cost_source(old)
            if 'cost' in data and data.get('cost')!=old_cost:
                cost_source='manual' if has_cost_constraint(data.get('cost')) else 'none'
                data['cost_explicit']=has_cost_constraint(data.get('cost'))
            elif 'cost_explicit' in old:
                data['cost_explicit']=old['cost_explicit']
        else:
            cost_source='manual' if has_cost_constraint(data.get('cost')) else 'none'
            data['cost_explicit']=has_cost_constraint(data.get('cost'))
        if 'cost' in data and not has_cost_constraint(data.get('cost')):
            data['cost']=''
            cost_source='none'
            data['cost_explicit']=False
        data['cost_source']=cost_source
        data['requester_id']=(old or {}).get('requester_id') or (
            actor if actor=='demo-requester' else 'demo-requester' if actor=='demo-platform' else actor)
        return self.store.save('demand',oid,data,body.get('expected_version',0),actor,'需求草稿')

    def gaps(self,oid):
        d=self.store.require('demand',oid)
        result={'fields':[k for k in REQUIRED if not d.get(k)],'services':[], 'authorization':[], 'quality':[]}
        if d.get('entry')=='service' and not d.get('service_ids'):result['services'].append('未选择服务')
        for sid in d.get('service_ids',[]):
            s=self.store.get('service',sid)
            if not s or s.get('lifecycle')!='published':result['services'].append(sid+'未发布或已退出')
        if not d.get('authorization_evidence'):result['authorization'].append('尚无外部授权证据；受理确认不等于执行授权')
        if not d.get('quality'):result['quality'].append('未明确质量要求')
        if not d.get('execution_constraints'):
            result['fields'].append('execution_constraints')
        return result

    def apply_draft(self,oid,body,actor):
        """Attach an extracted or rule-generated proposal; never confirms or creates a spec."""
        with self.store.atomic():
            d=self.store.require('demand',oid)
            if d['version']!=body.get('expected_version'):raise DomainError('需求版本已变化',409)
            proposal=body.get('proposal')
            if not isinstance(proposal,dict):raise DomainError('缺少结构化草案')
            allowed=set(REQUIRED)|{'service_ids','cost','attachments','execution_constraints','entry'}
            changes={k:v for k,v in proposal.items() if k in allowed and v is not None}
            prior_cost_source=self._cost_source(d)
            proposed_cost=proposal.get('cost')
            if not has_cost_constraint(proposed_cost):
                changes.pop('cost',None)
                # Remove only model-generated cost guesses. A manually entered cost
                # remains part of the demand even if a new draft omits it.
                if (prior_cost_source=='model' and self._model_cost_was_requested(d) is False) or not has_cost_constraint(d.get('cost')):
                    changes['cost']=''
                    changes['cost_source']='none'
                    changes['cost_explicit']=False
                elif prior_cost_source=='manual':
                    changes['cost_source']='manual'
            elif proposed_cost==d.get('cost') and prior_cost_source=='manual':
                changes['cost_source']='manual'
            else:
                changes['cost_source']='model'
                changes['cost_explicit']=body.get('cost_explicit') is True
            # Preserve the human's original description and record the model output as a proposal.
            reason=('目录规则生成缺口草案，待人工核对' if body.get('model')=='platform-catalog-gap-rule'
                    else '记录LLM结构化草案，待规则校验及人工确认')
            updated=self.store.save('demand',oid,{**d,**changes,'llm_draft':proposal,'llm_draft_model':body.get('model'),
                'state':'draft'},d['version'],actor,reason)
            return {'demand':updated,'gaps':self.gaps(oid),'draft':proposal}

    def confirm(self,oid,body,actor):
        with self.store.atomic():
            d=self.store.require('demand',oid)
            if d['version']!=body.get('expected_version'):raise DomainError('需求版本已变化',409)
            if d['state']=='confirmed':raise DomainError('该版本已经确认',409)
            gaps=self.gaps(oid)
            if gaps['fields']:raise DomainError('必填规格尚不完整：'+', '.join(gaps['fields']))
            confirmations=body.get('confirmations',{})
            if not all(confirmations.get(k) is True for k in ('content','attachments','gaps')):raise DomainError('需逐项确认内容、附件及缺口')
            if not body.get('note'):raise DomainError('需填写确认及修改说明')
            cost_guess_removed=False
            if (not has_cost_constraint(d.get('cost')) and d.get('cost_explicit') is None
                    and self._latest_flow_mentions_cost(d) is True):
                raise DomainError('原始需求明确提出预算/成本约束，但草案没有提取出具体条件；请先补充金额和单位，再确认规格。当前成本预算执行适配器未接入，仍不能按预算执行。',409)
            if (has_cost_constraint(d.get('cost')) and self._cost_source(d)=='model'
                    and self._model_cost_was_requested(d) is False):
                d=self.store.save('demand',oid,{**d,'cost':'','cost_source':'none','cost_explicit':False},
                    d['version'],actor,'清除原始需求未提出的模型成本猜测')
                cost_guess_removed=True
            from contracts.execution.constraints import normalize
            execution_policy=normalize(d)
            quality_unverified=execution_policy.get('quality_verification')=='unverified'
            if quality_unverified and confirmations.get('quality_requirement_reviewed') is not True:
                raise DomainError('需确认：本次质量要求会保留在规格中，但目前仅自动检查完整率；其他质量要求不会自动验证')
            if confirmations.get('execution_constraints') is not True:
                raise DomainError('需核对并确认范围筛选、时间字段和交付执行条件')
            spec_id='SPEC-'+oid;previous=self.store.get('spec',spec_id)
            spec=self.store.save('spec',spec_id,{'name':d['title']+' · 任务规格','demand_id':oid,'demand_version':d['version'],
                'specification':{**{k:d.get(k) for k in (*REQUIRED,'service_ids','cost','attachments','execution_constraints')},
                    'query_draft':body.get('query_draft')},
                'gaps':gaps,'confirmed_by':actor,'confirmation_note':body['note'],
                'quality_review':({'requirement':execution_policy['quality_requirement'],
                    'automated_check':'仅检查完整率，阈值 '+str(execution_policy['quality_min_complete']),
                    'other_requirements_verified':False,'acknowledged':True} if quality_unverified else
                    {'requirement':d.get('quality'),'automated_check':'完整率','other_requirements_verified':True,'acknowledged':True}),
                'cost_review':({'status':'unrequested_model_guess_removed','acknowledged':True}
                    if cost_guess_removed else {'status':'no_unrequested_model_guess','acknowledged':True}),
                'query_draft_confirmed':body.get('query_sql_confirmed') is True,
                'confirmed':True,'execution_authorized':False},previous['version'] if previous else 0,actor,'人工确认任务规格')
            self.store.save('demand',oid,{**d,'state':'confirmed','spec_id':spec_id,'spec_version':spec['version']},d['version'],actor,'人工受理完成')
            return spec
