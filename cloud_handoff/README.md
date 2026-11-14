# 云端会话交接：给本地会话"黑客松创新想法"

- 写于 2026-11-14 约 12:50（美东）。
- 来源：云端会话"项目竞争优势策略"。它运行在一台 Linux 云容器里（15 GB 内存、4 核），看不到主办方的 kit，也没有任何 API key。
- 分支：`claude/dreamy-allen-5qyhc4`，已合并 `origin/main` 的 `158e583`（5/5 有效）。
- 这个文件夹只是交接材料，**不要打进提交包**。

## 0. 一分钟版本

1. `prepare` 在内存小于约 20 GB 的机器上会被系统杀掉。已修复（提交 `24d7d23`），评委重跑前必须合并。
2. 5 个单功能包在 Linux 上全部通过：8 道本地闸、不用 Docker 的 oracle/nop 模拟、真 Harbor 0.26.0 + Docker 的 oracle/nop。Harbor 那一项有两处与提交包不同，见 1.3。
3. 任务容器里没有 `less`，click 自己的全量测试有 24 个失败。它可能影响"基线健康"那道闸，见 1.4。
4. 首轮运行约一半的钱花在重复写缓存上，见 1.5。只有决定重跑生成时才值得改。
5. 你的探针显示 Haiku 单功能题 15 次对 14 次。这是当前最大的失分点（Difficulty 20 分）。我的看法和一个限时方案在第 3、4 节。

## 1. 已验证的事实

### 1.1 `prepare` 内存问题与修复

- **现象：** 在 15 GB 内存、无交换分区的 Linux 上，`python -m taskfactory prepare` 涨到约 14 GB 后被 OOM 杀掉，覆盖率图没写出来。
- **原因：** `coverage json --show-contexts` 在每个函数和类的区域里重复整份文件的上下文表，导出的 `cov.json` 有 8.2 GB。实际数据只有约 98.5 万条"行-测试"对。与操作系统无关。
- **修复：** `factory/taskfactory/covmap.py` 改为用 `CoverageData.contexts_by_lineno` 直接读覆盖率数据库。
- **验证：** `prepare` 22 秒、峰值内存 371 MB；Linux 基线 2434 通过、0 失败（Windows 上是 2350，差别来自按系统跳过的用例）。5 个包重新选出的 P2P 清单与包内完全相同。
- **没验证：** 修复在 Windows 上的表现。本机已有缓存时 `prepare` 会跳过；要验证需先删掉 `factory/.cache/covmap.json` 和 `factory/.cache/base_health.json`。

### 1.2 5 个单功能包在 Linux 上的结果

| 包 | 8 道本地闸 | 变异 | oracle 模拟 | nop 模拟 | P2P 清单与 Linux 重选 |
|---|---|---|---|---|---|
| 0101 | 全过 | 11/12 | 1.0（F2P 15/15，P2P 80/80） | 0.0（F2P 0/15） | 相同 |
| 0102 | 全过 | 12/12 | 1.0（F2P 14/14，P2P 80/80） | 0.0（F2P 0/14） | 相同 |
| 0103 | 全过 | 10/12 | 1.0（F2P 38/38，P2P 19/19） | 0.0（F2P 0/38） | 相同 |
| 0104 | 全过 | 11/12 | 1.0（F2P 27/27，P2P 80/80） | 0.0（F2P 0/27） | 相同 |
| 0105 | 全过 | 9/12 | 1.0（F2P 16/16，P2P 80/80） | 0.0（F2P 0/16） | 相同 |

"oracle/nop 模拟"指不用 Docker，在基线快照上套用包自带的补丁，跑 `run_script.sh` 列出的文件，再按 `grade.py` 的规则计分。脚本是本文件夹里的 `oracle_nop_local.py`。

### 1.3 真 Harbor + Docker 的 oracle/nop（云端）

环境：Harbor 0.26.0、Docker 29.9.2、Compose v5.6.1。

- **结果：** 5 个包 oracle 全是 1.0，nop 全是 0.0，Exceptions 为 0，基线上的 P2P 全过。
- **与提交包的差别一：** 云端网络拦了 `deb.debian.org`，我用 `python:3.12-bookworm`（自带 git）代替 `python:3.12-slim-bookworm` + apt 那一步。Dockerfile 其余步骤原样执行。
- **与提交包的差别二：** 镜像由 `docker build` 直接构建，Harbor 以预构建镜像模式运行（在临时副本的 `task.toml` 里填了 `docker_image`）。
- **没跑：** kit 的 `selfcheck.sh` 和 `check_structure.py`。原样的 Dockerfile 仍需在一台网络正常的 Docker 机器上构建一次。

### 1.4 容器里缺 `less`

