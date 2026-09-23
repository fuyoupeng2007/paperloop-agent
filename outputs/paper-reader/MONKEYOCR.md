# MonkeyOCR 本地接入与论文信息核查

核查日期：2026-09-22。研究来源以作者官方 GitHub、arXiv 原文为主；作者主页仅使用本人或所在实验室页面。此次只做资料和硬件检查，没有下载模型、安装驱动、修改用户文档或重新解析已校正的论文。

## 结论

**这台电脑可以尝试接入官方 MonkeyOCRv2 的 Windows CPU 解析方案，但当前还没有安装或实测。** 建议作为复杂表格、公式、扫描页的增强解析器，现有数字 PDF 快速提取继续保留。原论文的 3B + NVIDIA CUDA 方案不适合直接照搬到本机；2026 年推出的独立后续项目 MonkeyOCRv2 已提供 CPU 路径，不能据老论文的 RTX 3090 示例断言“本机不能运行”。[官方 CPU 指南](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/docs/cpu_support.md)

## 本机实际条件

以下来自只读 PowerShell CIM、Python 环境和文件检查，不是网页推测。

| 项目 | 实际结果 | 含义 |
| --- | --- | --- |
| CPU | Intel Core Ultra 9 285H；16 核、16 逻辑处理器 | 可以使用官方 CPU 后端，实际吞吐待测 |
| 系统内存 | 33,752,997,888 字节，约 31.4 GiB | 有试运行小型解析模型的条件；不能等同于已测峰值内存 |
| 显卡 | Intel Arc 140T；驱动 32.0.101.7026 | 不是 NVIDIA CUDA 显卡；CIM 名称中的“16GB”不能据此认定为 16GB 独立显存 |
| NVIDIA/CUDA | 未发现 nvidia-smi、System32 下相应程序或 CUDA Toolkit 目录 | 不采用官方 CUDA 推理安装路径 |
| WSL | 系统提示未安装 | CPU 原生 Windows 路径不要求先装 WSL |
| Python | 应用为 3.14.3；启动器另列 Anaconda 3.9，没有 3.11 | MonkeyOCRv2 应隔离安装 Python 3.11，避免改坏应用环境 |
| 当前 OCR 运行库 | RapidOCR 可用；ONNX Runtime 1.30.0，CPUExecutionProvider 可用 | 现有扫描页 OCR 已可本地运行 |
| 深度学习依赖 | 应用环境未发现 torch、transformers、lmdeploy、vllm | 不能把“有官方方案”写成“已经装好并跑通” |

## 应采用哪个版本

需要区分三个名称：

