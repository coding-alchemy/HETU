# 中国宏观离线合成材料

## 1. 必要性与复用

旧 material_cases/shared.md、B02／03／04／08／11／12／14／15／17、publication-review 及消费者主要处理公司、证券或行业，缺宏观全景、重叠债务、财政执行、私人信用及地产分化。本次独立新增合成事实 F01–F07，各仅维护一份，不改名复用个股数字；旧原件、66项catalog、构造器和消费者全部保留。

复用 [material_fixtures.py](../../skill/material_fixtures.py) 的 materialize_input(case, variant, stage, output, *, root=...)，root 指向本目录绝对路径。直接消费者是本主题离线研究执行者和独立评审者。生成产出task.md、material.md、selection.json三个基本文件及catalog为当次选择声明的附件，不执行研究，不成为产品依赖。

## 2. 显式选择与边界

10个选择：CM01/main/01、CM02/main/01、CM02/main/02、CM03/main/01、CM03/missing/01、CM04/main/01、CM05/readonly/01、CM05/save-failure/01、CM06/excluded/01、CM06/ambiguous/01。

CM02/main/02=context continue，仅续派原研究会话；其余new，独立新上下文。缺初值变体仅F06，不能泄漏F05；未知选择写入前失败。执行者只读当次三个基本输入及catalog声明的附件、明确提供的Skill包及授权输出，不能读源facts/tasks、catalog、本README或review-expectations。评审侧读取原件、标准和实际记录；不能把评分答案加入派发。

CM05 readonly使用真实只读沙箱；save-failure在工作区把delivery建为内容“保留的普通文件”的普通文件，保留障碍验证失败回退。

## 3. 调用与验证

在仓库根使用现有Python环境：

```python
from pathlib import Path
from tests.product.skill.material_fixtures import materialize_input
materialize_input("CM01", "main", "01", Path("/tmp/新的输出目录"),
                  root=Path("tests/product/fixtures/china_macro").resolve())
```

首版10选择、未知选择、评分路由污染与缺初值的已执行[核验命令](../../../../.hetu/validation/china-macro-fundamentals/input-check-command.py.txt)保留；生成正确不能替代真实行为与宿主隔离。事实标注为合成，定位用块号／表号而非伪造机构URL。

## 4. CM07 增量审批行为

仅追加F08/F09独立合成原始记录和T07/T07B/T07C，原十选择保持。新增CM07/main/01、CM07/main/02、CM07/declined/01，该阶段共13选择。main/02续派main/01原会话且仅增加已批准范围的合成CSV；declined/01使用新上下文，不继承另一会话的授权和数字。合成批准没有真实采购/付费操作授权效力。

消费者沿用现有materialize_input与root参数；当次只发task.md、material.md、selection.json及Skill。源bundle/catalog/本入口/评分和计划留在开发或独立评审侧。输入逐字保留与未知/污染负例见已执行[增量核验命令](../../../../.hetu/validation/china-macro-fundamentals/completion/input-check-command.py.txt)；三轮短判断不代替真实付费接入或宏观研究验收。

