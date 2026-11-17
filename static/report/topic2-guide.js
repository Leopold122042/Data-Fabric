const steps=[
 ['sources','01 接入与目录','核对三源结构，再点“登记本案例目录快照”。展开右侧“数据对象”。','看到医院、医保、科研各24条合成记录的结构预览；对象包含主体、用途、到期日及版本。','创新点一：数据对象携带约束'],
 ['semantics','02 语义与关联','点“运行多证据匹配”，核对下方身份关联及诊断编码表，再展开“统一业务概念”。','看到患者引用、就诊引用及三个来源诊断码的映射依据；不按患者姓名关联。','多证据语义匹配与统一概念'],
 ['virtual','03 合同与逻辑视图','保留或编辑合同条款 → 生成策略对象草案 → 检查合规用途访问 → 模拟跨用途访问 → 模拟超期访问。','依次出现放行、用途不符拦截、期限不符拦截；下方逻辑视图展示连接键、筛选和输出口径。','创新点二：合同条款转策略并校验'],
 ['virtual','04 路径选择','把敏感等级依次切换为低敏汇总、健康信息、直接身份标识，每次点“推荐执行路径”。最后再点“检查合规用途访问”。','三种等级分别显示源端聚合、受控计算、限制标识汇出；最后恢复合规请求，为下一步执行准备。','敏感等级驱动路径决策（路径模拟）'],
 ['orchestration','05 正常执行','场景选择“正常完成” → 点“确认需求并编排” → 逐一展开“步骤详情”。','看到4步状态和耗时；下方显示从本案例输入算出的患者数、结算额和小样本抑制结果。','需求确认 → 编排 → 计算 → 交付'],
 ['orchestration','06 等待与重试','选择“源数据未就绪后恢复”运行一次；完成后选择“中间步骤失败后重试”再运行一次。','先出现等待后恢复；再出现第3步重试和第2次尝试。证据记录保留两种过程。','异常等待、有限重试及节点追踪'],
 ['feedback','07 变更与局部调整','点“注入变更并完成局部处置”，核对下方金额单位变化表。','金额由元变为分 → 单位偏差 → 定位费用汇总视图 → 除以100归一 → 保存v2草案；患者计数不变。','创新点四：变化发现、血缘定位和局部回写'],
 ['feedback','08 证据链收尾','点“查询全程证据链”，再依次点击右侧五类中间对象。','回看登记、语义、策略、访问放行、两种拦截、执行计量、等待、重试及变更处置记录。','创新点三：全程证据追踪']
];
export function guideHtml(state){
 const done=[state.catalogRegistered,state.semanticMatched,Boolean(state.accessChecks?.allow&&state.accessChecks?.purpose&&state.accessChecks?.expired),state.routeChecks?.size===3,state.completedModes?.has('normal'),Boolean(state.completedModes?.has('wait')&&state.completedModes?.has('retry')),state.feedback.complete,state.evidenceVisible];
 return `<section class="case-guide"><header><div><h2>课题二 · 现场操作路线</h2><p>按下列8步演示；点击“进入操作”切换到对应阶段。完成情况随实际点击更新。</p></div><button class="action-button" data-guide-reset>重新开始本页演示</button></header><div class="guide-steps">${steps.map((s,i)=>`<article class="guide-step ${done[i]?'done':''}"><h3>${s[1]} <small>${done[i]?'已完成':'待操作'}</small></h3><p><b>怎么操作：</b>${s[2]}</p><p><b>核对结果：</b>${s[3]}</p><footer><span>${s[4]}</span><button class="action-button" data-guide-stage="${s[0]}">进入操作</button></footer></article>`).join('')}</div><details><summary>当前能证明的能力与尚需接入的能力</summary><p>此路线证明本页合成案例的操作链路、浏览器内计算、策略判断和证据展示。医院/医保/科研连接器、跨机构身份对齐、真实沙箱及联邦节点、持久审计证据和自动语义偏差检测，不能由本页动画证明。实际平台入口位于各阶段底部；本页仍采用场景词典和预设变更，不能宣称这些生产能力已通过验收。</p></details></section>`;
}
