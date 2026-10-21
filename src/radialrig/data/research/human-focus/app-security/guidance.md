---
topic: app-security
pillar: human-focus
audience: agent
load-when: Writing or changing code that handles logins, permissions, user data, user input, payments or secrets; or telling someone an app is ready to share or deploy.
evidence: ./app-security.md
date: 2026-09-27
status: final
---

# App Security — Agent Rules

## Write the security the request left out

- For code that will have users (see the expectation guidance for telling it from throwaway code), treat the security rules a request leaves unstated as required; a detailed spec that says nothing about security does not waive them.
- Check permission on the server for every request that reads or changes data, validate input before it reaches a database, file, or command, and hash stored passwords, whether or not the request asks for it.
- Never weaken authentication, authorization, or input validation to clear an error: no bypass route, hard-coded login, skipped check, or widened access rule. Fix the cause, or stop and tell the person which check fails.
- When you add a security control (an auth middleware, a permission check, a rate limit), apply it to every existing route or handler it should cover, and list them in your report.
- Trace each place user input reaches HTML, SQL, a shell command, a file path, or a log line, and escape, parameterize, or encode it there. Cross-site scripting and log injection are where models fail most, and a larger model does not fix them.
- When you notice a security risk, fix it or tell the person before you finish; never note it and ship.

## Keep secrets out of the code

- Never write a secret (API key, token, password, connection string) into source, config, or MCP server files, even in a private repository. Read it from an environment variable or secret store, and keep the file that holds it out of git.
- Never keep a secret in browser storage or in code that runs in the browser; encoding such as base64 is not protection.
- Before each commit, scan the diff for keys and credential-shaped strings.
- If a secret was ever committed or pushed, tell the person plainly that it must be revoked and replaced at the provider. Deleting it from the code or the history does not un-leak it.

## Review before calling it ready

- Never treat working features, passing tests, or a clean build as evidence of security.
- Before telling someone an app is ready to share or deploy, get a security review of the diff from a reviewer that did not write it: a fresh-context subagent or the harness's review tool (in Claude Code, `/security-review` or the security-guidance plugin). Fix what it finds. Reviewing your own changes barely helps, and the rule in the expectation guidance against extra verify steps on short work does not waive this gate.
- Report which routes, handlers, and data stores the review covered and which it did not.
- A code review reads the repository, not the running service. When database or storage access rules live in a hosted dashboard, check them there or say they were not checked.
- Put a rule that must never be broken (no secrets in commits, admin routes check the role) in a blocking hook or CI check, not only in an instruction file or review guidance; review findings advise and never block. See the documentation-bloat guidance for what belongs in a hook.
