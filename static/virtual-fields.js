/* SQL values and identifiers are quoted separately; only fixed operators are accepted. */
(function(root){
  function build(id, columns, selected, filters){
    const ident=v=>'"'+String(v).replaceAll('"','""')+'"';
    const literal=v=>"'"+String(v).replaceAll("'","''")+"'";
    if(!selected.length)throw new Error('请至少选择一个返回字段。');
    const check=c=>{if(!columns.includes(c))throw new Error('字段不存在，请刷新目录。');return ident(c);};
    const conditions=filters.map(f=>{
      const c=check(f.column);
      if(f.op==='null')return c+' IS NULL';
      if(f.op==='notnull')return c+' IS NOT NULL';
      if(f.op==='eq')return c+' = '+literal(f.value);
      if(f.op==='contains')return 'contains(CAST('+c+' AS VARCHAR), '+literal(f.value)+')';
      if(['>','>=','<','<='].includes(f.op)){
        if(!String(f.value).trim()||!Number.isFinite(Number(f.value)))throw new Error('大小比较需要填写有效数字。');
        return 'TRY_CAST('+c+' AS DOUBLE) '+f.op+' '+Number(f.value);
      }
      throw new Error('不支持的查询条件。');
    });
    return 'SELECT '+selected.map(check).join(', ')+' FROM '+ident(id)+(conditions.length?' WHERE '+conditions.join(' AND '):'')+' LIMIT 200;';
  }
  function groupResults(columns,rows){
    const index=name=>columns.indexOf(name);
    const value=(row,name)=>index(name)<0?'':(row[index(name)]??'');
    const groups=new Map();
    rows.forEach(row=>{
      const path=value(row,'来源文件'),member=value(row,'包内文件'),sheet=value(row,'来源工作表');
      const key=JSON.stringify([value(row,'来源表编号'),path,member,sheet]);
      if(!groups.has(key))groups.set(key,{path,member,sheet,originalRows:[]});
      groups.get(key).originalRows.push(row);
    });
    return [...groups.values()].map(group=>{
      const duplicates={'数据·来源文件':'来源文件','数据·包内文件':'包内文件','数据·工作表':'来源工作表'};
      const keep=columns.map((name,i)=>({name,i})).filter(({name,i})=>{
        if(!name.startsWith('数据·'))return false;
        if(!group.originalRows.some(row=>row[i]!=null&&String(row[i]).trim()!==''))return false;
        // Source information already appears in the group heading.
        if(duplicates[name] && group.originalRows.every(row=>row[i]===value(row,duplicates[name])))return false;
        return true;
      });
      return {path:group.path,member:group.member,sheet:group.sheet,
        columns:keep.map(c=>c.name.slice(3)),rows:group.originalRows.map(row=>keep.map(c=>row[c.i]))};
    });
  }
  root.VirtualFieldQuery={build,groupResults};
  if(typeof module!=='undefined')module.exports=root.VirtualFieldQuery;
})(globalThis);