01–06执行计划已完成并按用户授权清理，原文见[历史回源](../../../../specs/plans/README.md#china-macro-closeout)。这些保存命令是历史核验依据，不是研究运行授权；当前构造器调用及原场景条件保持。

## 5. CM08／CM09 研究深度增量

截至CM09共16个选择。CM08/main/01复用F01–F04，追加市场产品与事前预测、银行与居民原始记录及资本和家庭原表；验证市场反馈、银行约束、财政落点和居民竞争性解释。CM08/limited/01复用F01/F03/F11，以定义不匹配的市场资料及晚发资本和家庭记录表达必要差异，不包含F04/F10/F12。

CM09/main/01仅使用F15和官方档案副本，验证入口失败后的原表采用及另一缺口的具体下一动作。仅CM09携带depth_bank_release.txt附件，通过既有assets接口提供；其他15个选择不带附件。三者均为新上下文，数字、机构和产品全为合成，无真实商业操作授权。

```python
materialize_input("CM09", "main", "01", Path("/tmp/新的输出目录"),
                  root=Path("tests/product/fixtures/china_macro").resolve())
```

输入边界及污染负例见[test_china_macro_depth_inputs.py](../../skill/test_china_macro_depth_inputs.py)。评审标准仅在[评审侧](review-expectations.md)，不随当次输入开放；原13选择及原评审标准保持，材料测试不证明研究行为或联网取证。

## 6. CM10 公开子范围取证

当前共18选择。CM10/main/01复用T01及F01/F03/F04/F10，CM10/restricted/01复用T02及F03/F04；二者只补F16的财政原文与T10离线档案环境。未复用F02，因为它已直接给平台债发行/偿还和全融资数，不能验证全口径缺失时是否主动发现子范围。

当次档案以library.md为总入口，再由r210.html定位同篇原发布r211.html及转载r212.html。目标表未放入task/material，目录无数值或研究答案，研究者须实际读取档案。所有内容为独立合成，不继承真实失败报告的文章、数值或结论。

消费者沿用materialize_input及root/assets接口；生成后在当次工作区将r211.html设为000只读访问拒绝，restricted再将r212.html设为000；main的r212.html保持可读。执行前由同用户读取确认实际PermissionError并记录权限；研究者仅获读取input和包、写out授权，不能chmod、更换路径绕过或联网。原厂与替代载体失败通过真实文件权限触发，不是给研究者一份预写的失败结论；它只证明离线档案环境中的失败处理。

两个选择均新上下文，各先执行一次。仅当次生成目录及包进入研究输入，源catalog/本README/评分及修复说明留评审侧；原16选择、构造器和旧消费者保持。仅修改场景输入不能替代工具事件和实际判断审阅。

## 7. CM11 传导分析增量

当前共21选择；§6的18为CM10交付时的历史数量。CM11/main/01、CM11/limited/01、CM11/scope/01均为新上下文，依次各首次一次；main为综合全景，limited为历史可知及局部缺证短专题，scope为人民币兑欧元政策传导短专题。原18选择与原标准保持。

沿用materialize_input及root/assets接口：T10环境复用，main/limited/scope分别追加T11/T11L/T11S；原事实显式选择，不改通用构造器。新增transmission-library.md总入口、transmission-release.md综合发布、transmission-limited.md晚发与缺比较版本、transmission-policy.md政策档案四份附件；两综合版本按选择只提供其中一份，目标名同为m310.md。政策统一生成m320.md，复用public-netfin.html为r212.html，用于公开债券子范围；每项总共7个输入文件。

研究者只收当次生成目录和受测四文件包，中性任务不摘录应有结论；评分、源bundle/catalog、本README、计划和其他场景产物留在评审侧。输入边界与污染负例见[test_china_macro_transmission_inputs.py](../../skill/test_china_macro_transmission_inputs.py)。这些测试只证明材料边界，实际读取与重要事实采用另审工具和完整输出。

## 8. CM12 证据采用与季度判断增量

当前共24选择；§7的21为CM11交付时的历史数量。CM12/main/01、CM12/limited/01、CM12/scope/01均为新上下文，分别为综合全景、增长与政策受限短专题、人民币兑欧元短专题。原21条目、生成字节及原评分保持。

沿用materialize_input及root/assets接口，组合T10与T12/T12L/T12S、F17/F03/F04/F10/F11/F12/F16，不使用已直接给名义GDP增速的F01；交叉引用的全国收支、就业与PMI由F17提供。七份integration-*.md附件分别维护档案入口、完整／受限运行发布、完整／受限政策发布及两份历史核算档案。按选择只提供其中适用版本，另复用transmission-policy.md与public-netfin.html。每项10个输入文件，档案入口不直接给历史基数，相关原文可沿m410→m430→m431实际查阅。

中性任务不列应有结论。评分、源bundle/catalog、本README、设计计划及其他运行答案留评审侧，仅当次生成输入和受测四文件包交研究者。[输入边界测试](../../skill/test_china_macro_integration_inputs.py)覆盖三选择、两种完整附件污染、评分路由及未知选择；原文人工核对与实际研究判断另行记录，不能以输入测试代替行为验收。


## 9. CM13 历史对照与跨年化债

当前26个选择；CM13/main/01与CM13/limited/01使用同一materialize_input及root参数，均context=new，合成截止2026-11-20T12:00:00+08:00。每项10文件：task.md、空material.md、selection.json、library.md、m500.md、m510.csv、m520.csv、m530.csv、m540.md、m550.md。main使用完整月表，limited使用缺期/版本断点月表；季度及年度表、正式多年方案和执行披露复用。

源catalog、评分、本README及其他研究结果留在评审侧；研究者只获当前生成目录及四文件Skill。原24选择和原评分保持，新输入边界与污染负例见[test_china_macro_history_inputs.py](../../skill/test_china_macro_history_inputs.py)。材料为合成，不是真实统计或商业授权；输入检查不代替实际研究。
