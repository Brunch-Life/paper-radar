# 关键词与信号源诊断报告

**日期**: 2026-05-07（创建）/ 2026-05-08（Tier S/A/B/C 落地）
**输入**: 535 条 relevant bios + 60 条最近 tweets + 132 author boost + 当前 `aggregate.py` TOPIC_KW
**结论先行**: 当前 TOPIC_KW 整体方向对，但 **3 类问题** 需要修；HF Daily 确实不该当主信号源，**正确做法是把信号源重新分层**而不是换一个。

---

## ✅ 落地状态（2026-05-08）

- **Tier S/A/B/C + DROP 已实装**于 `paper_radar/scoring.py`，`aggregate.py` 与 `annual_scan.py` 都从这里 import（不再有重复 TOPIC_KW）
- **HF cap 50→10** 已统一 (`HF_UPVOTE_CAP=10` in `paper_radar.scoring`)
- **效果**（用今天 RSS 反推）：候选数 53→23（噪声-57%），TOP 1 从 `Counterfactual identifiability...` 变成 `Q2RL`（真机 Franka RL，旧排名 #18 → 新 #1）；纯 LLM RL 论文没有 robot context 不再得分（旧 +2 → 新 0）；财政监管 / RTX 显卡 / "65% faster" 形容词等 false positive 全部去掉或 gated
- **未做**：watchlist 作者自投 RSS 信号（4.1 节）、Semantic Scholar 引用图谱（4.3 节）、OpenReview burst（4.2 节）

剩下的章节是当时设计文档的快照，保留作 V1 reference。

---

## 1. 师兄关注图谱里到底在关心什么

### 1.1 535 条 bio 多标签分类（不互斥）

| count | % | topic |
|---|---|---|
| 37 | 6.9% | **embodied AI（泛词）** |
| 37 | 6.9% | **公司 / 硬件 / 部署**（Figure / Tesla / Unitree / Apptronik / Galaxea / Galbot / Spirit ...） |
| 25 | 4.7% | RL theory / general RL |
| 22 | 4.1% | LLM / VLM / multimodal |
| 21 | 3.9% | humanoid / locomotion |
| 15 | 2.8% | manipulation / dexterous |
| 13 | 2.4% | world model |
| 12 | 2.2% | VLA / vision-language-action |
| 12 | 2.2% | foundation models / scaling |
| 10 | 1.9% | sim2real / simulation |
| 9  | 1.7% | RL post-train / RL fine-tune |
| 7  | 1.3% | autonomous driving |
| 5  | 0.9% | agentic / tool agent |

**关键观察**: 535 个里 **362 个 (68%) bio 没匹配上任何主题** —— bio 太短或太通用（"Robotics", "AI Researcher"）。**bio 不是好的 topic 信号源，最多只能告诉你某个人"是不是这个圈子的"，不能告诉你他在研究什么。**

### 1.2 RT 信号加权（过去 ~2 个月真实活跃度）

只有 12 个人最近 RT 过相关内容，加权分布是：

| RT-weight | topic |
|---|---|
| 4 | hardware / company / deployment |
| 3 | embodied AI |
| 2 | world model |
| 2 | LLM / VLM / multimodal |
| 1 | sim2real |
| 1 | manipulation |
| 1 | agentic |
| 1 | autonomous driving |

**bio 频率 vs RT 频率不一致**：bio 里 humanoid/RL 频次高（行业老话题），RT 里 world model / agentic / 公司动态高（当下热点）。**RT 是真信号，bio 是 priors。**

### 1.3 tweet 文本里出现的具体方法名（小样本但有指向）

```
VLA(8)  RL(5)  LBM(2)  WAM(2)  PI(2)  OOD(2)  JEPA(1)  
SimpleVLA-RL(1)  LVP(1)  DVA(1)  Robo-Dopamine(1)
"world models"(4)  "vla models"(2)  "joint learning"(2)
```

