/* Fixtures: one View per named state, shaped exactly like the Rust
   `View` struct in app/src-tauri/src/bootstrap.rs (serde default naming,
   so snake_case field names, Option -> null). Nothing enforces the match
   yet; when View changes, change these to match.

   The gate says the same thing whatever the block set is — it names what
   the first run sets up and where it lands, not the steps — so the three
   gate fixtures differ in what they send, not in what the page draws.
   They are kept because what they send is what the real shell sends. */
window.PREVIEW_STATES = {
  gate: {
    phase: "gate",
    blocks: ["gate_uv", "gate_python", "gate_engine", "gate_path"],
    found_uv: null,
    bin_dir: "C:\\Users\\Ada\\.local\\bin",
    notice: null,
    progress: null,
    failure: null,
    retry: false
  },

  "gate-carried-uv": {
    phase: "gate",
    blocks: ["gate_python", "gate_engine", "gate_path"],
    found_uv: "C:\\Users\\Ada\\.local\\bin\\uv.exe",
    bin_dir: "C:\\Users\\Ada\\.local\\bin",
    notice: null,
    progress: null,
    failure: null,
    retry: false
  },

  "gate-override-notice": {
    phase: "gate",
    blocks: ["gate_uv", "gate_engine"],
    found_uv: null,
    bin_dir: "C:\\Users\\Ada\\.local\\bin",
    notice: { key: "fail_debug_override", detail: "file:///C:/work/radialrig-0.1.0-py3-none-any.whl" },
    progress: null,
    failure: null,
    retry: false
  },

  running: {
    phase: "running",
    blocks: [],
    found_uv: null,
    bin_dir: null,
    notice: null,
    progress: "progress_download",
    failure: null,
    retry: false
  },

  "failed-retry": {
    phase: "failed",
    blocks: [],
    found_uv: null,
    bin_dir: null,
    notice: null,
    progress: null,
    failure: { key: "fail_download", detail: "connection reset" },
    retry: true
  },

  "failed-final": {
    phase: "failed",
    blocks: [],
    found_uv: null,
    bin_dir: null,
    notice: null,
    progress: null,
    // Slot::bare, the way the shell sends it: this stop has nothing to
    // quote, and the sentence says what moves it instead.
    failure: { key: "fail_checksum", detail: "" },
    retry: false
  }
};
