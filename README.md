# Paper Radar

Personal research-paper dashboard for robotics, reinforcement learning, and
vision-language-action models. The daily pipeline collects arXiv candidates,
scores them for a specific research stack, generates full-text deep reads,
fact-checks the output, and serves the resulting digests through a FastAPI and
vanilla-JavaScript application.

## Repository layout

- `frontend_new/`: production frontend source.
- `backend_new/`: production API and worker source.
- `paper_radar/`: pipeline modules.
- `run-daily.sh`: daily candidate, deep-read, review, and ranking workflow.
- `skill-paper-deep-read.md`: six-section deep-read instructions.
- `compare_gpt56_sol.py`: isolated, resumable Opus versus GPT-5.6-Sol A/B run.

Runtime credentials and generated digests are intentionally not stored here.
The production deployment is managed separately on the Paper Radar VPS.

## Model comparison

The comparison script reuses a historical day's candidates and existing Opus
outputs, generates GPT-5.6-Sol outputs from the same full text and prompt, then
randomizes each pair before GPT-5.6-Sol judges both versions against the source
paper. Generated experiment artifacts stay outside Git via `.gitignore`.

```bash
set -a
. ./deepread.env
set +a
python compare_gpt56_sol.py --date 2026-07-08 --count 18
```
