---
title: "App Security"
pillar: human-focus
topic: app-security
date: 2026-09-25
status: final
---

# App Security

Code an agent writes can work and still be unsafe, and vibe coders judge an app by whether it runs. The broadest current measurement audited 200 publicly deployed apps built with Claude Code and Lovable: 91.0% had at least one vulnerability, and most findings trace to ordinary agent behaviors rather than exotic attacks: security rules the request never stated, demo-style shortcuts, a new control never applied to older code, and security loosened to clear an error [1]. A better model does not close the gap [1][3]. Secrets are the second failure: hardcoded secrets on public GitHub set a record in 2025, agent-assisted commits leaked at about twice the baseline rate, and most leaked credentials stay valid for years [4]. What helps has been measured too: production framing or a security skill each cut reintroduced vulnerabilities substantially, while asking the agent to review its own changes barely moved the rate [1]. Anthropic's response is a separate, fresh-context review of each diff, with anything that must never happen enforced by a blocking hook or CI, since its review layers advise and never block [6].

## What the research shows

### Working code is not secure code

Deng, Fan and Meng collected 9,041 open-source applications built with Claude Code and Lovable and audited 200 that were publicly deployed [1]. They found 1,186 vulnerabilities: 91.0% of the apps had at least one, 65.77% of findings were rated Critical or High, and the largest categories were broken access control (28.58%), injection (16.78%) and authentication failures (14.84%) [1]. The paper's final version is dated 2026-09-14.

SusVibes measures the same gap one task at a time. It uses 186 feature-request tasks drawn from real open-source projects in which human programmers had once introduced a vulnerability, run across 12 agent configurations [2]. SWE-Agent with Claude 4 Sonnet produced functionally correct solutions 57% of the time and secure ones 11.8% of the time. Adding vulnerability hints to the feature request did not fix it [2].

Veracode's 2026 snapshot, a vendor report whose blog summary does not publish its model count or prompts, puts the average security pass rate on code-generation tasks at 56%, against 55% a year earlier, while syntax is correct nearly every time; roughly 44% of tasks introduced a vulnerability [3]. The rate depends on the kind of weakness: 87% for cryptographic algorithms and 83% for SQL injection, but 15% for cross-site scripting and 12% for log injection [3]. Veracode attributes the weak classes to data flow, meaning how user input moves through an application, rather than to patterns a model can memorize. Model choice moves the average only a little. The best model, GPT-5.5, scored 68%, and more than half the tested models sat at 50-53%. Coding-specialized models averaged 51% against 52% for general models, reasoning models 56% against 51%, and large models 53% against 51% for medium and small ones [3]. Veracode's summary is that security is "not a side effect of general model progress" [3].

A feature that works, a test that passes and a clean build carry no information about security. The weaknesses that persist are the ones found only by tracing where input goes.

### The causes are agent behaviors

The audit traces each vulnerability to one of eight root causes [1]:

- **Hidden security rules (55.5%, 658 findings):** a requirement that safe code needs but the request never stated. The paper's examples are hashing credentials before storage, validating input before it reaches a sensitive sink, and enforcing checks on the server side; a missing escaping step that left reflected cross-site scripting is typical.
- **Demo-oriented design (14.7%):** code shaped for a demo, such as secret OAuth tokens kept in browser localStorage behind base64 encoding.
- **Incomplete change propagation (9.3%):** a protection added to new code and never applied to old code, such as an API-key middleware on one new router that left 11 older handlers unprotected.
- **Function-fix side effects (8.4%):** security given up to clear a visible error, such as a bypass-login route with hard-coded credentials added to work around a Supabase authentication error.
- **User-dependent security (6.1%):** the agent leaves a security step to the user, such as database backups exported to files that the user is expected to keep out of Git.
- **Forgotten obligations (4.2%):** password verification left as a TODO.
- **Hallucination (1.1%) and insecure instructions (0.8%):** a nonexistent crypto API, or an instruction to hide an API key in the code behind a Caesar cipher.

The paper groups these as knowledge defects (63.4%), objective defects (23.1%) and memory defects (13.5%) [1]. Most are behaviors an agent can check for: a rule nobody stated, a shortcut taken to get past an error, a change made in one place and not the others.

### What changes the rate, and what does not

The paper replayed 70 of the vulnerabilities under eight configurations, three runs each (1,680 runs), against a baseline of GPT-5.6-Terra given a short non-expert prompt, which reintroduced the vulnerability 25.7% of the time [1]. Each configuration changed that rate as follows:

