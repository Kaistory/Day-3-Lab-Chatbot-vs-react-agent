# Ablation: ReAct reliability guardrails

## Exp 1+2 — non-terminating model (always repeats one Action)

| Config                             | LLM calls | Tool execs | Grounded |
| ---------------------------------- | --------: | ---------: | -------: |
| baseline (no guard, max_steps=6)   |         6 |          6 |      yes |
| + loop-guard (repeat>2)            |         6 |          2 |      yes |
| + loop-guard + max_steps=3         |         3 |          2 |      yes |

  -> loop-guard cut redundant tool executions by 4 (6 -> 2).
  -> tighter max_steps cut LLM calls by 3 (6 -> 3).
  -> all configs still returned GROUNDED data via last-observation fallback: base=True, guard=True, tight=True.

## Exp 3 — well-behaved model (concludes on step 3)

| Config                             | LLM calls | Tool execs | Grounded |
| ---------------------------------- | --------: | ---------: | -------: |
| max_steps=2 (too tight)            |         2 |          2 |      yes |
| max_steps=5 (adequate)             |         3 |          2 |      yes |

  -> too-tight cap stopped before the model's Final Answer but the fallback still surfaced grounded data (grounded=True); adequate cap let it conclude cleanly in 3 calls.
