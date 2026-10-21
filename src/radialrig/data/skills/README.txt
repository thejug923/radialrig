Skill sources for `radialrig skill add` (D-36, PLAN-0006). The kit copies
these onto a host project with no token substitution at all, so the bytes
a host receives hash identically to the bytes shipped here; the file list
that governs an install is SKILL_CATALOG in radialrig/skills.py, not this
directory listing. A file here that SKILL_CATALOG does not name is not
shipped to any host, and a test fails on the mismatch.
