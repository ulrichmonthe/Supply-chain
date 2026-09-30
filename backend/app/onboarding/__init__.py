"""Data onboarding: messy ministry files in, a validated, provenance-tagged dataset out.

Built as a country-agnostic core plus a country pack filled in per country. A
deterministic pipeline does the work; an optional agent only proposes; a named
approver signs off before anything reaches the model; loading is a separate human
action. The guardrails in ``guardrails.py`` are enforced in code, not in a document.
"""

#: The provenance vocabulary. Every staged value carries one of these; only the first
#: four can be approved.
CLASSES = ("observed", "converted", "confirmed", "estimated", "illustrative", "missing")
APPROVABLE = {"observed", "converted", "confirmed", "estimated"}
DIRECTIONAL = "directional, not for budgeting"
DOMAINS = ("Nodes", "Edges", "Products", "Demand")
