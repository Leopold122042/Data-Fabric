"""Field relationships for the graph, derived only from the caller's visible catalog."""

from collections import defaultdict
from unicodedata import normalize


def normalized(text):
    return normalize('NFKC', str(text or '')).strip().casefold()


def graph_relationships(tables, objects, registered, patient_link_is_current):
    by_id = {obj['id']: obj for obj in objects}
    by_field_id = {}
    by_field_key = {}
    by_name = defaultdict(list)
    by_label = defaultdict(list)
    for table in tables:
        physical_name = by_id[table['id']].get('structure', {}).get('physical_name')
        table_key = physical_name or table['name']
        for field in table['fields']:
            member = {'table_id': table['id'], 'field_id': field['id']}
            by_field_id[field['id']] = (table, field, member)
            by_field_key[(table['source'], normalized(table_key), normalized(field['name']))] = field['id']
            by_name[normalized(field['name'])].append(field['id'])
            label = normalized(field['label'])
            if field['status'] == 'confirmed' and label:
                by_label[label].append(field['id'])

    results = []
    exact_groups = set()

    def add(kind, label, note, field_ids):
        visible_ids = list(dict.fromkeys(fid for fid in field_ids if fid in by_field_id))
        members = [by_field_id[fid][2] for fid in visible_ids]
        if len({item['table_id'] for item in members}) < 2:
            return
        signature = frozenset(visible_ids)
        if signature in exact_groups:
            return
        exact_groups.add(signature)
        results.append({'id': f'field-link-{len(results) + 1}', 'kind': kind,
                        'label': label, 'note': note, 'members': members})

    for relation in registered:
        left, right = relation.get('source'), relation.get('target')
        if left not in by_field_id or right not in by_field_id:
            continue
        if relation.get('type') == 'same_patient_id':
            if patient_link_is_current(left, right):
                add('confirmed', 'patient_id', '目录已确认：两个字段的相等值代表同一患者。', [left, right])
        elif relation.get('status') == 'confirmed':
            add('reference', '已登记字段关系', '目录中已登记两个字段间的关系。执行时仍按查询规则校验。', [left, right])

    for table in tables:
        structure = by_id[table['id']].get('structure', {})
        source, table_name = table['source'], structure.get('physical_name') or table['name']
        for foreign_key in structure.get('foreign_keys', []):
            local = by_field_key.get((source, normalized(table_name), normalized(foreign_key.get('from'))))
            target = by_field_key.get((source, normalized(foreign_key.get('table')),
                                       normalized(foreign_key.get('to'))))
            if local and target:
                note = ('OpenAlex 同步定义中的标识参照。被引用论文可能不在当前同步范围内。'
                        if source == 'openalex' else '来源目录声明的参照关系。')
                add('reference', str(foreign_key['from']), note, [local, target])

    # Known relationships where the technical field names differ. Resolve
    # against physical names and current field definitions, never data values.
    named_candidates = (
        (('doris-nju', 'cdm.medfabric_diagnosis', 'icd10_code'),
         ('doris-nju', 'cdm.medfabric_icd10', 'code'),
         'ICD‑10 编码', '诊断编码引用 ICD‑10 字典。抽查发现仅部分编码精确匹配，仍需核对字典版本与细分编码。'),
        (('doris-nju', 'cdm.medfabric_diagnosis', 'icd10_label'),
         ('doris-nju', 'cdm.medfabric_icd10', 'label'),
         'ICD‑10 名称', '两列都描述 ICD‑10 诊断名称。查字典时应先核对编码，名称供比对。'),
        (('his', 'visit', 'diagnosis_code'), ('doris-nju', 'cdm.medfabric_diagnosis', 'icd10_code'),
         '诊断编码', '两侧都是诊断编码，需要核对标准与版本。此关系用于比较编码口径。'),
        (('his', 'visit', 'diagnosis_name'), ('doris-nju', 'cdm.medfabric_diagnosis', 'icd10_label'),
         '诊断名称', '就诊诊断名称和标准字典名称存在语义重叠，需结合编码判断是否同义。'),
        (('lis', 'result', 'item_code'), ('doris-nju', 'cdm.medfabric_lab_item', 'lab_item_code'),
         '检验项目编码', '两侧都是检验项目编码，需要来源系统的编码对照。'),
        (('lis', 'result', 'item_name'), ('doris-nju', 'cdm.medfabric_lab_item', 'lab_item_label'),
         '检验项目名称', '同类业务字段。名称可能存在别名或相同名称指不同项目。'),
        (('lis', 'result', 'unit'), ('doris-nju', 'cdm.medfabric_lab_item', 'lab_unit'),
         '检验单位', '两侧均为检验结果的计量单位，需要结合对应项目和单位口径解释。'),
        (('lis', 'result', 'value_num'), ('doris-nju', 'cdm.medfabric_lab_item', 'result_num'),
         '检验结果数值', '两侧均为数值结果，比较时需要匹配项目及计量单位。'),
        (('lis', 'result', 'flag'), ('doris-nju', 'cdm.medfabric_lab_item', 'result_flag'),
         '检验异常标记', '均描述结果相对参考范围的状态，标记编码和描述文字需要映射。'),
        (('his', 'visit', 'dept'), ('doris-nju', 'cdm.medfabric_diagnosis', 'dept_name'),
         '就诊科室', '均指诊断关联的就诊科室，跨来源时仍需科室字典对照。'),
        (('doris-nju', 'cdm.medfabric_lab_item', 'lab_item_code'),
         ('doris-nju', 'cdm.medfabric_loinc', 'code'),
         '检验项目标准化', '院内检验项目编码和 LOINC 是不同编码，关联需要院内到 LOINC 的对照表。'),
    )
    for left, right, label, note in named_candidates:
        left_id, right_id = by_field_key.get(left), by_field_key.get(right)
        if left_id and right_id:
            add('candidate', label, note, [left_id, right_id])

    for _, field_ids in sorted(by_name.items()):
        if len(field_ids) > 1:
            table_names = {by_field_id[fid][0].get('physical_name') for fid in field_ids}
            note = ('两张表分别采用 ICD‑10 和 LOINC。字段同名，但各自属于不同的编码体系。'
                    if table_names == {'cdm.medfabric_icd10', 'cdm.medfabric_loinc'}
                    else '字段名相同。含义、编码体系和可用的关联条件仍需核对。')
            add('overlap', by_field_id[field_ids[0]][1]['name'],
                note, field_ids)
    for _, field_ids in sorted(by_label.items()):
        if len({normalized(by_field_id[fid][1]['name']) for fid in field_ids}) > 1:
            add('candidate', by_field_id[field_ids[0]][1]['label'],
                '字段库业务名称相同。字段可能是同一维度，也可能采用不同的取值口径。', field_ids)
    return results
