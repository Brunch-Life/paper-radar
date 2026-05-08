"""paper_radar — shared library for the paper-radar skill.

Lives at ~/.claude/skills/paper-radar/paper_radar/. Imported by the entry
scripts under ../scripts/. To make import work without installing, the
entry scripts do:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from paper_radar.scoring import topic_score, author_match, HF_UPVOTE_CAP
"""
