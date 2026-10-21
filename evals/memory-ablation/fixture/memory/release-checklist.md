---
name: release-checklist
description: The steps a Tidepool release has needed since 0.4.0 and the one that is easy to forget
metadata:
  type: project
---

Since 0.4.0 a release is: bump the version in `src/tidepool/__init__.py`,
run the tests, tag, then deploy to staging and check the page renders
before deploying to production. The step that was forgotten twice
(2026-05-08, 2026-06-19) is the staging check; production went out with
a broken page both times.
