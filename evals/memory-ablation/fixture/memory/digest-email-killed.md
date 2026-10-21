---
name: digest-email-killed
description: The owner killed the sync-failure email digest on 2026-06-02; never re-propose it or anything that sends mail on a schedule
metadata:
  type: feedback
---

On 2026-06-02 an agent proposed a nightly email digest of sync failures.
The owner killed it: "I read the status page every morning. Mail is
noise." The kill covers the digest and anything like it: scheduled
mail, alert emails, a mail-on-failure step in the nightly run.

**Why:** the owner already has one place to look and does not want a
second channel to keep up with.

**How to apply:** never propose, design or carry forward a scheduled or
triggered email from Tidepool. Improvements to the status page itself
are welcome.
