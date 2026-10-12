"""Deep Agent package. See specs/05-agent-spec.md.

  runner.py        — poll loop + Evaluate Now consumer
  orchestrator.py  — score_bid (steps 2–6 for VENUE, CONTENT, EXPERIENCE)
  subagents/       — one per requirement type: VENUE, CONTENT, HOST, MUSIC,
                     EXPERIENCE (VENUE also covers safety and capacity)

Nothing here computes a quality, price or final score; that arithmetic lives in
app/domain/scoring.py (NFR-DET-1).
"""
