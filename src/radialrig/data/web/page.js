/* The setup page's only script — PLAN-0005 Phase 4.

   The page works without it: every control is a form the server answers.
   This adds three conveniences and nothing that can change the outcome.

   1. Copy buttons for the next-step commands. The button is removed when
      the browser has no clipboard API, so a dead control never shows.
   2. A close beacon. Leaving the page tells the local server to stop, so
      a walkthrough abandoned before the mount does not leave it running.
      The server also stops on a terminal state and on Ctrl-C.
   3. "Tick all" beside each group of skill boxes, which ticks that
      group's boxes and nothing else; the form posts what is ticked.

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

  /* "Tick all" beside a group of skill boxes (PLAN-0015 phase 3). The
     button ticks every box in its own group that can be ticked and
     nothing in another group; the page posts the boxes exactly as it
     would had they been ticked one by one, which is what a page with no
     script still does. A button standing outside any group has nothing
     to tick and is removed, the way a Copy button with no clipboard is. */
  var ticks = document.querySelectorAll("button.tick");
  for (var t = 0; t < ticks.length; t++) {
    (function (button) {
      var group = button.closest("fieldset");
      if (!group) {
        button.remove();
        return;
      }
      button.addEventListener("click", function () {
        var boxes = group.querySelectorAll("input[type=checkbox]:not(:disabled)");
        for (var b = 0; b < boxes.length; b++) { boxes[b].checked = true; }
      });
    })(ticks[t]);
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
