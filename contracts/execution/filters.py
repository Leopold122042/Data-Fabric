"""Binding for the comparison operators supported at each source."""
import math

from platform_core.storage import DomainError

COMPARISONS={'=','!=','<','<=','>','>='}
OPERATORS=COMPARISONS|{'in','prefix'}


def scalar(value):
    return (type(value) in (str,int,float)
            and (not isinstance(value,float) or math.isfinite(value)))


def filter_values(operator,value):
    if not isinstance(operator,str) or operator not in OPERATORS:
        raise DomainError('筛选支持等值、范围、集合和前缀，请核对当前筛选方式')
    if operator=='in':
        if not isinstance(value,list) or not 1<=len(value)<=50 or not all(
                scalar(item) and (not isinstance(item,str) or item.strip()) for item in value):
            raise DomainError('集合筛选需要 1 至 50 个明确值')
        return value
    if not scalar(value):
        raise DomainError('条件需要明确的文本或有限数值')
    if operator=='prefix' and (not isinstance(value,str) or not value or len(value)>128
                                or not all(char.isalnum() or char in '.-' for char in value)):
        raise DomainError('前缀条件需要明确的编码或文字前缀')
    return [value]


def bind_filter(column,operator,value,placeholder):
    values=filter_values(operator,value)
    if operator=='in':
        return f"{column} IN ({','.join([placeholder]*len(values))})",values
    if operator=='prefix':
        return f"SUBSTR({column},1,{placeholder}) = {placeholder}",[len(value),value]
    return f'{column} {operator} {placeholder}',values
