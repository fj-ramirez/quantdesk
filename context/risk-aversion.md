# Risk aversion: how to evaluate and report

How to answer "will this work?" and how to report what is wrong. It applies to every task, not
one area of the code.

1. **No generic disclaimers.** No "trading involves substantial risk" or "make sure to test
   thoroughly before production" unless asked. Assume professional competence, and do not hunt
   for reasons something won't work in abstract edge cases.

2. **Specific risks are findings, not hedging.** A race condition, a missing stop-loss, a
   volume-cap breach, a point-in-time leak, an `as_of` overwrite, a result below its noise
   ceiling, a shared type change pushed without `npm run build`: state each as a defect, with
   the direct fix. The executor trades a live 1:1000 account (invariant 11), so a concrete
   order-path defect is always reported.

3. **Engineering questions** ("will this pipeline work?") are judged on algorithmic complexity,
   data-flow integrity, concurrency limits and mathematical correctness. Point out a structural
   bottleneck or race technically and give the code fix; don't lecture on risk management.

4. **Edge questions** ("will this strategy work?") are judged by EdgeLab's gates (invariant 9):
   the noise ceiling, the doubled-cost gate, walk-forward and the roll-gap caveat, then the
   `edge-research` skill's forward tests. That skepticism is the product, not pessimism. Clean
   code does not make a backtest an edge.

5. **Trade-offs as a decision matrix**, not as a warning against proceeding: for example memory
   against speed in the GEX engine, or throughput against latency in a worker. Give a
   recommendation.

6. **Pragmatic delivery.** Clean, idiomatic, fast Python and TypeScript that matches the
   surrounding code.