`LBM` (Toyota Large Behavior Model)、`WAM` (World Action Model)、`JEPA`、`LVP` (Latent Video Pretraining)、`DVA` (Dual Video Action) —— **当前 TOPIC_KW 全都没收**，这些是 2025–2026 真正的新名词。

---

## 2. 当前 TOPIC_KW 的诊断

### 2.1 三类问题

#### 问题 A：**关键词过于精确，错过同义改写**
当前 `\bvla\b|\bvision[- ]language[- ]action\b` 只匹配三种字面，但 paper 标题里更常见的是：
- `vision language model` for robot
- `unified visuomotor`
- `multimodal action model`

→ 应当扩展到 paraphrase 集合。

#### 问题 B：**关键词过于宽松，引入噪声**
- `\b(?:offline RL|on-policy|off-policy|PPO|SAC|DPO|GRPO)\b` 权重 +1 但会命中**任意 LLM RL 论文**——LLM-only RL 论文一年几千篇，按 +1 也会污染 ranking
- `\b(?:embodied)\b` +1 类似——很多 NLP "embodied agent" 论文跟机器人没关系

→ 这两条应该改成 **gating** 而不是 +1：必须**同时**命中 robot/policy/control 才算分。

#### 问题 C：**named-model 列表停留在 2024**
当前列表：`OpenVLA / RT-1/2/X / Octo / Helix / π0 / RDT / GR00T`

漏掉了 2025–2026 关键模型：
- **π0.5 / π0-FAST / π_RL** (Physical Intelligence 后续)
- **GR00T-N1 / GR00T-N1.5 / GR00T-N2** (NVIDIA)
- **MolmoAct / MolmoAct2** (AI2)
- **EmbodiedOneVision / OneVL**
- **Genie 2 / Genie 3** (DeepMind world model)
- **LBM** (Toyota Large Behavior Model)
- **GigaBrain-0 / 0.5M*** (字节)
- **Dream-VLA**
- **HY-Embodied** (腾讯)
- **EBT-Policy**
- **JEPA / V-JEPA-2** (Meta)
- **Veo / Cosmos** (world models)

每漏一个就漏一篇该被推到顶端的论文。

### 2.2 现在的 score 上限分布

跑一遍 250 候选实际 score 分布：score ∈ [58, 70]，绝大多数在 58–62 之间——**区分度太差**，因为绝大多数同时命中 3–4 个 topic-keyword（拉满 +12 已经是天花板）。**应该给 named-model 命中 +6**（明显信号），让真正硬核的论文能拉开档。

---

## 3. 推荐的新 TOPIC_KW（分层版）

设计原则：**S/A/B/C 四级 + 同义改写 + 强 gating + 反向词**。

### Tier S — named system / named model（命中即必读）

权重 **+6**。一篇 paper 标题/abstract 出现这些词，意味着它直接做同方向的延伸，必须深读。

```python
TIER_S = [
    # Physical Intelligence family
    (r'\b(?:π0\.5|π0|pi[- ]?0\.5|pi[- ]?0|pi-?zero|pi-?fast)\b', 'pi-family'),
    # OpenVLA / RT family
    (r'\b(?:OpenVLA(?:[-_ ]?OFT)?|RT-?[12X]|Octo)\b', 'openvla-rt'),
    # NVIDIA GR00T
    (r'\bGR00T(?:[-_ ]?N?[12]\.?[05]?)?\b', 'gr00t'),
    # Hot 2025-2026 VLAs
    (r'\b(?:RDT|MolmoAct[2]?|Helix|EmbodiedOneVision|OneVL|Dream[- ]?VLA|HY[- ]?Embodied|GigaBrain[- ]?0(?:\.5M)?|Green[- ]?VLA|EBT[- ]?Policy|FASTER|VLA[- ]?RFT|SimpleVLA[- ]?RL|π_?RL|RLinf[- ]?VLA)\b', 'hot-vla-2026'),
    # World model named systems
    (r'\b(?:Genie[- ]?[123]|Veo[- ]?[123]?|Cosmos|V?[- ]?JEPA[- ]?[12]?|Sora|LWM)\b', 'world-model-named'),
    # Behavior models
    (r'\b(?:LBM|Large Behavior Model|WAM|World[- ]?Action[- ]?Model)\b', 'lbm-wam'),
    # Frameworks the user uses
    (r'\b(?:RLinf|verl|veRL|TRL|HuggingFace TRL)\b', 'rl-framework'),
]
```

