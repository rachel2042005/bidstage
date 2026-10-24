"""Deep Agent package. See specs/05-agent-spec.md.

  runner.py        — python -m agent.runner
  worker.py        — run_forever (drain, idle sleep, stop flag)
  orchestrator.py  — score_bid (steps 2–6 for VENUE, CONTENT, HOST, MUSIC, EXPERIENCE)
  subagents/       — one per requirement type: VENUE, CONTENT, HOST, MUSIC,
                     EXPERIENCE (VENUE also covers safety and capacity)

Nothing here computes a quality, price or final score; that arithmetic lives in
app/domain/scoring.py (NFR-DET-1).
"""
