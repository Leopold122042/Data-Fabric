"""Read-time SQLite fields derived from a registered birth date."""
from management.catalog.field_library import FieldLibrary


AGE_FIELDS = {
    'age_years': ('INTEGER','年龄（岁）','完整出生日期计算周岁，只有出生年份时按年份差估算。'),
    'age_basis': ('TEXT','年龄计算口径','说明本行年龄来自完整出生日期还是出生年份估算。'),
}


def with_derived_age(snapshot):
    tables={}
    for name,info in snapshot.get('tables',{}).items():
        tables[name]=info
        cols=info.get('columns',[])
        if 'birth_date' not in cols or set(cols)&set(AGE_FIELDS) or not info.get('column_details'):
            continue
        derived=[{'name':key,'type':dtype,'comment':description,'derived_from_birth_date':True}
                 for key,(dtype,_,description) in AGE_FIELDS.items()]
        tables[name]={**info,'columns':[*cols,*AGE_FIELDS],
                      'column_details':[*info['column_details'],*derived]}
    return {**snapshot,'tables':tables}


def confirm_synthetic_ages(store):
    library=FieldLibrary(store)
    actor='demo-platform'
    for field in store.list('object'):
        if (field.get('source_id')!='his' or field.get('table')!='patient'
                or field.get('status')!='active' or field['name'] not in AGE_FIELDS
                or not field.get('structure',{}).get('derived_from_birth_date')):
            continue
        definition=store.require('field_definition',field['id'])
        if definition['status']=='confirmed' and field['classification']=='health':
            continue
        _,label,description=AGE_FIELDS[field['name']]
        store.save('object',field['id'],{**field,'classification':'health',
            'classification_basis':'模拟患者年龄派生字段'},field['version'],actor)
        saved=library.save(field['id'],{'expected_version':definition['version'],
            'label':label,'definition':description,'aliases':['周岁'] if field['name']=='age_years' else [],
            'basis':'本地模拟患者出生日期和派生规则'},actor)
        library.save(field['id'],{'expected_version':saved['version'],
            'confirmation_note':'模拟源的派生年龄已核对，保留每行计算口径。'},actor,confirm=True)


def attach_age_view(connection,table,fields,quote):
    if not any(field.get('structure',{}).get('derived_from_birth_date') for field in fields):
        return
    birthday="TRIM(CAST(\"birth_date\" AS TEXT))"
    today="date('now','+8 hours')"
    year=f"CAST(SUBSTR({birthday},1,4) AS INTEGER)"
    year_age=f"(CAST(SUBSTR({today},1,4) AS INTEGER)-{year})"
    exact=(f"{birthday} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND "
           f"date({birthday},'+0 days')={birthday} AND {birthday}<={today} AND {year_age} BETWEEN 0 AND 130")
    estimate=(f"({birthday} GLOB '[0-9][0-9][0-9][0-9]' OR {birthday} GLOB '[0-9][0-9][0-9][0-9]-') "
              f"AND {year_age} BETWEEN 0 AND 130")
    age=(f"CASE WHEN {exact} THEN {year_age}-(SUBSTR({today},6,5)<SUBSTR({birthday},6,5)) "
         f"WHEN {estimate} THEN {year_age} END")
    basis=f"CASE WHEN {exact} THEN '按完整日期计算周岁' WHEN {estimate} THEN '按出生年份估算' ELSE '出生日期缺失或无效' END"
    connection.execute(f'CREATE TEMP VIEW {quote(table)} AS SELECT *, {age} AS age_years, '
                       f'{basis} AS age_basis FROM main.{quote(table)}')
