# App shell preview

A browser preview of the app's page states, for design work. An agent
can screenshot every screen in seconds without installing anything or
touching the machine.

## How it works

`serve.py` serves `app/src` unchanged and injects three script tags
into `index.html` on the way out:

1. `tauri-global.js`, the official Tauri API bundle, read from the
   pinned crate in the cargo registry at the version Cargo.lock names.
   It defines `window.__TAURI__`, including the `mocks` namespace.
2. `states.js`, one fixture per screen state, shaped like the `View`
   struct in `app/src-tauri/src/bootstrap.rs`.
3. `mock.js`, which routes `invoke` and the `shell-state` event through
   the fixtures.

`bootstrap.js` is deferred, so it runs after all three and finds a
bridge that answers. The files in `app/src` stay byte-identical, and
`frontendDist` ships that directory whole, so none of this ships.

## Run

```
python tools/preview/serve.py
```

Then open `http://127.0.0.1:4747/?state=gate`. The states are named in
`states.js`: `gate`, `gate-carried-uv`, `gate-override-notice`,
`running`, `failed-retry`, `failed-final`. In the console,
`window.preview.show("failed-retry")` steps the screen without a
reload, and pressing the consent button routes through the mock the
way the real shell moves.

## What this cannot prove

The data is fake and the renderer is your browser, not the app's
WebView2. Nothing checks the fixtures against the Rust `View` struct,
so a field rename over there goes stale here without an error. When a
design settles, verify it against the real app: launch it with
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333`
in one shell and attach over CDP. The full procedure and its limits
are in `reports/agent-driving-tauri-research-2026-08-21.md`.
