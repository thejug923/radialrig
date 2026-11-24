/* The install screen's only script — PLAN-0008 Phase 5, redrawn by
   PLAN-0027 phase 4.

   The markup ships with empty slots and none of the words. This fills
   them from strings.json, which the shell and the pytest hold read too,
   so the three cannot drift.

   Nothing here decides anything. The shell sends one picture of what the
   screen should show — which step is running, which stop was reached —
   and this draws it in one of three states: Ready, Working or Stopped.
   The two presses send their consent and their retry back and draw
   whatever comes back.

   The one thing this file knows that the picture does not carry is where
   a key sits among the four steps the screen names. The shell sends one
   of nine progress keys, as it always has; the screen groups them into
   Downloading, Checking the download, Installing and Starting, so the
   step is read out of the table below rather than sent over the wire.
   That is placement, not a decision: the same key always sits in the
   same place.

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
  /* Between a step's count and its name in Details. */
  var DOT = " · ";

  /* The nine keys the shell sends while a first install runs, in the
     order they run. */
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

  /* The four steps the screen names, in order. Their count is the
     number the two step sentences say. */
  var STEP_NAMES = ["step_download", "step_check", "step_install", "step_start"];

  /* Which of the four each key belongs to (wording.md section 4). The
     engine swap's own line replaces the engine, which is installing. */
  var STEP_OF = {
    progress_probe: 1,
    progress_download: 1,
    progress_verify: 2,
    progress_extract: 3,
    progress_path: 3,
    progress_install: 3,
    progress_resolve: 3,
    swap_running: 3,
    progress_version: 4,
    progress_start: 4
  };

  /* The engine swap's line, which gives the Working card its own
     heading and sentence. */
  var SWAP = "swap_running";

  /* The stop an engine sends when it ends while the window is open. The
     shell sends it only once the engine has named its address, and an
     engine that ends after the pass finished, with its pages up, ended
     no installation (PLAN-0027 phase 4 review). */
  var ENGINE_ENDED = "fail_engine_stopped";

  /* A swap that gave up while programs it names still hold the engine
     says so in its heading and its next step; the sentence that names
     them stays in Details, under the step (wording.md, Screen 1). */
  var HELD_BY_NAME = "swap_held";
  var HELD_STOP = "recovery_held";

  var words = null;
  var latest = null;

  /* Which operating system the shell was built for, sent on every
     picture. Where the strings file carries one sentence per platform,
     this is the key that picks the platform's own words. */
  var platform = null;

  /* The engine version this build needs, from the latest picture. */
  var needed = "";

  /* Whether the pass on screen is the engine swap. The swap's own line
     arrives once, and the steps after it (finding the program, its
     version, the start) belong to the same replacement, so the card
     keeps its heading until the pass ends. A fresh pass opens with no
     step at all, which is where this is cleared. */
  var replacing = false;

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
     with the empty string, which on an error line reads as an error
     that said nothing and on a named path reads as a place that is not
     one. Nothing shown beats either.

     The needed version goes in first, so that a detail quoting a
     machine's own words is never read for a slot of its own.

     Only the sentence's own spacing is tidied, and before anything goes
     in. A value goes in exactly as it came: a path with two spaces in a
     row names a different folder than the same path with one, and an
     error over several lines is the machine's own words (PLAN-0027
     phase 4 review). */
  function tidy(text, detail) {
    if (typeof text !== "string") { return ""; }
    text = text.replace(/\s+/g, " ").trim();
    if (text.indexOf(NEEDED) >= 0) {
      if (!needed) { return ""; }
      text = text.split(NEEDED).join(needed);
    }
    if (text.indexOf(SLOT) >= 0) {
      if (!detail) { return ""; }
      text = text.split(SLOT).join(detail);
    }
    return text;
  }

  /* One sentence's three parts, as the strings file splits them: what
     happened, what to do next, and the line Details shows. A sentence
     written as one line has nothing after it, and the later parts come
     back empty. */
  function lines(key, detail) {
    var text = words ? pick(words[key]) : null;
    if (typeof text !== "string" || !text) { return ["", "", ""]; }
    var parts = text.split("\n");
    return [
      tidy(parts[0], detail),
      tidy(parts[1], detail),
      tidy(parts.slice(2).join(" "), detail)
    ];
  }

  function say(key, detail) {
    return lines(key, detail)[0];
  }

  /* Everything the markup names, written once.

     None of it changes while the window is open: the masthead, the
     Ready card and the step names say the same thing on every machine,
     and the two presses carry the same labels. The landing rows are
     objects rather than flat keys, so that a label and the path it
     labels are one value in the file and cannot be separated by an edit
     to either. */
  function paint() {
    var keyed = document.querySelectorAll("[data-key]");
    for (var i = 0; i < keyed.length; i++) {
      put(keyed[i], say(keyed[i].getAttribute("data-key"), ""));
    }
    var labeled = document.querySelectorAll("[data-label]");
    for (var l = 0; l < labeled.length; l++) {
      labeled[l].setAttribute("aria-label",
        say(labeled[l].getAttribute("data-label"), ""));
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

  /* Which of the four steps a key belongs to, counted from one. Zero is
     a key no step stands for, and a screen with no step to name draws
     no count rather than an invented one. */
  function stepOf(key) {
    return STEP_OF[key] || 0;
  }

  /* The step's count and its name, as Details says them. */
  function counted(sentence, step) {
    return say(sentence, String(step)) + DOT + say(STEP_NAMES[step - 1], "");
  }

  /* The four steps: everything before the step is done, the step itself
     is current, and everything after it is still to come. */
  function track(step) {
    var node = at("progress");
    if (!node) { return; }
    var cells = node.children;
    for (var i = 0; i < cells.length; i++) {
      cells[i].className = i + 1 < step ? "done" : (i + 1 === step ? "current" : "");
      if (i + 1 === step) {
        cells[i].setAttribute("aria-current", "step");
      } else {
        cells[i].removeAttribute("aria-current");
      }
    }
  }

  /* The Working and Stopped card, from one picture of it. */
  function status(view) {
    var running = view.phase === "running" && !view.failure;
    var stopped = !!view.failure;
    show(at("state-status"), running || stopped);
    if (!running && !stopped) { return; }

    var key = running ? view.progress : view.failure.key;
    var detail = running ? "" : view.failure.detail;
    /* The step is the one the pass reached, on a stop as while it runs.
       The shell keeps it on the picture across a stop and clears it as
       each pass opens and as each one finishes, so a stop from the
       opening look, or one after the pass was done, counts no step. A
       stop's own key never chooses it: one key is sent from more than one
       place in the pass (PLAN-0027 phase 4 review). */
    var step = stepOf(view.progress);
    /* An engine that ended after its pages were up says what happened
       with no "Installation stopped" over it: the pass had finished, which
       is when the shell clears its step. */
    var ended = stopped && key === ENGINE_ENDED && !view.progress;

    show(at("stopped-row"), stopped && !ended);
    /* The steps are drawn while the run is going, and their current one
       is the screen's one amber; a stop says what happened instead. */
    show(at("progress"), running);
    show(at("actions"), stopped && !!view.retry);

    var heading = "";
    var saying = "";
    var doing = "";
    var held = "";
    var error = "";
    if (running) {
      if (replacing) {
        var swapped = lines(SWAP, "");
        heading = swapped[0];
        saying = swapped[1];
      } else {
        heading = say("working_head", "");
        saying = say("working_lede", "");
      }
      track(step);
      /* What the step is doing right now, in one line behind Details. */
      doing = STEPS.indexOf(key) >= 0 ? say(key, "") : "";
    } else {
      /* A swap that gave up while named programs still hold the engine
         heads its stop with what holds it; the machine's own words stay
         the failure's. */
      var named = view.held && view.held.key === HELD_BY_NAME;
      var said = lines(named ? HELD_STOP : key, detail);
      heading = said[0];
      saying = said[1];
      error = lines(key, detail)[2];
      held = view.held ? say(view.held.key, view.held.detail) : "";
    }

    put(at("heading"), heading);
    put(at("saying"), saying);
    show(at("saying"), !!saying);

    var count = step > 0 ? counted(running ? "step_running" : "step_stopped", step) : "";
    put(at("step-label"), count);
    show(at("step-label"), !!count);
    put(at("doing"), doing);
    show(at("doing"), !!doing);
    /* The one sentence an engine swap that gave up adds under its stop:
       the programs that still hold the engine, or that none was found
       running from its folders (PLAN-0021 phase 2, prescription 4). The
       shell sends it as its own key and value, so this draws it and
       composes nothing. */
    put(at("held"), held);
    show(at("held"), stopped && !!held);
    put(at("detail"), error);
    show(at("detail"), !!error);
    show(at("status-details"), !!(count || doing || held || error));
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
       The Ready card was painted before any picture arrived, so the
       slots that differ per platform are painted again now that the
       answer is known. */
    if (view.platform && platform !== view.platform) {
      platform = view.platform;
      paint();
    }
    if (typeof view.engine_needed === "string") {
      needed = view.engine_needed;
    }
    if (view.progress === SWAP) {
      replacing = true;
    } else if (view.phase === "gate" || (view.phase === "running" && !view.progress)) {
      replacing = false;
    }

    show(at("state-gate"), view.phase === "gate");

    var notice = at("notice");
    if (notice) {
      show(notice, !!view.notice);
      if (view.notice) { put(notice, say(view.notice.key, view.notice.detail)); }
    }

    /* What the PATH read found sits behind the Ready card's Details, on
       the platform whose step reads rather than writes. */
    var report = at("path-report");
    if (report) {
      show(at("gate-details"), !!view.path_report);
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
