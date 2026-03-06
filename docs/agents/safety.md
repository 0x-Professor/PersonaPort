# Safety Rules

PersonaPort is safety-sensitive because it automates browser sessions for AI platforms.

Non-negotiable rules:

- Keep all data local by default.
- Never hard-code or log credentials, tokens, cookies, or session state.
- Preserve the README and CLI warning language.
- Prefer official export flows first.
- Keep unsafe scraping and injection behind explicit confirmation or opt-in flags.

High-risk areas that always need careful review:

- `personaport/browser/`
- session-state persistence
- provider key storage and lookup
- auth flows and login/logout behavior
- export scraping fallbacks

Review expectations:

- Low-risk changes may be auto-merged after maintainer review and passing GitHub checks.
- Human review is still required for auth, session state, provider key, and browser automation changes.
- If a change touches auth, session state, provider keys, or browser automation, mention that risk explicitly in the proof-of-work summary.