- **现象：** 在任务镜像里跑 click 全量测试：24 失败、2410 通过。失败的全是 `tests/test_utils/test_echo_via_pager.py` 里 `less` 的 24 个参数化用例。
- **原因：** 镜像里没有 `less`。`python:3.12-slim-bookworm` 和 `python:3.12-bookworm` 都没有。我在宿主机上把 `less` 从 PATH 里拿掉，复现了同样的 24 个失败；放回去就全过。Windows 上这些用例被跳过，所以本机看不到。
- **风险一：** 如果评委的"基线健康"闸是在容器里跑全量测试，5 个包都会受影响。kit 的 `selfcheck.sh` 只用 oracle/nop，查不出这一点。
- **风险二：** 评委在装有 `less` 的机器上重跑流水线时，这些用例在宿主机上是通过的，可能被选进某个翻页器相关任务的 P2P，到容器里就失败，oracle 得 0。
- **修法：** `factory/taskfactory/package.py` 第 16 行的 apt 安装列表末尾加 `less`。我没法在云端验证这一步。
- **代价：** 改 Dockerfile 会改变 task_checksum，必须在跑最终基准之前决定，并通过流水线重新出包，不要手改已出的包。
- **另一个观察：** 在容器里单独跑 `test_echo_via_pager.py` 这一个文件，第一个用例就报 `AttributeError: _termui_impl`。这是 click 自己的测试对导入顺序的依赖。本地闸按用例 ID 跑，容器里按整个文件跑，两者不完全等价。

### 1.5 首轮运行的成本

数据来自 `submission/runs/20261114T162521Z-s1/` 里每次调用的用量。

- **总花费：** 10.95 美元，5 个有效任务，平均 2.19 美元。
- **缓存没命中：** 整个 click 源码（约 16 万 token）被完整写入缓存 7 次，共 5.62 美元，占 51%。prewarm 写入的前缀是 159,222 token，design 是 159,724，implement 是 159,633，三者对不上，所以 5 个并行的 design 调用各写了一遍，第一个 implement 又写了一遍。
- **原因：** 没定位。可能与 prewarm 把输出上限设为 16 有关。
- **最稳的修法：** 让第一个 design 调用单独跑完，再放出其余 4 个。理想情况下每批省约 4.6 美元，平均降到约 1.3 美元。
- **两次多余的修复调用：** 0101 和 0104 首次尝试只败在公平闸的引号误报上，共 0.78 美元。`main` 上的 `158e583` 已经修了 `WORD_RE`，重跑时不会再发生。

### 1.6 实测差异度

由本文件夹里的 `task_overlap.py` 从 5 个包直接算出，不需要模型调用。

| 包 | gold 改动的文件 | 新增行 | F2P | P2P |
|---|---|---|---|---|
| 0101 | core.py（Group） | 63 | 15 | 80 |
| 0102 | core.py（Context、Option）、types.py | 161 | 14 | 80 |
| 0103 | shell_completion.py | 131 | 38 | 19 |
| 0104 | parser.py、core.py（Command、Context）、decorators.py、`__init__.py` | 248 | 27 | 80 |
| 0105 | termui.py、core.py（Option） | 117 | 16 | 80 |

- **P2P 两两重合度（Jaccard）：** 平均 0.048，最大 0.21（0102 与 0104）。10 对里有 6 对完全不重合。
- **改动位置重合：** 只有两对共用一个类：0102 与 0104（Context）、0102 与 0105（Option）。
- **用法：** 这张表可以直接放进 `VARIATION.md`，作为"差异是量出来的"的证据。

## 2. 没做或做不了的

- Haiku 基准：云端没有 key，容器里的智能体也出不了网。
- 你的组合题和 `compose` 阶段：不在 `origin` 的任何分支上，我没见过。
- 原样 Dockerfile 的构建、kit 的检查器：见 1.3。
- 下面第 3 节的想法都没有实现，只有分析。

## 3. 策略脑暴（原话的整理）

用户最初的问题是"如何取得独有优势夺得第一"。我的回答定位是一句话：别的队"声称"题目难而公平，我们"测量"它。

### 3.1 核心想法：解题者进闭环

出包前由两个解题者实测，强者过、弱者挂才放行。

- **弱解题者：** Haiku 本尊。只给 `instruction.md` 和源码，写补丁，本地跑 F2P/P2P。约 2 美分一次。
- **强盲解者：** Sonnet/Opus，最好换一家厂商。只看 spec。它全过，就证明"仅凭 spec 可解"，补上公平闸只查用词、不查逻辑的短板。它挂掉的断言逐条裁决：spec 里有依据就保留，没有就补 spec 或删断言。
- **Haiku 错误补丁的三个用途：** 当语义变异体；它弄坏的现有测试进 P2P；每题的失败规则构成"失败指纹"，作为考察不同能力的实证。

### 3.2 配套想法

