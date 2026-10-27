# Evidence-Based Maturity Model

A design target and a production certification are different assertions.

## Status lifecycle

PLANNED -> DESIGNED -> IMPLEMENTED -> TESTED -> VERIFIED -> PRODUCTION_PROVEN

Additional states: FAILED, EXPIRED, NOT_EVIDENCED.

## Formula

```text
maturity = sum(verified_control_weight) / sum(applicable_control_weight) * 100
```

A critical failed or expired control blocks production certification regardless of the numerical score.

## Reference weights

| Domain | Weight |
|---|---:|
| Business architecture | 7 |
| UX and accessibility | 9 |
| Decision intelligence | 23 |
| Enterprise architecture | 8 |
| Agent architecture | 10 |
| Workflow and orchestration | 8 |
| Data architecture | 7 |
| RAG and knowledge | 7 |
| Security | 10 |
| AI governance | 7 |
| Privacy | 5 |
| Reliability | 6 |
| Observability | 5 |
| Evaluation and assurance | 6 |
| DevSecOps | 4 |
| DR and resilience | 2 |
| Business value | 2 |
| Deployment | 4 |
| Independent assurance | 3 |
| Performance | 3 |
| **Total** | **136** |

The total is deliberately not normalised to 100. Because the score is verified
weight over total applicable weight, a control the platform cannot yet satisfy
only counts against it if it is present in the catalogue. Were the total pinned
at 100, admitting a new unmet control would require shrinking an existing one —
raising the per-control score for doing nothing. The last two domains exist
specifically to hold what the platform has not yet earned: it has never been
applied to a cluster, never independently assessed, and never run in
production. They are unevidenced today and are meant to be.