### Tier A — core topic (4 分)

权重 **+4**，覆盖你 stack 的 5 个研究方向。

```python
TIER_A = [
    # VLA + paraphrase set
    (r'\b(?:vision[- ]language[- ]action|VLA|visuomotor polic|generalist polic|robot foundation model)\b', 'VLA-or-equiv'),
    # Real-robot / Real-world RL
    (r'\b(?:real[- ]world|real[- ]robot|on[- ]?robot)\s+(?:RL|reinforcement learning|fine[- ]tun|post[- ]train|adaptation)', 'real-robot-RL'),
    # RL post-training of VLA / policy
    (r'(?:post[- ]train|fine[- ]tun).*\b(?:vla|polic|robot|manipulation)|\b(?:vla|robot|polic).*(?:post[- ]train|fine[- ]tun).*(?:RL|reward)', 'post-train-policy'),
    # World model + RL
    (r'\bworld model.*(?:reward|RL|polic|action)|\b(?:RL|polic).*world model', 'world-model-RL'),
    # Diffusion / flow policy
    (r'\b(?:diffusion polic|flow[- ]match.*polic|polic.*flow[- ]match|EDM polic|score-based polic)\b', 'diffusion-flow-policy'),
]
```

### Tier B — adjacent topic (2 分)

权重 **+2**，邻近方向但不直击 stack。

```python
TIER_B = [
    # Sim platforms (only count if paper actually uses them)
    (r'\b(?:ManiSkill[23]?|RLBench|RoboCasa|RoboSuite|Isaac (?:Gym|Lab|Sim)|MuJoCo|MetaWorld|CALVIN|LIBERO)\b', 'sim-platform'),
    # Sim2real
    (r'\b(?:sim[- ]?to[- ]?real|sim2real|domain randomization|cross[- ]embodiment)\b', 'sim2real'),
    # Imitation / data collection
    (r'\b(?:imitation learning|behavior cloning|GELLO|ALOHA|UMI|teleoperation)\b', 'imitation-data'),
    # Manipulation / dexterous
    (r'\b(?:dexterous (?:manipulation|hand)|bimanual|dual[- ]arm|mobile manipulation|whole[- ]body)\b', 'manipulation-modes'),
    # Humanoid
    (r'\b(?:humanoid|biped|legged locomotion|quadruped robot)\b', 'humanoid'),
    # Robots used in user's lab
    (r'\b(?:Franka|FR3|UR[35e]|xArm|H1|G1|Unitree)\b.*\b(?:RL|polic|learn|manipulation)', 'lab-robots'),
]
```

### Tier C — gated weak signal (1 分，但必须 co-occur with robot context)

只有同时命中 robot/embodied/policy/manipulation 才给分，否则无效——避免引入纯 LLM RL 论文。

```python
TIER_C_GATED = [
    # RL methods, only when robot context present
    (r'(?=.*\b(?:robot|embod|polic|manipul|control))\b(?:GRPO|DPO|PPO|SAC|RLOO|REINFORCE|TD-MPC|MPO|advantage actor critic)\b', 'rl-method-in-robot'),
    # LLM/VLM, only when explicitly for robot/embodied
    (r'(?=.*\b(?:robot|embod|polic|manipul))\b(?:LLM|VLM|multimodal|large language model)\b', 'llm-for-robot'),
    # 3D / point cloud, only for robot scene
    (r'\b3d (?:scene|representation).*(?:robot|polic|manipul)|point cloud.*(?:robot|polic|manipul)', '3d-for-robot'),
]
```

