/* The mocked shell. Runs after tauri-global.js (which defines
   window.__TAURI__ and window.__TAURI__.mocks) and before bootstrap.js
   (which is deferred, so it runs after these classic scripts).

   Nothing here is served from app/src. The preview server injects the
   three script tags into index.html on the way out; the file on disk is
   untouched and the shipped bundle is unchanged. */
(function () {
  "use strict";

  var api = window.__TAURI__;
  var params = new URLSearchParams(window.location.search);
  var name = params.get("state") || "gate";
  var view = window.PREVIEW_STATES[name];

  if (!view) {
    console.error("[preview] no such state: " + name);
    view = window.PREVIEW_STATES.gate;
  }

  /* current is what the two commands answer with, so a press moves the
     screen the way the real shell moves it. */
  var current = view;

  api.mocks.mockIPC(function (cmd) {
    console.log("[preview] invoke " + cmd);
    if (cmd === "bootstrap_proceed") {
      current = window.PREVIEW_STATES.running;
      return current;
    }
    if (cmd === "bootstrap_retry") {
      current = window.PREVIEW_STATES.running;
      return current;
    }
    return null;
  }, { shouldMockEvents: true });

  /* The real shell emits on window load and repeats once 300 ms later,
     because the page registers its listener over the same round trip.
     Same here, for the same reason. */
  function emit() {
    api.event.emit("shell-state", current);
  }

  window.addEventListener("load", function () {
    emit();
    setTimeout(emit, 300);
  });

  /* A handle for the driver: an agent can step the screen without a
     reload. */
  window.preview = {
    states: Object.keys(window.PREVIEW_STATES),
    show: function (key) {
      current = window.PREVIEW_STATES[key] || current;
      emit();
      return key;
    }
  };
})();
