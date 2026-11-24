/* The setup page's only script — PLAN-0005 Phase 4.

   The page works without it: every control is a form the server answers.
   This adds three conveniences and nothing that can change the outcome.

   1. Copy buttons for the next-step commands. The button is removed when
      the browser has no clipboard API, so a dead control never shows.
   2. A close beacon. Leaving the page tells the local server to stop, so
      a walkthrough abandoned before the mount does not leave it running.
      The server also stops on a terminal state and on Ctrl-C.
   3. "Add all" at the head of a column of the skill grid on Add a
      project, which ticks that agent's boxes and nothing else; the form
      posts what is ticked. Details follows the boxes.

   A page whose root carries a render id posts that id back with the
   close, so a server that stands rather than ends can tell a reload's
   close — sent by a document its own reload already replaced — from a
   reader closing the page. A page with no such attribute posts the body
   it has always posted, byte for byte. */

(function () {
  "use strict";

  var root = document.documentElement;
  var token = root.getAttribute("data-token") || "";
  var endpoint = root.getAttribute("data-endpoint") || "/setup";
  var terminal = root.getAttribute("data-terminal") === "1";
  var render = root.getAttribute("data-render") || "";

  var canCopy = !!(navigator.clipboard && navigator.clipboard.writeText);
  var buttons = document.querySelectorAll("button.copy");
  for (var i = 0; i < buttons.length; i++) {
    (function (button) {
      if (!canCopy) {
        button.remove();
        return;
      }
      button.addEventListener("click", function () {
        var text = button.getAttribute("data-copy") || "";
        navigator.clipboard.writeText(text).then(
          function () {
            var was = button.textContent;
            button.textContent = "Copied";
            window.setTimeout(function () { button.textContent = was; }, 1600);
          },
          function () { button.textContent = "Copy failed"; }
        );
      });
    })(buttons[i]);
  }

  /* The desktop shell's folder picker, asked for from the page. The
     Browse button ships hidden: only that shell can open a folder, and
     it reveals the button itself once the page has loaded inside its
     window. A press asks through the one channel a page has back to the
     shell - the document's own name - and puts the name back the moment
     it has been read, so nothing a reader sees carries the marker. */
  var browse = document.getElementById("browse");
  if (browse) {
    browse.addEventListener("click", function () {
      var was = document.title;
      document.title = "radialrig-app.pick-folder";
      window.setTimeout(function () { document.title = was; }, 0);
    });
  }

  /* The press under the folder field stays off until the field names a
     folder (PLAN-0014 phase 1). The server renders it enabled, so a page
     with no script keeps the bare submit it has always had, answered
     with the listing; here the press follows the field's own `input`
     event, which a typed path fires and the shell's picker dispatches
     after writing the path it chose. */
  var field = document.getElementById("root");
  var press = field && field.form ? field.form.querySelector("button.primary") : null;
  if (field && press) {
    var follow = function () { press.disabled = field.value.trim() === ""; };
    field.addEventListener("input", follow);
    follow();
  }

  /* On Projects the field sits in Add a project's Details, closed until
     it is opened. The shell's menu can open the picker with it closed,
     and the path it writes would land out of sight, so a path arriving
     opens the Details that holds the field (PLAN-0027 phase 5 review P1).
     A typed path arrives with it open already. */
  if (field && field.closest) {
    var holder = field.closest("details");
    if (holder) {
      field.addEventListener("input", function () { holder.open = true; });
    }
  }

  /* Add a project (PLAN-0027 phase 2). What Details lists follows the
     boxes: a skill's files and its command stand while its box is
     ticked, and the connection's while its box is. The server draws
     each line as the last post left the boxes and this keeps them in
     step from there; with no script they stay as drawn, and Refresh
     redraws them. A line names its boxes in `data-for`, by the key a
     box carries in `data-shows`. */
  var boxes = document.querySelectorAll("input[data-shows]");
  var followers = document.querySelectorAll("[data-for]");
  var followBoxes = function () {
    var on = {};
    for (var b = 0; b < boxes.length; b++) {
      if (boxes[b].checked) { on[boxes[b].getAttribute("data-shows")] = true; }
    }
    for (var d = 0; d < followers.length; d++) {
      var keys = (followers[d].getAttribute("data-for") || "").split(" ");
      var shown = false;
      for (var k = 0; k < keys.length; k++) { if (on[keys[k]]) { shown = true; } }
      followers[d].hidden = !shown;
    }
  };
  if (boxes.length) {
    for (var x = 0; x < boxes.length; x++) {
      boxes[x].addEventListener("change", followBoxes);
    }
    /* A reload can bring a box back ticked. */
    followBoxes();
  }

  /* "Add all" at the head of a column of the grid ticks that agent's
     boxes and nothing else, and writes nothing: the screen's one press
     posts what is ticked. It ships hidden, since only this can wire it. */
  var alls = document.querySelectorAll("button[data-all]");
  for (var a = 0; a < alls.length; a++) {
    (function (button) {
      button.hidden = false;
      button.addEventListener("click", function () {
        var agent = button.getAttribute("data-all");
        for (var b = 0; b < boxes.length; b++) {
          if (boxes[b].getAttribute("data-agent") === agent && !boxes[b].disabled) {
            boxes[b].checked = true;
          }
        }
        followBoxes();
      });
    })(alls[a]);
  }

  /* Refresh, in Details, redraws the screen for a changed name. Details
     and its Show commands box come back as they were left, once, so the
     reader sees what the press was for. */
  var settings = document.querySelector("details.settings");
  var refresh = document.querySelector("button.refresh");
  if (settings && refresh) {
    var commands = settings.querySelector(".show-commands");
    try {
      var kept = window.sessionStorage.getItem("radialrig.details");
      window.sessionStorage.removeItem("radialrig.details");
      if (kept) {
        settings.open = true;
        if (commands && kept === "commands") { commands.checked = true; }
      }
    } catch (err) { /* no storage: Details opens closed, as drawn */ }
    refresh.addEventListener("click", function () {
      try {
        window.sessionStorage.setItem(
          "radialrig.details", commands && commands.checked ? "commands" : "open");
      } catch (err) { /* as above */ }
    });
  }

  if (terminal || !token) { return; }

  /* A form submission leaves this page for the next state of the same
     walkthrough. Only a departure that is not ours means the user is
     done with the page. */
  var ours = false;
  var forms = document.querySelectorAll("form");
  for (var f = 0; f < forms.length; f++) {
    forms[f].addEventListener("submit", function () { ours = true; });
  }

  var sent = false;
  function close() {
    if (sent) { return; }
    sent = true;
    var body = "token=" + encodeURIComponent(token) + "&action=close";
    if (render) { body += "&render=" + encodeURIComponent(render); }
    var type = "application/x-www-form-urlencoded";
    /* sendBeacon reports only that the request was queued, so a false
       return is the one failure it can report. Take the fallback then;
       the server also stops on a terminal state and on Ctrl-C. */
    if (navigator.sendBeacon) {
      if (navigator.sendBeacon(endpoint, new Blob([body], { type: type }))) {
        return;
      }
    }
    try {
      var request = new XMLHttpRequest();
      request.open("POST", endpoint, false);
      request.setRequestHeader("Content-Type", type);
      request.send(body);
    } catch (err) { /* the server also stops on Ctrl-C */ }
  }

  window.addEventListener("pagehide", function (event) {
    if (!event.persisted && !ours) { close(); }
  });
})();