### DROP — 反向词（命中扣 -8）

```python
DROP_KW = [
    # Off-domain
    r'\b(?:bioinformatic|protein folding|drug discovery|molecular dynamics|gene expression)\b',
    r'\b(?:medical imaging|radiolog|histopath|MRI|CT scan|electronic health record)\b',
    r'\b(?:federated learning|differential privac|homomorphic encrypt)\b',
    r'\b(?:adversarial attack|backdoor attack|jailbreak|red team)\b',
    # Pure NLP w/ no robotics tie-in
    r'\b(?:machine translation|text summarization|sentiment analysis|named entity)\b',
    r'\b(?:recommender system|search engine|ranking)\b',
    # Theory / pure CV / pure NLP
    r'\b(?:image classification|object detection|semantic segmentation)\b(?!.*\b(?:robot|polic|embod))',
]
```

### 总分目标范围

新 TOPIC_KW 让 score 上限拉到 ~30–50，普通 paper 在 5–15，VLA 顶刊在 20–35，强相关 + named-model 命中能上 40+。**区分度 ×4**。

---

## 4. Hugging Face Daily Papers 不该当主源

你说的对——HF Daily 是 **community vote**，存在三类 brigading：
1. **作者刷票** —— 自家公司一发就 50+ 票
2. **明星账号转发拉票** —— Yann LeCun 转发 → 一条 +200 票
3. **HF 内部 PR 推荐位** —— hf-daily 选谁不选谁本身就有偏

实测：本次 250 候选里得分 ≥58 的 50 篇里，**有 5 篇 HF upvote=200+ 但 paper 实际质量平庸**（标题党 / setup 浅），HF 信号是它们的主要分数来源。

### 信号源应该重新分层

| 层级 | 信号源 | 可靠度 | 建议权重 |
|---|---|---|---|
| **L1 一手** | arXiv cs.RO/cs.LG 直接 listing | ⭐⭐⭐⭐⭐ | 主源 |
| **L1 一手** | **121 watchlist 作者自己的 arXiv 投稿** | ⭐⭐⭐⭐⭐ | **新增主源**（下面详述） |
| **L2 同行评议** | OpenReview 进行中 review（NeurIPS/ICLR/CoRL/RSS） | ⭐⭐⭐⭐ | 在 review window 期间用 |
| **L2 引用图谱** | Semantic Scholar：被 Zotero 库或 watchlist 论文引用的新 paper | ⭐⭐⭐⭐ | 累积信号 |
| **L3 社区注意力** | HF Daily Papers | ⭐⭐ | **降级为 tertiary，capped +5 而不是 +50** |
| **L3 行业信号** | papers-with-code（有 repo） | ⭐⭐ | tie-breaker |
| **L4 趋势** | alphaXiv / Twitter mentions | ⭐ | 仅 awareness，不进 score |

### 4.1 替换方案：**watchlist 作者自投 RSS**（最大改动，最高 ROI）

每个 121 个账号在 arXiv 上有自己的 author page，里面是他们投的所有 paper。这是**最干净的信号** —— 不可 brigade，因为 arXiv 投稿是 author-attested。

```python
# 替代 / 补充 fetch_hf_daily_archive
def fetch_watchlist_arxiv(boost_table, days_back=30):
    """For each watchlist author, query arxiv for their recent submissions.
    Returns papers with strong author signal (already +N from boost table)."""
    import arxiv
    out = {}
    for handle, entry in boost_table.items():
        name = entry['name']
        # arxiv author search by name
        search = arxiv.Search(
            query=f'au:"{name}"',
            max_results=10,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        for r in client.results(search):
            ...
    return out
```

可行性：132 个作者 × 10 paper = 1320 次 query，按 arxiv rate limit 8s/req 约 2.5 小时一次，**可接受为周更**而非日更。每周一刷新 watchlist-papers cache，每天 aggregate 时直接读 cache。

### 4.2 OpenReview 接入（低改动，定期生效）

