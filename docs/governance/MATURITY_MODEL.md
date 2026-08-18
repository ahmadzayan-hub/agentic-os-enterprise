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
| UX and accessibility | 7 |
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
| Evaluation and assurance | 5 |
| DevSecOps | 4 |
| DR and resilience | 2 |
| Business value | 2 |
| **Total** | **100** |
