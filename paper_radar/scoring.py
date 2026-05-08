"""Single source of truth for paper-radar topic / author / HF scoring.

Tiered keyword design (replaces the flat TOPIC_KW that lived duplicated in
aggregate.py and annual_scan.py). Rationale in
data/KEYWORD_SOURCE_ANALYSIS.md.

Tier S (+6) — named systems / models. Hitting one of these almost always
means the paper is directly on-stack and worth deep-reading.

Tier A (+4) — core research-area keywords (paraphrase-resilient).

Tier B (+2) — adjacent topic, useful but not central.

Tier C (+1, gated) — weak signals like generic RL methods. ONLY scored
when robot/embodied/policy/manipulation context is also present, to keep
LLM-only RL papers out.

DROP (-8) — off-domain (bioinformatics / medical / FL / adversarial /
pure-NLP-without-robotics).

Score ranges: typical strong paper now lands at 18-35, with named-model
hits pushing past 40. Old design capped most papers at 8-12 with poor
discrimination.
"""

from __future__ import annotations

import re
from typing import Tuple, List

# ============================================================================
# Tier S — named systems (+6)
# ============================================================================
TIER_S = [
    # Physical Intelligence family
    (r'\b(?:π0\.5|π0|pi[- ]?0\.5|pi[- ]?0|pi[- ]?zero|pi[- ]?fast|π[- ]?RL|pi[- ]?RL)\b', 'pi-family'),
    # OpenVLA / RT family — require literal hyphen to avoid matching "RTX 3090"
    (r'\b(?:OpenVLA(?:[- _]?OFT)?|RT-[12X]|Octo[- ]?(?:[Mm]odel|polic|VLA))\b', 'openvla-rt'),
    # NVIDIA GR00T (N1 / N1.5 / N2 etc.)
    (r'\bGR00T(?:[- _]?N?[12](?:\.[05])?)?\b', 'gr00t'),
    # 2025–2026 hot VLAs surfaced from this past year of digests.
    # NOTE: ambiguous tokens (RDT, FASTER) require co-occurrence of robot-context.
    (r'\b(?:MolmoAct[2]?|Helix[- ]?(?:VLA|polic|model)?|EmbodiedOneVision|OneVL|Dream[- ]?VLA|HY[- ]?Embodied|GigaBrain[- ]?0(?:\.5M)?|Green[- ]?VLA|EBT[- ]?Policy|VLA[- ]?RFT|SimpleVLA[- ]?RL|RLinf[- ]?VLA|Q2RL|OGPO)\b', 'hot-vla-2026'),
    # Names that share their string with common English adjectives — gate on robot/VLA context.
    (r'(?=.*\b(?:robot|robotic|embod|manipul|VLA|teleop|grasp|locomotion|humanoid|gripper)\w*)\b(?:FASTER|RDT)\b', 'hot-vla-gated'),
    # World model named systems
    (r'\b(?:Genie[- ]?[123]|Veo[- ]?[123]?|Cosmos|V?[- ]?JEPA[- ]?[12]?|Sora|LWM)\b', 'world-model-named'),
    # Behavior models / Toyota / dual-action
    (r'\b(?:LBM|Large Behavior Model|WAM|World[- ]?Action[- ]?Model|DVA|Dual[- ]?Video[- ]?Action|LVP|Latent[- ]?Video[- ]?Pretraining)\b', 'lbm-wam'),
    # Frameworks the user actually uses
    (r'\b(?:RLinf|verl|veRL|TRL|HuggingFace TRL|LeRobot|le[- ]?robot)\b', 'rl-framework'),
]

# ============================================================================
# Tier A — core topic (+4) — paraphrase-resilient
# ============================================================================
TIER_A = [
    # VLA + paraphrase set
    (r'\b(?:vision[- ]language[- ]action|VLA|visuomotor polic|generalist polic|robot foundation model|generalist robot)\b', 'VLA-or-equiv'),
    # Real-robot / Real-world RL
    (r'\b(?:real[- ]world|real[- ]robot|on[- ]?robot)\s+(?:RL|reinforcement learning|fine[- ]tun|post[- ]train|adaptation)', 'real-robot-RL'),
    # RL post-training of VLA / policy (bidirectional pattern)
    (r'(?:post[- ]train|fine[- ]tun)\w*\s*\w*\s*\b(?:vla|polic|robot|manipulation)|\b(?:vla|robot|polic)\w*\s*\w*\s*(?:post[- ]train|fine[- ]tun)', 'post-train-policy'),
    # World model + RL coupling
    (r'\bworld model[s]?[^.]{0,80}?(?:reward|RL|polic|action)|(?:RL|polic)[^.]{0,80}?\bworld model', 'world-model-RL'),
    # Diffusion / flow policy (specific, not just any "diffusion")
    (r'\b(?:diffusion polic|flow[- ]match\w*\s*polic|polic\w*\s*flow[- ]match|EDM polic|score[- ]based polic)\b', 'diffusion-flow-policy'),
]

