# GPT-5.6-Sol vs Claude Opus 4.8

Date: 2026-07-10

Dataset: Paper Radar candidates from 2026-07-08

Sample size: 18

## Method

The experiment reused the production Top-18 candidate list and historical
Claude Opus 4.8 deep reads. GPT-5.6-Sol received the same paper full text,
deep-read skill, and reader profile. For each paper, a stable hash randomized
the two outputs as A/B. GPT-5.6-Sol then received the original paper plus both
anonymous outputs and scored factuality, core move, evidence, critique,
usefulness, and clarity.

All generation and judging calls were streamed. The run completed without a
failed paper. Production digest files were not modified.

## Result

GPT-5.6-Sol won 18, Claude Opus 4.8 won 0, with 0 ties.

| Dimension | Claude Opus 4.8 | GPT-5.6-Sol | Delta |
|---|---:|---:|---:|
| Factuality | 7.200 | 9.289 | +2.089 |
| Core move | 8.561 | 9.272 | +0.711 |
| Evidence | 8.006 | 9.406 | +1.400 |
| Critique | 7.922 | 9.378 | +1.456 |
| Usefulness | 8.183 | 9.639 | +1.456 |
| Clarity | 8.778 | 9.189 | +0.411 |

The largest observed gain was factuality. Recurring criticisms of the older
Opus outputs were unsupported author/institution background, table or protocol
misreads, and presenting inference as paper fact. GPT-5.6-Sol more consistently
anchored claims to source evidence and proposed executable FR3, ManiSkill3, and
RLinf experiments.

## Per-paper outcome

GPT-5.6-Sol won every pair. Judge confidence, in candidate order:

| arXiv | Confidence |
|---|---:|
| 2607.06558 | 0.95 |
| 2607.06370 | 0.94 |
| 2607.05511 | 0.97 |
| 2607.06291 | 0.97 |
| 2607.06560 | 0.95 |
| 2607.02963 | 0.91 |
| 2607.03451 | 0.96 |
| 2607.06262 | 0.94 |
| 2607.06403 | 0.97 |
| 2607.06559 | 0.93 |
| 2607.03530 | 0.91 |
| 2607.02980 | 0.97 |
| 2607.05468 | 0.94 |
| 2607.06442 | 0.94 |
| 2607.06537 | 0.96 |
| 2607.06564 | 0.97 |
| 2606.31329 | 0.94 |
| 2607.05869 | 0.96 |

## Caveat

GPT-5.6-Sol was both a contestant and the judge. A/B labels hid model identity,
but output style can still reveal model family and self-preference may remain.
The 18:0 result is strong evidence for migration in this workflow, not an
independent benchmark verdict. A future audit should use a third model or human
reviewer on a stratified subset.

## Stage 3 smoke test

GPT-5.6-Sol successfully ranked all 18 generated deep reads into a 36,763-character
`ranked.md`. Usage was 75,753 prompt tokens and 31,563 completion tokens; the
request completed through streaming without a gateway timeout.