OpenReview 有公开 API。在 NeurIPS/ICLR/CoRL/RSS review window（每年 6/9/11 月各一次），可拉到 reviewer scores 给 paper 加权。比 HF 投票权威得多——因为有 desk reject + 同行审阅。

```python
# Pseudo
import openreview
client = openreview.api.OpenReviewClient(baseurl='https://api2.openreview.net')
notes = client.get_notes(invitation='ICLR.cc/2026/Conference/-/Submission')
# 过滤 keyword 命中的 + 取 mean review score
```

可行性：venue 季节性，实际在每年 4 个 review window 有 burst 信号。**不替代日常 pipeline，但每年 4 次能补上 100+ 篇高质 paper**。

### 4.3 Semantic Scholar 引用图谱（中改动，稳定加分）

S2 API: 给定 paper id，查"who cited this"，反过来给"被你的 baseline 集合大量引用的 new paper"加分。

```python
# Workflow:
# 1. 你 Zotero 里已读的 100+ paper 当种子
# 2. 每天 query S2: papers citing >=3 of these seeds, published in last 30 days
# 3. 给这些 paper 加 cite-graph-bonus +5
```

这是 **time-decayed reading-list-aware ranking**，比纯关键词高一个 dimension。

### 4.4 HF Daily 降级为 tertiary

```python
# OLD
hf_bonus = min(p.get('hf_upvotes', 0), 50)

# NEW: cap at 5, not 50
hf_bonus = min(p.get('hf_upvotes', 0) // 20, 5)
# 即使 100+ upvote 也只 +5，让 author + topic + cite 信号占主导
```

---

## 5. 具体改动清单（按 ROI 排）

| # | 改动 | ROI | 工作量 | 影响 |
|---|---|---|---|---|
| 1 | **TOPIC_KW 重写为 Tier S/A/B/C + DROP**，加 named-model 列表 | ⭐⭐⭐⭐⭐ | 0.5h | 排序质量大幅提升 |
| 2 | **HF upvote cap 从 50 改 5** | ⭐⭐⭐⭐⭐ | 5min | 立即去除 brigading |
| 3 | **加 fetch_watchlist_arxiv（周更 cache）** | ⭐⭐⭐⭐ | 2–3h | 找回 HF 漏掉的高质 paper |
| 4 | **加 Semantic Scholar 引用信号** | ⭐⭐⭐⭐ | 2h | 自动跟随你已有兴趣 |
| 5 | OpenReview 季节性 burst（review window 期间生效） | ⭐⭐⭐ | 3h | 4 次/年补强 |
| 6 | papers-with-code "has-repo" tie-breaker | ⭐⭐ | 1h | 加 +1 给有代码 paper |
| 7 | **本次重跑历史 250 验证新打分** | ⭐⭐⭐⭐ | 0.5h | 看新打分是否真把好论文拉上来 |

### 立即可做（10 分钟内，不动管线）

1. 把 `aggregate.py` 里 `hf_bonus = min(p.get('hf_upvotes', 0), 10)` 改成 `min(... // 20, 5)`
2. 在 TOPIC_KW 第一组前面插入 Tier S 命中 +6（直接列 named-model 列表）
3. 把 `\b(?:offline RL|on-policy|...)\b` 改成 lookahead-gated 版本（要求同时命中 robot/polic）

### 一周内做（重写 aggregator）

4. 重写 TOPIC_KW 完整 4 层
5. 加 `scripts/refresh_watchlist_arxiv.sh` 周更脚本
6. 重跑 annual-2026-05-07 candidates，看 ranking 变化

---

## 6. 一句话总结

**当前的 TOPIC_KW 没本质错，错在权重设计太平 + named-model 列表停在 2024 + 一些关键词过度宽松（LLM-only RL 漏进来）+ HF upvote 比重过高。HF Daily 不是不能用，而是不该当主信号——主信号应该是「watchlist 作者自己 arXiv 投了什么」+「Semantic Scholar 引用图谱」，HF 降级为 tertiary tie-breaker。**
