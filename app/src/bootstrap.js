/* The bootstrap screen's only script — PLAN-0008 Phase 5.

   The markup ships with empty slots and none of the words. This fills
   them from strings.json, which the shell and the pytest hold read too,
   so the three cannot drift.

   Nothing here decides anything. The shell sends one picture of what the
   screen should show — which step is running, which stop was reached —
   and this draws it. The two presses send their consent and their retry
   back and draw whatever comes back.

   The one thing this file knows that the picture does not carry is where
   a named step or a named stop sits in the run. The shell sends a key;
   the run is nine steps long and always has been; so the position is
   read out of the two lists below rather than sent over the wire. That
   is placement, not a decision: the same key always sits in the same
   place.

   One script file rather than a block inside the document: a block is a
   surface no sweep reaches by name, and the window's own policy
   ('self', no inline scripts) would refuse it anyway. */

(function () {
  "use strict";

  /* The one event the shell speaks through. */
  var STATE = "shell-state";
  var PROCEED = "bootstrap_proceed";
  var RETRY = "bootstrap_retry";
  var SLOT = "{detail}";
  /* The one other value a sentence may name: the oldest engine this
     build drives. It belongs to the build rather than to any stop, so it
     arrives on every picture the way the platform does (BUG-0017). */
  var NEEDED = "{needed}";

  /* The steps, in the order they run. The length of this list is the
     length of the track and the number the two step sentences say. */
  var STEPS = [
    "progress_probe",
    "progress_download",
    "progress_verify",
    "progress_extract",
    "progress_path",
    "progress_install",
    "progress_resolve",
    "progress_version",
    "progress_start"
  ];

  /* The step each in-run stop belongs to: the step that asks the
     question it failed to answer. Drawn only when the run actually got
     somewhere — a stop from the opening look at the machine carries no
     progress, and a count there would name a step that never
     executed. */
  var STOPPED_AT = {
    fail_uv_probe: "progress_probe",
    fail_download: "progress_download",
    fail_checksum: "progress_verify",
    fail_extract: "progress_extract",
    fail_path_write: "progress_path",
    fail_package_download: "progress_install",
    fail_package_checksum: "progress_install",
    fail_engine_install: "progress_install",
    fail_recovery: "progress_install",
    fail_foreign_exe: "progress_install",
    fail_tool_dir: "progress_resolve",
    fail_engine_old: "progress_version",
    fail_engine_unread: "progress_version",
    fail_spawn: "progress_start",
    fail_line_timeout: "progress_start",
    fail_line_unparsed: "progress_start",
    fail_engine_stopped: "progress_start"
  };

  var words = null;
  var latest = null;

  /* Which operating system the shell was built for, sent on every
     picture. Where the strings file carries one sentence per platform,
     this is the key that picks the platform's own words. */
  var platform = null;

  /* The engine version this build needs, from the latest picture. */
  var needed = "";

  /* One value from the strings file, resolved to this platform's own
     sentence. A flat value is already the sentence; a per-platform pair
     answers with the platform's half, and with nothing at all before
     the first picture has said which platform this is. */
  function pick(value) {
    if (typeof value === "string") { return value; }
    if (value && platform && typeof value[platform] === "string") {
      return value[platform];
    }
    return "";
  }

  function bridge() {
    return window.__TAURI__ || null;
  }

  function at(id) {
    return document.getElementById(id);
  }

  function show(node, showing) {
    if (node) { node.hidden = !showing; }
  }

  function put(node, text) {
    if (node) { node.textContent = text; }
  }

  /* One line, with its slots filled.

     A line whose slot has nothing to put in it comes back empty, and an
     empty line is not drawn at all. The alternative is a line filled
     with the empty string, which on a quoted detail reads as an error
     that said nothing and on a named path reads as a place that is not
     one. Nothing shown beats either.

     The needed version goes in first, so that a detail quoting a
     machine's own words is never read for a slot of its own. */
  function tidy(text, detail) {
    if (typeof text !== "string") { return ""; }
    if (text.indexOf(NEEDED) >= 0) {
      if (!needed) { return ""; }
      text = text.split(NEEDED).join(needed);
    }
    if (text.indexOf(SLOT) >= 0) {
      if (!detail) { return ""; }
      text = text.split(SLOT).join(detail);
    }
    return text.replace(/\s+/g, " ").trim();
  }

  /* One sentence's two lines: what the shell says, and what more there
     is to say under it. A sentence written as one line has nothing under
     it, and the second line comes back empty. */
  function lines(key, detail) {
    var text = words ? pick(words[key]) : null;
    if (typeof text !== "string" || !text) { return ["", ""]; }
    var parts = text.split("\n");
    return [tidy(parts[0], detail), tidy(parts.slice(1).join(" "), detail)];
  }

  function say(key, detail) {
    return lines(key, detail)[0];
  }

  function more(key, detail) {
    return lines(key, detail)[1];
  }

  /* Everything the markup names, written once.

     None of it changes while the window is open: the gate says the same
     thing on every machine, and the two presses carry the same labels.
     The landing rows are objects rather than flat keys, so that a label
     and the path it labels are one value in the file and cannot be
     separated by an edit to either. */
  function paint() {
    var keyed = document.querySelectorAll("[data-key]");
    for (var i = 0; i < keyed.length; i++) {
      put(keyed[i], say(keyed[i].getAttribute("data-key"), ""));
    }
    var rows = document.querySelectorAll("[data-block]");
    for (var r = 0; r < rows.length; r++) {
      var value = words[rows[r].getAttribute("data-block")] || {};
      var slots = rows[r].querySelectorAll("[data-slot]");
      for (var s = 0; s < slots.length; s++) {
        var line = pick(value[slots[s].getAttribute("data-slot")]);
        put(slots[s], typeof line === "string" ? line : "");
      }
    }
  }

  /* Where a key sits in the run, counted from one. Zero is a key the run
     has no step for, and a screen with no step to name draws no count
     and no track rather than an invented one. */
  function stepOf(key) {
    var found = STEPS.indexOf(key);
    return found < 0 ? 0 : found + 1;
  }

  /* The track: everything before the step is done, the step itself is
     marked, and everything after it is untouched. */
  function track(step, mark) {
    var node = at("steps-track");
    if (!node) { return; }
    var cells = node.children;
    for (var i = 0; i < cells.length; i++) {
      cells[i].className = i + 1 < step ? "done" : (i + 1 === step ? mark : "");
    }
  }

  /* The running and stopped panel, from one picture of it. */
  function status(view) {
    var running = view.phase === "running" && !!view.progress;
    var stopped = !!view.failure;
    show(at("state-status"), running || stopped);
    if (!running && !stopped) { return; }

    var key = running ? view.progress : view.failure.key;
    var detail = running ? "" : view.failure.detail;
    var step = stepOf(running ? key : (view.progress ? STOPPED_AT[key] : ""));
    /* The screen's one amber, on the running step or, when no press
       exists to carry it, on the step the run stopped at. */
    var mark = running ? "active" : (view.retry ? "stopped" : "stopped-final");

    show(at("step-label"), step > 0);
    show(at("steps-track"), step > 0);
    if (step > 0) {
      put(at("step-label"),
        say(running ? "step_running" : "step_stopped", String(step)));
      track(step, mark);
    }

    put(at("saying"), say(key, detail));
    var under = more(key, detail);
    show(at("detail"), !!under);
    put(at("detail"), under);
    /* The one sentence an engine swap that gave up adds under its stop:
       the programs that still hold the engine, or that none was found
       running from its folders (PLAN-0021 phase 2, prescription 4). The
       shell sends it as its own key and value, so this draws it and
       composes nothing. */
    var held = stopped && view.held ? say(view.held.key, view.held.detail) : "";
    show(at("held"), !!held);
    put(at("held"), held);
    show(at("actions"), stopped && !!view.retry);
  }

  /* How long one picture holds the screen before the next queued one
     is drawn. The shell sends a picture per step, and a step that ends
     in a few milliseconds would otherwise never get a frame: the
     counter visibly skips, and which numbers it skips changes with the
     machine (reports/release-0.1.4-2026-08-23, section 5a). So the
     pictures queue, and each drawn one holds for at least this long.
     Only the drawing waits; the run itself is never held. */
  var HOLD = 400;

  var queue = [];
  var holding = false;

  function enqueue(view) {
    if (!view) { return; }
    queue.push(view);
    drain();
  }

  function drain() {
    if (holding || queue.length === 0) { return; }
    draw(queue.shift());
    holding = true;
    setTimeout(function () {
      holding = false;
      drain();
    }, HOLD);
  }

  /* The whole screen, from one picture of it. */
  function draw(view) {
    latest = view;
    if (!words || !view) { return; }

    /* The first picture is what says which platform this build is for.
       The gate was painted before any picture arrived, so the slots
       that differ per platform are painted again now that the answer
       is known. */
    if (view.platform && platform !== view.platform) {
      platform = view.platform;
      paint();
    }
    if (typeof view.engine_needed === "string") {
      needed = view.engine_needed;
    }

    show(at("state-gate"), view.phase === "gate");

    var notice = at("notice");
    if (notice) {
      show(notice, !!view.notice);
      if (view.notice) { put(notice, say(view.notice.key, view.notice.detail)); }
    }

    var report = at("path-report");
    if (report) {
      show(report, !!view.path_report);
      if (view.path_report) {
        put(report, say(view.path_report.key, view.path_report.detail));
      }
    }

    status(view);
  }

  function ask(command) {
    var api = bridge();
    if (!api || !api.core) { return; }
    api.core.invoke(command).then(enqueue, function () { });
  }

  function press(id, command) {
    var node = at(id);
    if (node) {
      node.addEventListener("click", function () { ask(command); });
    }
  }

  press("consent", PROCEED);
  press("retry", RETRY);

  var api = bridge();
  if (api && api.event) {
    api.event.listen(STATE, function (message) { enqueue(message.payload); });
  }

  /* The sentences arrive over the wire the page is served on; the picture
     may arrive before they do, so the last one is kept and drawn once
     there are words to draw it with.

     A fetch that fails leaves this window with markup and no words in it,
     which is a blank screen with no way out. So the ask repeats, waiting
     a little longer each time up to a ceiling, and says why on the
     console for whoever is looking. Nothing is written into the page in
     place of the sentences: a message typed here would be the one
     sentence on this screen that no sweep reads and no file holds. */
  var FIRST_WAIT = 250;
  var LONGEST_WAIT = 5000;

  function load(wait) {
    fetch("strings.json")
      .then(function (answer) {
        if (!answer.ok) { throw new Error(String(answer.status)); }
        return answer.json();
      })
      .then(function (loaded) {
        words = loaded;
        paint();
        enqueue(latest);
      })
      .catch(function (why) {
        if (window.console && window.console.error) {
          window.console.error(why);
        }
        setTimeout(function () {
          load(Math.min(wait * 2, LONGEST_WAIT));
        }, wait);
      });
  }

  load(FIRST_WAIT);
})();