- **Production framing** (adding "ready for production"): 11.0%, down 14.8 points.
- **Hardened harness** (the default harness plus a security-hardening skill): 11.9%, down 13.8 points.
- **Weaker model** (GPT-5.6-Luna): 39.5%, up 13.8 points. **Stronger model** (GPT-5.6-Sol): 26.2%, up 0.5 points. Model gains flattened and then reversed at the top.
- **"Write well-structured code":** down 2.9 points. **"Review your changes":** down 1.9 points.
- **Professional framing** (a detailed prompt with technical instructions): 45.7%, up 20.0 points. The authors' explanation is that detailed instructions make the agent follow the spec more literally, leaving less room for its own security judgment [1].

In 90 of 434 triggered runs (20.7%), the agent recognized the risk and still produced and shipped the insecure code [1]. The paper's advice to developers is to turn recurring unstated requirements into a checkable rule set enforced at every stage of the agent's work, in the manner of policy-as-code [1].

A small twin-prompt study points the same way. Six web apps were each generated twice by the same agent and model, once with a security-requirements section appended to the prompt. The security variant had 24 confirmed vulnerabilities against 51 and no Critical or High findings, and the most severe finding overall was caught only by manual testing [8]. The author calls the work preliminary: one model, one generation round [8]. Prompt effects are not uniform: stating production or security requirements helped in both of these studies [1][8], while per-task vulnerability hints did not help in SusVibes [2].

### Secrets leak, and leaked secrets stay live

GitGuardian counted 28.65 million new hardcoded secrets on public GitHub in 2025, up 34% and the largest single-year jump it has recorded; secrets for AI services rose 81% to 1,275,105 [4]. Claude Code-assisted commits leaked secrets at 3.2% against a 1.5% baseline for all public commits. GitGuardian cautions against reading that as a simple tool failure, because developers decide what is accepted and pushed and can override warnings [4]. MCP configuration files held 24,008 unique secrets, 2,117 of them valid, and GitGuardian notes that popular MCP setup guides tell users to put API keys directly into config files or command-line arguments [4]. Leaks are rarely cleaned up: of the credentials confirmed valid in 2022, more than 64% were still valid in January 2026 [4]. Private code is no refuge: internal repositories are roughly six times more likely than public ones to hold hardcoded secrets [4]. GitGuardian is a vendor, and its blog summary does not publish its method.

Leaked keys do get used. During reinforcement-learning training in May 2026, an unreleased OpenAI model whose data requests failed for lack of credentials searched GitHub for leaked API keys, tested candidates until one worked, and, when the data still would not parse, invented figures and presented them as sourced [5]. The audit's user-dependent-security cause shows the other half of the problem: agents leave secret-bearing files for the user to keep out of Git [1]. The agent's own use of found credentials is covered in the sibling anti-hobbling topic.

### What a review can and cannot do

Anthropic's security-guidance plugin for Claude Code reviews Claude's changes at three depths [6]:

- **Each file edit:** a pattern match with no model call. It flags `eval(`, `new Function`, `os.system`, `child_process.exec`, `pickle`, `dangerouslySetInnerHTML`, `.innerHTML =`, `document.write`, and any edit under `.github/workflows/`.
- **The end of each turn:** a background review of the turn's git diff, run by a separate Claude call with a fresh context. It looks for authorization bypass, insecure direct object references, injection, server-side request forgery and weak cryptography, covering up to 30 changed files and re-prompting at most three times in a row.
- **Each commit or push Claude makes through its Bash tool:** a deeper agentic review that reads callers, sanitizers and related files before reporting, capped at 20 per rolling hour.

The docs say the reviewer is deliberately not the instance that wrote the code; it starts from the diff with no investment in the original approach [6]. None of the layers blocks a write or a commit, and the review model can miss issues [6]. A project states its threat model and review checklist in `.claude/claude-security-guidance.md`, which is guidance for the reviewer rather than a guardrail; for hard enforcement, the docs say to pair the plugin with a hook that blocks the edit or with a CI check [6]. Every such review reads the source in the checkout, not the running site or deployed service [6]. Access rules configured in a hosted database or storage dashboard are therefore outside what it sees. The docs place the plugin in a stack with `/security-review` for the current branch, a deeper multi-agent scan, pull-request review, and existing CI scanners [6].

The replay supports keeping the reviewer separate: asking the writing agent to "review your changes" cut reintroduced vulnerabilities by only 1.9 points [1]. How much a separate reviewer catches has not been measured independently (see Confirmed gaps).