# ============================================================================
# Tier B — adjacent (+2)
# ============================================================================
TIER_B = [
    # Sim platforms (specific named ones the user cares about)
    (r'\b(?:ManiSkill[23]?|RLBench|RoboCasa|RoboSuite|Isaac (?:Gym|Lab|Sim)|MuJoCo|MetaWorld|CALVIN|LIBERO|SimplerEnv)\b', 'sim-platform'),
    # Sim2real
    (r'\b(?:sim[- ]?to[- ]?real|sim2real|domain randomization|cross[- ]embodiment)\b', 'sim2real'),
    # Imitation / data collection / teleop
    (r'\b(?:imitation learning|behavior cloning|GELLO|ALOHA|UMI|teleoperation|tele[- ]op)\b', 'imitation-data'),
    # Manipulation modes (more specific than just "manipulation")
    (r'\b(?:dexterous (?:manipulation|hand|grasp)|bimanual|dual[- ]arm|mobile manipulation|whole[- ]body|contact[- ]rich)\b', 'manipulation-modes'),
    # Humanoid / locomotion
    (r'\b(?:humanoid|biped|legged locomotion|quadruped robot|legged robot)\b', 'humanoid'),
    # Robots used in user's lab + RL/learning context
    (r'\b(?:Franka|FR3|UR[35e]|xArm|H1|G1|Unitree|Apptronik|Optimus|Figure)\b[^.]{0,80}?\b(?:RL|polic|learn|manipulation|fine[- ]tun)', 'lab-robots'),
]

# ============================================================================
# Tier C — gated weak signal (+1, ONLY when robot/embodied context co-occurs)
# ============================================================================
# Lookahead `(?=...)` requires the second pattern be present anywhere in
# the same text — avoids LLM-only RL papers grabbing +1.
TIER_C_GATED = [
    (r'(?=.*\b(?:robot|robotic|embod|manipul|VLA|teleop|grasp|locomotion|humanoid|gripper)\w*)\b(?:GRPO|DPO|PPO|SAC|RLOO|REINFORCE|TD-MPC|MPO|advantage actor critic)\b', 'rl-method-in-robot'),
    (r'(?=.*\b(?:robot|robotic|embod|manipul|VLA|teleop|grasp|locomotion|humanoid)\w*)\b(?:LLM|VLM|multimodal|large language model)\b', 'llm-for-robot'),
    (r'\b(?:3d (?:scene|representation)|point cloud)[^.]{0,80}?\b(?:robot|manipul|grasp|teleop)', '3d-for-robot'),
]

# ============================================================================
# Drop (-8) — off-domain papers
# ============================================================================
DROP_KW = [
    # Off-domain science
    r'\b(?:bioinformatic|protein folding|drug discovery|molecular dynamics|gene expression)\b',
    r'\b(?:medical imaging|radiolog|histopath|MRI|CT scan|electronic health record)\b',
    # Off-domain ML
    r'\b(?:federated learning|differential privac|homomorphic encrypt)\b',
    r'\b(?:adversarial attack|backdoor attack|jailbreak attack|red[- ]team)\b',
    # Pure NLP
    r'\b(?:machine translation|text summarization|sentiment analysis|named entity recognition)\b',
    r'\b(?:recommender system|search engine|ad ranking)\b',
]

# ============================================================================
# Tier weights
# ============================================================================
TIER_S_WEIGHT = 6
TIER_A_WEIGHT = 4
TIER_B_WEIGHT = 2
TIER_C_WEIGHT = 1
DROP_PENALTY = 8

# HuggingFace upvote cap — community vote can be brigaded; cap low so it
# can't dominate genuine topic/author signal. Was 50 in annual_scan,
# 10 in aggregate; unify to 10 (analysis doc proposed 5, but 10 keeps
# some community signal without letting it run away).
HF_UPVOTE_CAP = 10


# ============================================================================
# Public scoring functions
# ============================================================================
def topic_score(title: str, abstract: str) -> Tuple[int, List[str]]:
    """Return (score, list_of_matched_labels).

    Args:
        title:    paper title
        abstract: paper abstract (or any extra text to search)
    """
    text = (title + '\n' + abstract).lower()
    score = 0
    labels: List[str] = []

    for pat, label in TIER_S:
        if re.search(pat, text, flags=re.IGNORECASE):
            score += TIER_S_WEIGHT
            labels.append(f'S:{label}')

    for pat, label in TIER_A:
        if re.search(pat, text, flags=re.IGNORECASE):
            score += TIER_A_WEIGHT
            labels.append(f'A:{label}')

    for pat, label in TIER_B:
        if re.search(pat, text, flags=re.IGNORECASE):
            score += TIER_B_WEIGHT
            labels.append(f'B:{label}')

    for pat, label in TIER_C_GATED:
        if re.search(pat, text, flags=re.IGNORECASE | re.DOTALL):
            score += TIER_C_WEIGHT
            labels.append(f'C:{label}')

    for pat in DROP_KW:
        if re.search(pat, text, flags=re.IGNORECASE):
            score -= DROP_PENALTY
            labels.append('DROP-NEG')

    return score, labels


def author_match(authors: List[str], boost_table: dict) -> Tuple[int, List[str]]:
    """Sum author-boost scores for authors that appear in the watchlist.

    Args:
        authors: list of author names from the paper
        boost_table: { lowercased_name: { 'name': ..., 'handle': ..., 'score': N } }
    """
    total = 0
    hits: List[str] = []
    for a in authors:
        key = a.strip().lower()
        if key in boost_table:
            entry = boost_table[key]
            total += entry['score']
            hits.append(f"{entry['name']}(@{entry['handle']},+{entry['score']})")
    return total, hits


def hf_bonus(upvotes: int) -> int:
    """Capped HuggingFace Daily Papers upvote bonus."""
    return min(int(upvotes or 0), HF_UPVOTE_CAP)