1. 用户上传的 **MonkeyOCR_2025_v1.pdf**：arXiv `2506.05218v1`，2025-06-05，原始 3B/SRR 论文。
2. **同一论文的 arXiv v2**：`2506.05218v2`，2026-02-07；作者和数据规模已更新。这不是下面的软件项目 MonkeyOCRv2。
3. **MonkeyOCRv2 新项目**：2026 年独立后续项目，官方仓库提供 0.6B 的 S-Parsing、0.7B 的 B-Parsing，2026-08-22 公布 CPU 支持。本机应优先评估这一条路径。[原论文版本记录](https://arxiv.org/abs/2506.05218)；[新项目官方仓库](https://github.com/Yuliang-Liu/MonkeyOCRv2)

老版官方支持 CUDA/LMDeploy、vLLM、Transformers，并有 Windows 指南；硬件例子包括 RTX 3090 等，8GB RTX 4060 可部署其 1.2B 或量化 3B。那是 NVIDIA 硬件证据，不代表 Intel Arc 可以直接使用同一 CUDA 环境。[原项目硬件说明](https://github.com/Yuliang-Liu/MonkeyOCR#supported-hardware)；[老版安装指南](https://github.com/Yuliang-Liu/MonkeyOCR/blob/main/docs/install_cuda_pp.md)

## CPU 路径与接入建议

官方 Windows CPU 文档要求 Python 3.11、PyTorch 2.5.1 CPU、Torchvision 0.20.1、Transformers 4.57.1、Accelerate 1.11.0 等；只需解析模型检查点，不需要 GPU 专用 DFlash 草稿模型。官方 CLI 为 `parsing/cpu/parse_cpu.py`。建议独立目录和解释器管理，先试 S-Parsing，再用同样样本比较 B-Parsing。[官方 CPU 安装与用法](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/docs/cpu_support.md)

已检查官方 CPU 代码：默认 float32、eager attention、单页并发；支持单独识别 text/formula/table，以及完整页面的类别、坐标、内容输出。可先对选中表格裁图做单任务验证，再接入整页结构解析。此检查只证明实现路径存在，未证明本机速度或精度。[CPU 命令行代码](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/cpu/parse_cpu.py)；[CPU 推理实现](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/cpu/core_runner.py)

具体接入步骤是本项目的工程建议：

1. 建立单独的 MonkeyOCR 运行目录和 Python 3.11 环境，固定官方代码提交和模型版本。应用通过本机子进程调用，不把大模型依赖装进现有 `.venv`。
2. 首批仅验证独立样本：标题页、双栏页、公式页、扫描页，以及 MonkeyOCR 第 5 页的表格副本。记录耗时、峰值内存、标题/页边注记识别、表格行列及勾选正确率。
3. 新解析结果写到新版本记录，保留原文图像、坐标、解析器版本和校验值。与现有块匹配、比较差异后再应用；第 5 页人工校正不被后台任务覆盖。
4. 默认由 pdfplumber 提取健康的 PDF 文本；遇到缺字、表格结构异常或扫描页，调用增强解析。失败仍保留原文与已有译文，并能暂停重试。
5. 实测证明改善后再决定是否默认用于全部页面。CPU 模型需要下载权重和运行库，且公开资料未给本机型号的速度；此阶段不承诺秒级识别，也不承诺免除人工核对。

| 能力 | 本项目现有 pdfplumber + RapidOCR | 建议中的 MonkeyOCRv2 CPU 增强 |
| --- | --- | --- |
| 有文字层的普通 PDF | 直接读取字符和坐标，速度快；已有实现与测试 | 图像模型重新识别，未必值得全部替换 |
| 扫描页 | RapidOCR 识别文字，版面组织能力较基础 | 可尝试统一输出结构、内容、阅读顺序 |
| 复杂表格/公式 | 当前启发式存在真实问题：合并单元格、字体缺字、图形误判 | 官方支持专门任务，需用实际样本评估改善 |
| 硬件/依赖 | 已运行，CPU 即可 | 需要隔离 Python 和模型；CPU 延迟、内存待实测 |
| 翻译与论文问答 | 交给已接通的 ChatGPT 通道 | MonkeyOCR 是解析器，不替代翻译与问答模型 |

比较中的“当前实现”来自本地代码和本次真实 PDF 检查；模型能力来自上面的官方文档。这里没有进行两者同机质量或速度基准测试。

## 当前发表信息：预印本与期刊应分开写

用户这份 PDF 是 **2025-06-05 的 arXiv v1 技术报告**。当前 arXiv 最新记录为 2026-02-07 的 v2。作者官方仓库在 2026-07-12 宣布论文已被 **SCIENCE CHINA Information Sciences** 接收；这是期刊，不是 CVPR/ICCV 等会议。可在产品里写“arXiv 预印本；官方项目宣布 SCIS 期刊接收”，并标明核查日期。此次没有核实最终出版卷期、页码或出版社 DOI，不能编填这些字段；arXiv DOI 也不是期刊出版 DOI。[用户版本](https://arxiv.org/abs/2506.05218v1)；[当前 arXiv 记录](https://arxiv.org/abs/2506.05218)；[作者官方接收公告](https://github.com/Yuliang-Liu/MonkeyOCR#news)

## 完整作者与单位

用户上传 v1 的顺序如下，单位按原文：

| 顺序 | 作者 | 单位 |
| --- | --- | --- |
| 1 | Zhang Li | Huazhong University of Science and Technology，华中科技大学 |
| 2 | Yuliang Liu | 华中科技大学 |
| 3 | Qiang Liu | Kingsoft Office，金山办公 |
| 4 | Zhiyin Ma | 华中科技大学 |
| 5 | Ziyang Zhang | 华中科技大学 |
| 6 | Shuo Zhang | 华中科技大学 |
| 7 | Zidun Guo | 华中科技大学 |
| 8 | Jiarui Zhang | 金山办公 |
| 9 | Xinyu Wang | 华中科技大学 |
| 10 | Xiang Bai | 华中科技大学 |

[v1 原文作者与单位](https://arxiv.org/html/2506.05218v1)

当前 v2 在 Shuo Zhang 后、Zidun Guo 前新增 **Biao Yang（华中科技大学）**，总计 11 人；其余姓名与顺序保持对应。不要把 v2 作者名单静默覆盖到用户正在阅读的 v1 上。[v2 原文作者与单位](https://arxiv.org/html/2506.05218v2)

可唯一对应、已经打开核对的少数作者主页：

- **Yuliang Liu**：[官方 GitHub](https://github.com/Yuliang-Liu) 直接链接 [本人主页](https://yuliang-liu.com/)，主页列出 MonkeyOCR 和华中科技大学单位，身份链条清楚。
- **Xiang Bai**：[实验室个人主页](https://xbai.vlrlab.net/) 明确姓名、华中科技大学及视觉/文字识别研究领域，可与论文单位对应；该页面部分新闻较旧，不据此推断最新职务变化。

未给其他常见姓名随意匹配搜索结果，避免把同名学者主页链接进去。

## 对读论文应用的研究意义

这篇工作的直接价值是：把文档解析明确分成“找到区域、识别区域内容、确定阅读顺序”，让复杂表格、公式和跨栏正文拥有结构化结果，而不只是把所有字符拼成字符串。这正对应当前应用暴露的解析短板。原始 v1 采用 3B 模型和 390 万中英文实例的数据集；论文报告的指标属于作者在其基准设置下的结果，不能当作在用户电脑或每篇论文上的保证。[原始论文](https://arxiv.org/html/2506.05218v1)

v2 修订论文将数据规模更新为 450 万，并加入更小模型的参数裁减研究；这些变化应作为“后续进展”展示，不应混进 v1 正文翻译。[修订论文](https://arxiv.org/html/2506.05218v2)

本项目可借鉴其解析层方案，同时继续由 ChatGPT 做中文翻译、释义和引用问答。解析器、Agent 自动任务循环和桌面窗口是三个不同层次；更换解析器不要求重写整个桌面应用。

## 补充核实：实际下载量、许可证、输出适配

2026-09-22 对作者官方 Hugging Face 文件目录/API 做了只读检查；没有下载权重。

| 选择 | 仓库全部文件的实测元数据合计 | 主要权重文件 | 对本机建议 |
| --- | --- | --- | --- |
| S-Parsing | 22 个文件；1,877,658,034 字节，约 1.88 GB（1.75 GiB） | 主权重 1,568,277,288 字节；两个预处理权重 4,823,415 与 288,533,650 字节 | 先采用这一版，验证 CPU 体验 |
| B-Parsing | 22 个文件；2,065,305,782 字节，约 2.07 GB（1.92 GiB） | 主权重 1,755,925,032 字节；预处理权重同上 | 后续同样本比较，不需一开始同时下载 |

目录还包含分词器、配置和自定义模型代码。官方下载脚本会下载选定仓库完整快照；只需选 **一个 Parsing 模型仓库**，其中包含一个主模型和两个可选的预处理权重。不需要再下载独立视觉编码器、Understanding 模型、DFlash 模型或 MonkeyDoc 数据集。[官方 S 文件清单](https://huggingface.co/zenosai/MonkeyOCRv2-S-Parsing/tree/main)；[官方 B 文件清单](https://huggingface.co/zenosai/MonkeyOCRv2-B-Parsing/tree/main)；[官方下载脚本](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/download_model.py)

如果只做已摆正的数字 PDF 或固定区域识别，启用 `--skip-preprocess` 后，运行时不会加载那两个预处理模型；S 版所需主模型、分词器和配置约 **1.58 GB**。这个精简量是由文件清单和加载分支推导，不是官方完整下载脚本的默认行为。后者仍约 1.88 GB。另需 Python 3.11、CPU PyTorch 和配套库；未生成完整依赖锁，不能给这些依赖伪精确总量。工程上可先预留约 3 GB 下载预算、6–8 GB 安装与缓存空间，这是预算估计，不是实测占用。[CPU 加载分支](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/cpu/core_runner.py)

官方仓库声明代码和 MonkeyOCRv2 模型权重使用 **Apache License 2.0**，官方模型卡也标记 apache-2.0。训练数据集的标注使用另一许可证，但本次推理无需下载该数据集。后续打包应携带所固定版本的许可证和归属说明。[官方许可证声明](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/LICENSE)；[官方模型卡](https://huggingface.co/zenosai/MonkeyOCRv2-S-Parsing)

### 输出可以对接，但必须处理坐标空间

已读官方 CPU 代码，输出包含 Markdown、JSON、图片和可选布局可视化 PDF。JSON 根节点含 `image_name`、`image_path`、`image_size`、`layouts`；块含 `bbox`、`label`、`content`、`page_num`。页码从 1 开始，列表按页及模型预测的阅读顺序排列。模型原始坐标采用 0–1000，官方最终 JSON 已把坐标转换成页图像像素。[CPU 输出实现](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/cpu/core_runner.py)

下面是本应用适配设计，不是已实现功能：

| MonkeyOCR 输出 | 本应用字段/处理 |
| --- | --- |
| `page_num` | `page`，保持从 1 开始 |
| 某页 `image_size=[W,H]` | 用该页尺寸归一化坐标 |
| `bbox=[x1,y1,x2,y2]` 像素 | `bbox=[x1/W,y1/H,x2/W,y2/H]`；左上角原点 |
| 单页列表顺序 | `order`，不再按 x/y 排序，否则会破坏多栏阅读顺序 |
| `Title` / `Section-header` | `title` / `heading` |
| `Text` / `Caption` / `Table` / `Formula` | 相应 `kind`；图像块单独保留 |
| `Page-header` / `Page-footer` | `margin`，保留但不混入正文翻译 |
| `content` | 正文；标题去 Markdown 标记；表格保留 HTML/单元格跨度信息；公式保留 LaTeX |

**首版对数字 PDF 使用 `--skip-preprocess`。** 默认预处理可能旋转或去畸变；这时 JSON 的坐标指向处理后的图像，仅按 W/H 归一化不足以回到原始 PDF。若以后支持预处理后的扫描页，需保存几何逆变换，或者明确把高亮展示在处理后图像上。不能在原 PDF 上显示错误位置。固定裁图的 `-t table` 模式可沿用裁图原有坐标，适合最小接入验证。

### 可执行的后续接入步骤

1. 在独立 `runtime/monkeyocr` 目录安装 Python 3.11 环境，按官方 CPU 指南安装依赖；固定仓库提交，下载 S-Parsing 一个仓库。所有缓存、模型和输出指定到项目目录。
2. 用公开样本或当前论文第 5 页的独立副本运行：从 MonkeyOCRv2 的 `parsing` 目录调用 `cpu/parse_cpu.py`，参数给定 `--input-path`、`--model-path`、`--output-path`，加上 `--skip-preprocess --page-max-inflight 1 --draw-layout`。第一轮只测一页，记录耗时和内存。
3. 实现 `MonkeyOCRParser` 适配器读取上述 JSON，校验页数、坐标范围、空块、重复块及表格结构；应用已有模型翻译、问答和笔记层无需替换。
4. 先把增强结果写成独立候选版本，确认第 5 页所有数据集行名与勾选位置准确、页边 arXiv 文本不混入正文，并检查 PDF 高亮对应。
5. 保留当前已校正的文档版本；用户选择应用新结果时再迁移段落关联，不能以自动重解析覆盖已完成译文和人工表格校正。

当前仍使用本地 pdfplumber + RapidOCR；MonkeyOCR 尚未安装、集成或实测。没有向官方 Demo 或其他第三方上传论文。
