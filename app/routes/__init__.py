"""Flask blueprints (controllers). See specs/02-architecture.md §2.

Controllers authorize, build a command or query, dispatch it, and render.

Auth routes: /register, /login, /logout. Role gates live in access.py.
Business logic in a controller is a defect: a controller that computes a score
or decides a disqualification belongs in app/commands/ or app/domain/.
"""
