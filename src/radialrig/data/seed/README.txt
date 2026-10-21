Seed sources for `radialrig mount` (PLAN-0005 Phase 2). The mount copies
these onto a host project with token substitution; the file list that
governs a mount is SEED_FILES in radialrig/mount.py, not this directory
listing. A file here that SEED_FILES does not name is not shipped to any
host, and a test fails on the mismatch.
