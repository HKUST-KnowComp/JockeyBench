# Program setting results

Main cohort of the Program setting: 26 models, 130 A and 130 S trajectories on the same 324 JRA races over 10 race days (2024-01-06 to 2024-02-03). Protocol `economic-v1`, projection `objective-v5`, tariff `score-yen-v2` (multiplier 0.5, hit bonus 100); see [Program reproduction](../docs/REPRODUCING.md).

Snapshot: 2026-10-18 16:58:57 EDT. Schedule SHA-256 `1f74656e91857339a1e1d429bee555db317c84909904d9f26dc9ea5d2695c808`.

## Main table

For this snapshot, each model contributes rounds 1–5 by round index. Complete later rounds appear only in the [robustness table](#robustness-rounds-beyond-the-main-table). Rows are sorted by mean log gain; the order is not a significance ranking.

| Model | Rounds A / S | Log gain ± SD | Share of v5 ref. | Brier skill ± SD | Top-1 | Top-3 precision | Program delivery | A − S [95% CI] | A − (S+Te) [95% CI] |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GPT-6.1-Sol | 5 / 5 | 0.525 ± 0.002 | 86% | 0.093 ± 0.002 | 28.5% | 48.3% | 100.0% | A ≡ S | −0.011 [−0.032, 0.009] |
| GPT-6-Astra | 5 / 5 | 0.523 ± 0.008 | 86% | 0.094 ± 0.003 | 30.0% | 48.0% | 100.0% | A ≡ S | −0.010 [−0.029, 0.010] |
| GPT-5.6-Sol | 5 / 5 | 0.473 ± 0.023 | 78% | 0.079 ± 0.003 | 27.8% | 45.9% | 100.0% | +0.004 [−0.005, 0.014] | +0.003 [−0.005, 0.011] |
| GPT-6-Sol | 5 / 5 | 0.471 ± 0.019 | 77% | 0.078 ± 0.009 | 27.3% | 45.4% | 100.0% | +0.009 [−0.000, 0.018] | −0.001 [−0.009, 0.008] |
| Claude-Opus-5 | 5 / 5 | 0.469 ± 0.039 | 77% | 0.078 ± 0.008 | 27.5% | 45.7% | 100.0% | −0.001 [−0.007, 0.005] | −0.003 [−0.010, 0.004] |
| Claude-Fable-5.1 | 5 / 5 | 0.456 ± 0.048 | 75% | 0.076 ± 0.012 | 26.7% | 45.6% | 100.0% | +0.007 [−0.018, 0.031] | +0.010 [−0.009, 0.029] |
| Claude-Opus-5.5 | 5 / 5 | 0.453 ± 0.030 | 74% | 0.078 ± 0.005 | 27.5% | 46.0% | 100.0% | −0.001 [−0.003, 0.001] | +0.003 [−0.010, 0.015] |
| GLM-5.3 | 5 / 5 | 0.401 ± 0.054 | 66% | 0.064 ± 0.009 | 24.6% | 43.5% | 100.0% | +0.017 [−0.006, 0.039] | +0.017 [−0.001, 0.035] |
| Qwen3.8-2.4T-A95B | 5 / 5 | 0.367 ± 0.048 | 60% | 0.058 ± 0.010 | 24.6% | 43.1% | 100.0% | +0.030 [0.002, 0.058] | +0.002 [−0.021, 0.024] |
| Hunyuan-4-Preview | 5 / 5 | 0.354 ± 0.202 | 58% | 0.060 ± 0.035 | 23.2% | 40.7% | 86.7% | +0.018 [−0.001, 0.037] | +0.013 [−0.002, 0.028] |
| Kimi-K3 | 5 / 5 | 0.348 ± 0.112 | 57% | 0.053 ± 0.024 | 24.8% | 42.7% | 100.0% | +0.354 [0.244, 0.464] | +0.071 [0.027, 0.115] |
| MiMo-V2.6-Pro | 5 / 5 | 0.338 ± 0.195 | 56% | 0.057 ± 0.031 | 24.6% | 42.9% | 100.0% | +0.203 [0.141, 0.265] | −0.009 [−0.024, 0.005] |
| DeepSeek-V4.1-Flash | 5 / 5 | 0.323 ± 0.130 | 53% | 0.051 ± 0.025 | 23.8% | 41.3% | 100.0% | −0.068 [−0.107, −0.029] | −0.071 [−0.111, −0.031] |
| GLM-5.3-Flash | 5 / 5 | 0.286 ± 0.225 | 47% | 0.037 ± 0.052 | 23.4% | 42.4% | 100.0% | +0.297 [0.190, 0.404] | +0.006 [−0.079, 0.092] |
| DeepSeek-V4-Pro | 5 / 5 | 0.270 ± 0.162 | 44% | 0.042 ± 0.033 | 21.0% | 39.1% | 99.9% | +0.166 [0.099, 0.232] | +0.082 [0.026, 0.137] |
| DeepSeek-V4-Flash | 5 / 5 | 0.242 ± 0.141 | 40% | 0.036 ± 0.020 | 21.5% | 40.0% | 100.0% | +0.010 [−0.051, 0.070] | −0.055 [−0.113, 0.003] |
| GPT-5.6-Luna | 5 / 5 | 0.214 ± 0.094 | 35% | 0.033 ± 0.015 | 22.8% | 41.0% | 100.0% | −0.014 [−0.034, 0.005] | −0.041 [−0.090, 0.008] |
| MiMo-V2.6-Flash | 5 / 5 | 0.202 ± 0.143 | 33% | 0.032 ± 0.024 | 17.3% | 36.8% | 98.3% | +0.008 [−0.010, 0.025] | +0.004 [−0.019, 0.027] |
| GPT-6-Luna | 5 / 5 | 0.200 ± 0.162 | 33% | 0.033 ± 0.027 | 18.6% | 36.9% | 95.1% | +0.035 [0.017, 0.053] | +0.038 [0.019, 0.057] |
| MiMo-V2.5-Pro | 5 / 5 | 0.179 ± 0.163 | 30% | 0.020 ± 0.040 | 24.1% | 41.6% | 99.8% | +0.367 [0.289, 0.445] | −0.035 [−0.078, 0.009] |
| GPT-5.6-Terra | 5 / 5 | 0.170 ± 0.119 | 28% | 0.025 ± 0.018 | 16.6% | 35.0% | 98.1% | −0.072 [−0.108, −0.037] | −0.068 [−0.103, −0.033] |
| Gemma-4-26B-A4B | 5 / 5 | 0.070 ± 0.063 | 11% | 0.007 ± 0.027 | 17.0% | 34.4% | 70.0% | −0.009 [−0.062, 0.043] | −0.016 [−0.076, 0.044] |
| Nemotron-3.5-Lightning | 5 / 5 | 0.000 ± 0.000 | 0% | 0.000 ± 0.000 | 7.3% | 23.4% | 0.0% | A ≡ S | A ≡ S+Te |
| Nemotron-3-Ultra | 5 / 5 | −0.083 ± 0.247 | −14% | −0.024 ± 0.055 | 14.9% | 31.7% | 98.4% | +0.359 [0.220, 0.498] | −0.079 [−0.204, 0.047] |
| Qwen3.5-35B-A3B | 5 / 5 | −0.218 ± 0.428 | −36% | −0.056 ± 0.108 | 12.4% | 28.0% | 89.1% | −0.234 [−0.535, 0.067] | −0.258 [−0.559, 0.044] |
| Qwen3.5-9B | 5 / 5 | −0.280 ± 0.291 | −46% | −0.053 ± 0.068 | 13.2% | 28.6% | 69.6% | −0.304 [−0.450, −0.158] | −0.314 [−0.462, −0.167] |


Not listed:

* Gemma-4-E4B (3 complete rounds): DeepInfra deprecated the model on 2026-10-01 and has since served gemma-4-31B-it-turbo under its name, so it cannot reach five rounds.
* Granite-4.2-8B (4 complete rounds): both round-5 attempts were rejected in warmup by DeepInfra's context window (prompts above 65,536 tokens once 65,536 output tokens are reserved), so it cannot reach five rounds without a protocol change.

A ≡ S (A ≡ S+Te): on every race the A arm predicted exactly what the S arm (its recalibrated version) did, because it never replaced its initial program or never delivered one.

## References

Fixed programs run through the same runtime and tariff on the same races (no model calls).

| Reference | Log gain | Share of v5 ref. | Brier skill | Top-1 | Top-3 precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| R0 | 0.000 | 0% | 0.000 | 7.1% | 23.4% |
| R1 | 0.155 | 25% | 0.024 | 15.7% | 35.7% |
| R2 | 0.343 | 56% | 0.061 | 23.8% | 42.0% |
| R3 | 0.298 | 49% | 0.032 | 21.0% | 41.7% |

## Robustness: rounds beyond the main table

Models with complete rounds beyond 5: the same statistics over rounds 1–5 (main) and over every complete round (all).

| Model | Rounds main → all | Log gain main → all | A − S main [95% CI] | A − S all [95% CI] | A − (S+Te) main [95% CI] | A − (S+Te) all [95% CI] |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Kimi-K3 | 5 → 6 | 0.348 → 0.350 | +0.354 [0.244, 0.464] | +0.316 [0.219, 0.413] | +0.071 [0.027, 0.115] | +0.082 [0.038, 0.126] |
| DeepSeek-V4-Pro | 5 → 6 | 0.270 → 0.266 | +0.166 [0.099, 0.232] | +0.138 [0.081, 0.195] | +0.082 [0.026, 0.137] | +0.067 [0.020, 0.114] |
| DeepSeek-V4-Flash | 5 → 6 | 0.242 → 0.232 | +0.010 [−0.051, 0.070] | +0.039 [−0.013, 0.090] | −0.055 [−0.113, 0.003] | −0.015 [−0.065, 0.034] |
| MiMo-V2.5-Pro | 5 → 6 | 0.179 → 0.207 | +0.367 [0.289, 0.445] | +0.304 [0.240, 0.368] | −0.035 [−0.078, 0.009] | −0.033 [−0.072, 0.007] |

## Definitions

* **Log gain:** mean over races of ln[n · (0.98 p + 0.02 / n)] for the winner, where p is the sealed win probability and n the field size; 0 is a uniform forecast. Tied winners average the smoothed probabilities first.
* **± SD:** sample standard deviation across a model's rounds. Rounds share the same races, so it describes run-to-run variability, not a confidence interval.
* **Share of v5 ref.:** log gain divided by 0.608, the log gain of a conditional logit fitted on the `objective-v5` fields of 20,730 earlier training races and scored on the same 324 races. It is relative to that fitted model only.
* **Brier skill:** (1/k − 1/n) − Σ(p̃ᵢ − yᵢ)² with the same smoothing; 0 is uniform.
* **Top-1 / Top-3 precision:** share of races whose top-ranked runner won / share of the predicted top three that finished in the top three.
* **Program delivery:** share of A-arm races predicted by a validated program; failed and default predictions remain in every score.
* **A − S:** A (daily revision) minus S (the frozen initial program) on the same races. Rounds are averaged within each race day, then a day-clustered sandwich standard error with a t(9) critical value gives the 95% interval. Pointwise, no multiple-comparison correction.
* **A − (S+Te):** A minus S recalibrated online: a temperature and a uniform mix fitted each day only on the trajectory's earlier released days (day 0 is the identity), with no model calls. It separates program revision from recalibration a fixed rule could do.

## Files

* [`program-results.json`](program-results.json): machine-readable aggregates for the three tables above (`main`, `references`, `robustness`), plus the snapshot, schedule and exclusions. Table aggregates are stored to six decimal places and rounded further for display here.

All values are aggregates of private trajectories. No race, runner or JRDB record is included.
