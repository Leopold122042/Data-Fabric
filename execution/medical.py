"""Reconstructed fictional 12-person fixture, not the report's original records."""
import sqlite3


def aggregate(min_group=5):
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE people(record_id TEXT,age INTEGER);CREATE TABLE diagnosis(record_id TEXT,category TEXT);')
        ages=[32]*3+[44]*5+[57]*4
        db.executemany('INSERT INTO people VALUES(?,?)',[(f'F{i:02}',age) for i,age in enumerate(ages)])
        db.executemany('INSERT INTO diagnosis VALUES(?,?)',[(f'F{i:02}','类别甲' if i<8 else '类别乙') for i in range(12)])
        rows=db.execute("SELECT CAST(age/10 AS INT)*10,category,COUNT(*) FROM people JOIN diagnosis USING(record_id) GROUP BY 1,2 HAVING COUNT(*)>=?",(max(5,min_group),)).fetchall()
    return {'columns':['age_group','category','count'],'rows':[[f'{a}—{a+9}岁',c,n] for a,c,n in rows],
            'notice':'部分分组未满足输出条件','fixture':'新建虚构样例；仅用于本地演示'}