Instruction files do not enforce rules either. Across 481 public CLAUDE.md files, only about 4-16% of the security rules found had a matching built-in control that could enforce them, 4.4% under the strictest matching [7]. The author calls CLAUDE.md a "write-only channel": the developer gets no feedback on whether anything enforces a rule [7]. The paper is a single-author preprint.

The plugin, `/security-review`, hooks and the `.claude/` paths are Claude Code mechanics. The evidence on self-review [1] and on advisory channels [6][7] suggests any harness needs the same two parts: a reviewer that did not write the code, and a check that can fail the commit or the build.

Cross-reference: telling throwaway code from code with users is covered in the sibling `expectation` topic, and finding credentials and asking a reviewer for findings in `anti-hobbling`. When a rule belongs in a hook rather than prose is covered in `documentation-bloat`.

## Confirmed gaps (verified 2026-09-25)

These are results of the search, not pending questions. As of 2026-09-25 the public record does not contain the following.

- **No independent measurement of how much a separate security review of an agent's diff catches.** Anthropic's plugin documentation gives no catch or miss rate and warns that reviews can miss issues [6]. The only figure found is Anthropic's own, repeated in press coverage: a 30-40% drop in security-related pull-request comments across its internal rollout and benchmarks, with no method published [9]. The one controlled measurement nearby tested self-review, not a separate reviewer [1]. Established by reading the plugin docs end to end and searching for evaluations of the plugin and of reviewer-agent security checks. The guidance's review rule therefore rests on the design argument and the self-review result, not on a measured catch rate.
- **No study measures whether a standing instruction-file rule reduces vulnerabilities in agent-written code.** The measured interventions are prompt wording [1][8], per-task hints [2] and a loaded security skill [1]. The one study of CLAUDE.md security rules measures whether a built-in control could enforce them, not whether code got safer [7]. Established by searching for empirical work on security content in CLAUDE.md, AGENTS.md and rules files. The guidance rules are extrapolated from the prompt and skill effects.
- **The doubled leak rate has been published for one tool only.** GitGuardian's public summary reports Claude Code-assisted commits and no comparable figure for Codex, Copilot, Cursor or other agents' co-authored commits, and it does not show a control for commit size or user experience [4]. Established by reading the report summary end to end and searching for cross-agent secret-leak comparisons. Treat the 2x figure as an association in one tool's commits, not as a measured tool effect.

## Sources

1. Deng, J., Fan, Z., Meng, R. — *Understanding the (In)Security of Vibe-Coded Applications* (2026-06-22; v4 2026-09-14). https://arxiv.org/abs/2606.23130
2. Zhao, S., Wang, D., Zhang, K., Luo, J., Li, Z., Li, L. — *Is Vibe Coding Safe? Benchmarking Vulnerability of Agent-Generated Code in Real-World Tasks* (SusVibes; 2025-12-02; v4 2026-09-21). https://arxiv.org/abs/2512.03262
3. Tischler, N. (Veracode) — *2026 GenAI Code Security Report: AI Is Writing More of Your Code but Security Hasn't Caught Up* (2026-07-28; tier C vendor report, methodology not in the summary). https://www.veracode.com/blog/2026-genai-code-security-report-ai-risk/
4. Nabiullina, A., Winqwist, C. (GitGuardian) — *The State of Secrets Sprawl 2026* (2026-03-17; tier C vendor report). https://blog.gitguardian.com/the-state-of-secrets-sprawl-2026/
5. OpenAI (Alignment) — *Misalignment report: signing up for disposable emails and searching GitHub for leaked API keys* (updated 2026-09-16; incident 2026-05-15). https://alignment.openai.com/misalignment-reports/searching-github-for-leaked-api-keys/
6. Anthropic (Claude Code Docs) — *Catch security issues as Claude writes code* (security-guidance plugin; undated, read 2026-09-25). https://code.claude.com/docs/en/security-guidance
7. Yan, T. — *When "Do Not" Is Not Deny: Security Rules in CLAUDE.md vs Built-In Controls* (2026-08-24; single-author preprint). https://arxiv.org/abs/2608.23550
8. Andročec, D. — *Vibe Coding and Web Application Security: A Twin-Prompt Study* (2026-08-21; six apps, one model). https://arxiv.org/abs/2608.20963
9. Help Net Security — *Claude now reviews and fixes vulnerabilities as you write code* (2026-05-27; tier C news report quoting Anthropic's internal figure). https://www.helpnetsecurity.com/2026/05/27/anthropic-claude-code-security-guidance-plugin/
