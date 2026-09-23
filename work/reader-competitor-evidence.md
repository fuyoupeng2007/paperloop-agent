# 论文阅读工具官方功能证据与差距

核对日期：2026-09-22。范围：SciSpace、Scholarcy、Elicit；先搜索官方页面，再打开核对正文。以下是官方材料能够证明的功能，不是实际付费账号测试或效果评测；不比较价格。旧公告与当前功能页交叉使用，不能据此保证所有账号的界面一致。

## 精简横向对照

| 工具 | 划词、术语与阅读问答 | 图表放大和局部交互 | 多篇横向比较 |
| --- | --- | --- | --- |
| SciSpace | 官方说明支持高亮不熟悉的术语、缩写、段落后解释；当前 ChatPDF 页面也列出高亮解释、带引用的问答与追问。 | 官方教程明确支持裁剪公式后逐步解释、裁剪表格后解释数据与上下文。已读材料未明确证明独立图片放大器或单柱语义点击。 | Data Extraction 页面提供多篇按统一或自定义字段抽取、比较以及导出，结果有来源引用。 |
| Scholarcy | 结构化摘要卡片包含关键概念，可点击查询定义；Dig Deeper 支持问答。概念定义链接不能直接等同于任意划词即时 AI 解释。 | 当前用户指南明确：提取图片与图注，在 Figures 中点击图片展开；表格可以导出到 Excel。能否成功提取取决于文档。 | 官方功能页说明可以把多篇卡片导出为 Excel 研究综合矩阵，对比发现、结果、参与者等；不能因此推断所有对比均在应用内交互完成。 |
| Elicit | 已读官方材料主要证明研究检索、结构化抽取和论文问答；没有核实阅读器里的任意划词解释浮层。 | 抽取列可以读取表格，复杂布局可能失败。图像、图表、示意图抽取有套餐和设置限制：帮助页明确为 Scale/Enterprise，并在系统综述设置启用 Premium PDF parsing 与 Extract from figures。 | 当前 Library 支持选择论文建立自定义列抽取表；官方 Notebooks 公告明确多篇同时聊天和比较；系统综述结果强调原文证据。 |

### SciSpace 原始证据

- [ChatPDF 当前产品页](https://scispace.com/chat-pdf)：带引用的答案、分节摘要、高亮解释及追问。
- [官方 Copilot/ChatPDF 功能介绍](https://scispace.com/resources/introducing-copilot-ai-assistant-explains-research-papers/)：高亮术语、缩写、段落；裁剪公式和表格后解释；来源定位。此文最初发表于 2023 年，关于高亮解释的表述同时由当前 ChatPDF 产品页支撑。
- [Data Extraction 产品页](https://scispace.com/extract-data)：自定义比较字段、跨论文抽取、引用以及表格导出。

### Scholarcy 原始证据

- [当前用户指南：Supercharge your reading](https://help.scholarcy.com/guide/supercharge-your-reading)：概念定义、卡片导航、图片提取及点击展开、表格 Excel 导出。
- [官方功能页](https://www.scholarcy.com/scholarcy-features)：Dig Deeper 问答、前人研究比较，以及 Literature synthesis matrix 导出。
- [官方 FAQ](https://www.scholarcy.com/faq)：图片提取默认关闭，可在 Library Settings 的导入设置启用。不要把没有显示图片直接理解成不支持图片功能。

### Elicit 原始证据

- [Library 产品页](https://elicit.com/solutions/library)：导入 PDF、选定多篇论文、自定义抽取列。
- [官方 Notebooks 公告](https://elicit.com/blog/notebooks)：上传论文与检索结果组合、多篇同时聊天比较；发表于 2024-03-29，是官方已公布能力证据，不能据此保证今天每种账号入口相同。
- [Systematic Review 产品页](https://elicit.com/solutions/systematic-review)：跨论文定性和定量抽取，结果对应原文引文或图表。
- [帮助页：从表格或图片抽取数据](https://support.elicit.com/en/articles/14758168-extracting-data-from-a-table-or-figure-within-a-paper-in-column-answers)：表格布局限制、Premium PDF parsing，以及图像抽取的套餐和启用条件。此页是限制条件的直接证据，不据此推测价格。

## “点击柱状图某一根柱子”必须独立表述

三家已查阅的官方页面都没有明确证明：用户直接点击一根柱子，系统自动命中该柱子的类别、数值和图例关系，再绑定该图形元素继续追问。

应写“本轮官方资料未核实”，不能写“这些产品不支持”。图片展开、区域裁剪识图、整图数据抽取、单图形元素语义命中是不同能力。已有裁剪解释或图表抽取不能作为单柱点击已实现的证据。

## 与本应用现有实现的差距

仅只读核对本地代码；没有修改程序或调用模型。

1. **划词已经能采集，但缺少直接、稳定的交互。** `outputs/paper-reader/src/main.tsx` 原 PDF 的 `onMouseUp` 会取 `window.getSelection()` 文本并关联块；“问这段”和“解释”主要仍以整段为单位，解释动作传入空选中文字。缺口是划词后就地出现动作、保留选词及所在句上下文、明确标示追问锚点，以及译文区同样可操作。因此不能描述成完全不支持选中文本，也不能描述成已经有完整的划词解释体验。
2. **已有原 PDF 缩放、框选视觉问答，缺少独立看图体验。** `src/main.tsx` 有整页缩放与归一化区域框选，框选后带入图表或公式问题；不是独立提取图片列表、单图放大和平移，也不是单柱自动识别。优先补独立图表展开、清楚的裁剪区域、区域内连续追问。单柱语义识别需另外验证，不宜首版承诺可靠自动取数。
3. **书架存多篇不等于多篇比较。** `backend/app.py` 的问答路由及证据选择围绕单个 `doc_id`，当前没有选择多篇后按共同字段对齐、逐格标引用和跨文档追问的工作流。应补少量多篇选择、可编辑比较字段、每格原文页码/引文、缺失证据及冲突标记，再加跨篇追问。

## 规划建议（我们的判断，不是竞品事实）

优先级建议：先做划词浮层及稳定选词上下文；接着做图表独立放大和区域对话；然后做 2–5 篇的可追溯比较表。比较默认列可以是研究问题、方法、数据集、指标、关键结果、局限，用户可改。所有比较结论都应该能返回对应论文和原文证据。

交付说明中区分“官方证明已支持”“本轮未核实”“我们的规划”，不要用未查到替代能力否定，也不要用营销效果数字代替实测结果。
