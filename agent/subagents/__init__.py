"""One sub-agent per requirement type. See specs/05-agent-spec.md §5.

Planned: venue.py, content.py, host.py, music.py, experience.py

Each receives the requirement, the supplier's answer and the rubric, and returns
the structured verdict in §6. None receives a price: their input comes from
rm_bid_for_assessment, which has no price column.

Use the requirement-evaluator skill when adding a type.
"""
