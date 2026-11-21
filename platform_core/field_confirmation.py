"""One-time migration for the currently approved demo field dictionary."""
import json
from pathlib import Path

from management.catalog.field_library import FieldLibrary, signature
from platform_core.storage import now


def confirm_current_demo_fields(runtime, marker):
    marker = Path(marker)
    if marker.exists():
        return {'changed': 0, 'already_applied': True}
    store = runtime.management.store
    library = FieldLibrary(store)
    library.sync(actor='demo-platform')
    changed = 0
    with store.atomic():
        for field in store.list('object'):
            if field.get('kind') != 'field' or field.get('status') != 'active':
                continue
            current = store.require('object', field['id'])
            classification = current.get('classification')
            from execution.security.output_policy import field_classification
            classified = field_classification(current)
            if classified not in ('identifier', 'health', 'internal', 'public', 'sensitive', 'aggregate', 'pseudonymized'):
                classified = 'health'
            if classification != classified or not current.get('classification_basis'):
                current = store.save('object', current['id'], {**current,
                    'classification': classified,
                    'classification_basis': '平台方一次性确认当前演示数据字段分类'},
                    current['version'], 'demo-platform', '一次性确认当前演示字段分类')
            definition = store.get('field_definition', field['id'])
            if not definition:
                continue
            updated = {**definition,
                'label': definition.get('label') or field.get('name', ''),
                'definition': definition.get('definition') or current.get('meaning') or
                              f'字段“{field.get("name", "") }”的业务含义按当前演示数据源结构定义。',
                'basis': definition.get('basis') or '当前演示数据源结构及平台方一次性确认',
                'status': 'confirmed', 'source_signature': signature(current),
                'source_id': current.get('source_id'), 'table': current.get('table'),
                'name': current.get('name'), 'data_type': current.get('structure', {}).get('type'),
                'confirmed_by': 'demo-platform', 'confirmed_at': now(),
                'confirmation_note': '平台方一次性确认当前全部演示数据字段；查询时不再重复要求字段确认。'}
            store.save('field_definition', field['id'], updated, definition['version'],
                       'demo-platform', '平台方一次性确认当前演示数据字段')
            changed += 1
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'version': 1, 'confirmed_fields': changed,
                                  'applied_at': now()}, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    return {'changed': changed, 'already_applied': False}
