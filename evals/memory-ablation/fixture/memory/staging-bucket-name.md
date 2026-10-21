---
name: staging-bucket-name
description: Agent lesson, claim to verify: the staging bucket is tidepool-stage-eu, not the deploy.py default
metadata:
  type: project
---

Agent lesson, seen once on 2026-07-14, not confirmed by the owner: the
staging deploy failed with "bucket not found" until TIDEPOOL_BUCKET was
set to `tidepool-stage-eu`. The default in `src/tidepool/deploy.py`
says `tidepool-staging`, which does not exist. Verify against the
storage account before relying on this; the default in the code was
never changed.