| 想法 | 做法 | 对应分数 | 状态 |
|---|---|---|---|
| 难度旋钮 | `grade.py` 全对才算 resolved，单次通过率约等于各规则簇通过率的乘积；把单次通过率压到 0.2 以下，resolve@3 最多 1 次的概率约 90% | Difficulty | 未做 |
| 实测差异度 | 两两距离矩阵，太近就拒收 | Variation | 数据已算（1.6），闸未做 |
| 回归切片升级 | 对被改函数的原有代码做变异，选出杀得死变异体的最小测试集 | Regression | 未做 |
| 环境扰动闸 | gold + F2P 在不同 COLUMNS、LANG、TZ、哈希种子、乱序下各跑一遍 | Reliability | 未做 |
| 备用格子对冲 | 某题首轮重挂时并行启动备用格子 | Reliability | 未做 |
| 题目卡 | 测试按规则编号打标签，每题一张卡：难度、区分度、规则覆盖、成本 | Fairness、演示 | 未做 |
| 按历史选题 | 按 git 历史里 bug 修复最密集的函数选扩展点 | Difficulty | 未做 |
| 新鲜度 | 基线 8.5.1 才发布 9 天，模型没见过 | 演示 | 可直接讲 |

### 3.3 结合你的探针结果，我的更新看法

以下是推断，没有实测。

- **乘法不够：** 单题通过率 93% 时，每条规则的通过率约 99%。靠增加规则数或拼接功能，通过率降得很慢。这与你"组合题约 80%"的结论一致。
- **难度要来自深度：** 规格写得再全，也不能照抄成实现的那类工作。我能想到四种机制：
  1. 算法内核：规则少、用例多，例如带确定性决胜规则的排版、排序、引号文法。
  2. 找全调用点：spec 只陈述一个不变量，解题者要在 3000 行的 `core.py` 里自己找出所有要改的位置。
  3. 状态与重入：异常、清理、链式命令下的精确执行顺序。
  4. 兼容性陷阱：改热路径，同时用对回归敏感的 P2P 把关。
- **落地方式：** 把这四种机制做成变化矩阵的第三根轴，写进 design 提示。改动集中在 `matrix.py` 和 `prompts.py`。这同时加强 Variation 的说法。
- **只能由流水线来做：** 评委只给流水线的原样产出打分，所以任何加难都必须是提示或闸门的改动，再用主办方的 key 重跑。

## 4. 与你的 7 步方案的衔接

| 你的步骤 | 我这边已有的 |
|---|---|
| 1 合并内存修复 | 合并本分支即可，改动只有 `covmap.py` 和本文件夹。 |
| 2 在 WSL 里验证 | 5 个单功能题我已在 Linux 上验证（1.2）。组合题没见过。`oracle_nop_local.py` 可以直接用。 |
| 3 Harbor 自检 | 云端结果见 1.3，命令见第 5 节。用户已在 WSL 里装好 Harbor 0.26.0；我最后看到时 Docker 还没装上。 |
| 4 Haiku 基准 | 没有。注意基准的包必须与提交的包一致。 |
| 5 选交哪一组 | 见 3.3：我的判断是两组都偏易，选哪组主要看 Variation。 |
| 6 打包 | `factory/smoke/` 和本文件夹不要进包。`less` 的决定要在这之前做。 |
| 7 演示 | 可用的现成素材：1.1 的内存故事、1.6 的差异度表、基线发布才 9 天。 |

### 一个限时选项：加难后重跑一次

只有同时满足下面三条才值得做，否则按你的方案交付：

- 生成能在收工时间前跑完。首轮 5 题用了 992 秒、10.95 美元。
- 有一台能跑 Haiku 基准的 Docker 机器，并且基准能在打包前跑完。
- 现有的 5 题作为保底，随时可以退回。

如果做，同一次重跑里顺带改三处：3.3 的第三根轴、1.5 的缓存、1.4 的 `less`。跑完先用你的探针比较新旧两批，新批不更难就不换。

## 5. 复现我的检查

合并本分支（如果本地改过 `covmap.py`，先看一眼冲突）：

```bash
git fetch origin
```

```bash
git merge origin/claude/dreamy-allen-5qyhc4
```

不用 Docker 的 oracle/nop 模拟，在仓库根目录用 factory 的 venv 运行：

```bash
python cloud_handoff/oracle_nop_local.py
```

差异度表：

```bash
python cloud_handoff/task_overlap.py
```

本地闸复查：

```bash
python -m taskfactory recheck ../submission/generated_tasks/*
```

真 Harbor 的 oracle 和 nop（需要 Docker），在仓库根目录运行：

```bash
harbor run -p submission/generated_tasks -a oracle -o ~/harbor_jobs -y
```

```bash
harbor run -p submission/generated_tasks -a nop -o ~/harbor_jobs -y
```

在任务镜像里跑 click 全量测试，用来确认 `less` 的问题和修复（镜像名用 `docker images` 查）：

```bash
docker run --rm <任务镜像> bash -c 'cd /app && python -m pytest tests -q -p no:cacheprovider --color=no | tail -3'
```

## 6. 披露用的事实

- 云端会话对仓库代码的改动只有一处：提交 `24d7d23`（`factory/taskfactory/covmap.py`）。
- 它没有写过、也没有改过任何任务包、spec、gold 或测试。
- 它没有用过主办方的任何 key，也没有运行过生成或基准。

## 7. 怎么找云端会话

- 云端会话还开着，Docker 也还能用。把新的包推到任意分支并告诉它分支名，它可以在 Linux 上跑本地闸和 Harbor 的 oracle/nop，大约两分钟。
- 它在会话列表里的名字是"项目竞争优势策略"。
