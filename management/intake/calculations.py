"""Supported scalar calculations and catalog-backed diagnosis scopes."""

from platform_core.storage import DomainError
from execution.security.output_policy import field_classification


DIABETES_COUNTS = 'doris_nju__cdm__medfabric_diabetes_counts'
DIABETES_AGE = 'doris_nju__cdm__medfabric_diabetes_age'
DIABETES_AGE_OUTPUTS = ('diagnosed_people','linked_people','age_eligible_people','mean_age_summary')
DIAGNOSIS = 'doris_nju__cdm__medfabric_diagnosis'
DORIS_PEOPLE = 'doris_nju__cdm__medfabric_people_counts'
DORIS_PEOPLE_SCOPES = (
    ('diagnosis', 'Doris 诊断数据集 · 患者人数'),
    ('lab', 'Doris 检验数据集 · 患者人数'),
)
DIABETES_SCOPES = (
    ('diabetes_all', '糖尿病（ICD‑10 E10–E14）', 'E10', 'E15'),
    ('diabetes_type2', '2 型糖尿病（ICD‑10 E11）', 'E11', 'E12'),
    ('diabetes_type1', '1 型糖尿病（ICD‑10 E10）', 'E10', 'E11'),
)
CALCULATION_LABELS = {
    'row_count': '记录数',
    'person_count': '去重人数',
    'diabetes_count': '去重人数',
    'doris_people_count': '去重人数',
}
SUMMARY_TABLES = {
    DIABETES_COUNTS: ('diabetes_count', tuple((key,label) for key,label,*_ in DIABETES_SCOPES)),
    DORIS_PEOPLE: ('doris_people_count', DORIS_PEOPLE_SCOPES),
}
SUMMARY_SCOPES = {kind:dict(scopes) for kind,scopes in SUMMARY_TABLES.values()}


def person_identifier(table, field):
    name=field.get('name','').casefold()
    return (name in ('patient_id','person_id') and field_classification(field)=='identifier'
            or name=='author_id' and table.get('source_id')=='openalex'
            and table.get('table') in ('openalex_authors','openalex_authorships'))


def summary_count_kind(table, field):
    summary=SUMMARY_TABLES.get(table.get('table'))
    if summary and table.get('source_id')=='doris-nju' and field.get('name')=='patient_count':
        return summary[0]
    return None


def validate_summary_scope(kind, filters, cohort_field):
    if (len(filters) != 1 or filters[0]['field'] != cohort_field
            or filters[0]['op'] != '=' or filters[0]['value'] not in SUMMARY_SCOPES[kind]):
        raise DomainError('人数汇总需要选择一个已登记的统计口径')
