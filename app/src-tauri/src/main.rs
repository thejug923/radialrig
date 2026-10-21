// The desktop shell — PLAN-0008 phase 5.
//
// A thin builder and nothing else. Constraint 2: the engine is never
// bundled here. This process installs the engine with uv, starts it, and
// shows the pages the engine itself serves; the one screen it draws on
// its own is the bootstrap screen, which runs before there is an engine
// to draw anything (R5).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod attach;
mod bootstrap;
mod config;
mod holders;
mod picker;
mod removal;
mod strings;
mod update;

use tauri::webview::{PageLoadEvent, WebviewWindowBuilder};
use tauri::{Manager, WindowEvent};

/// The stop when the window this shell is configured to draw is not in
/// the config it was compiled against.
const NO_WINDOW_CONFIGURED: &str = "the window is not configured";

/// The environment variable WebView2 reads for extra browser arguments.
/// An ambient value here opens a DevTools port on this window, and a
/// port holder can call the gated commands without a human press.
const WEBVIEW2_ARGS: &str = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS";

/// Remove `name` from this process's environment, answering whether it
/// was set. Separated from main so a test can hold the behavior.
#[cfg_attr(debug_assertions, allow(dead_code))]
fn clear_env(name: &str) -> bool {
    let was_set = std::env::var_os(name).is_some();
    std::env::remove_var(name);
    was_set
}

fn main() {
    // The config this build was compiled with, `--config` overlay and
    // all. It is a value rather than a running app, so the flag run below
    // can read its identifier before any app exists, and the builder at
    // the end runs on this same one.
    let context = tauri::generate_context!();

    // The uninstaller's one call into this binary (U5). It is answered
    // before a builder, a window or an engine exists: the flag run reads
    // the Path record, decides, says one sentence and ends. The record is
    // looked for under this build's own identifier, never a spelled-out
    // one (PLAN-0021 phase 2, prescription 1).
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == removal::FLAG) {
        std::process::exit(removal::run(
            &context.config().identifier,
            args.iter().any(|arg| arg == removal::QUIET),
        ));
    }

    // Both compiled-in files are read before anything is drawn, so a
    // shell that cannot say its own sentences or trust its own pins stops
    // here rather than in front of a reader.
    let _ = config::pins();
    let _ = strings::words();

    // The consent gate's word must stay true in the shipped app, so the
    // release build clears the DevTools door before the webview exists.
    // The debug build keeps it: that port is the design loop's
    // verification channel. The registry policy route stays open by
    // ruling; PLAN-0008 records it.
    //
    // debug_assertions stands in for the release profile here, as it
    // does on the windows_subsystem line at the top of this file. The
    // two cfg sites move together; a custom profile that re-enables
    // debug assertions in a release build would compile this clear out
    // and show a console window, the visible sign of that state.
    #[cfg(not(debug_assertions))]
    clear_env(WEBVIEW2_ARGS);

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        // The updater, for the app's own Rust to call (PLAN-0021 phase 3,
        // prescription 2). Its commands reach no page: the window's
        // capability grants none of them (Invariant 6).
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(bootstrap::Shell::new())
        .invoke_handler(tauri::generate_handler![
            bootstrap::bootstrap_proceed,
            bootstrap::bootstrap_retry
        ])
        .setup(|app| {
            // The window is built here rather than at startup so that the
            // document's own name can be watched (D11's one channel back
            // from an evaluated script). Everything about the window —
            // its name included — still comes from the config, which is
            // what keeps D14's one name true and needs no retitling.
            let configured = app
                .config()
                .app
                .windows
                .first()
                .cloned()
                .expect(NO_WINDOW_CONFIGURED);
            // The guard on every address this window is asked to go to.
            // Two things it refuses: the launch address once its one
            // navigation has been spent — a history Back reaches for it
            // first, and the engine answers a spent token with nothing a
            // reader wants to see — and any origin that is neither this
            // shell's own screen nor the engine this run started. A
            // refused navigation is a navigation that does not happen,
            // which on screen is nothing at all.
            let guarding = app.handle().clone();
            let window = WebviewWindowBuilder::from_config(app.handle(), &configured)?
                .on_navigation(move |url| {
                    let shell = guarding.state::<bootstrap::Shell>();
                    let home = shell.home();
                    shell.engine.may_navigate(url.as_str(), home.as_deref())
                })
                .on_document_title_changed(|window, name| picker::renamed(&window, &name))
                .on_page_load(|window, payload| {
                    if payload.event() != PageLoadEvent::Finished {
                        return;
                    }
                    // The first document this window loads is the local
                    // screen, so its address is the one D7's return
                    // navigation goes back to. Every arrival on it — the
                    // launch, a retry, and that return — redraws from the
                    // one state the shell keeps.
                    let app = window.app_handle().clone();
                    let here = payload.url().to_string();
                    let shell = app.state::<bootstrap::Shell>();
                    shell.remember_home(here.clone());
                    if shell.home().as_deref() == Some(here.as_str()) {
                        bootstrap::redraw(&app);
                    }
                    // The engine's Browse button ships hidden, because
                    // only this shell can open a folder. A page that has
                    // loaded inside this window is shown the button; a
                    // page without one is left exactly as it was.
                    let _ = window.eval(picker::reveal_script());
                })
                .build()?;

            window.set_menu(picker::menu(app.handle())?)?;
            window.on_menu_event(picker::pressed);

            let closing = app.handle().clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    let shell = closing.state::<bootstrap::Shell>();
                    if !shell.engine.is_live() {
                        return;
                    }
                    // D9: the engine is asked to stop and given a bounded
                    // five seconds to finish what it was doing, so an
                    // in-flight write completes before the process ends.
                    api.prevent_close();
                    // The claim is made here, in the handler itself, and
                    // it is one exchange: whichever close request wins it
                    // is the one that waits, and a second press while
                    // that wait runs changes nothing.
                    if !shell.engine.claim_closing() {
                        return;
                    }
                    let app = closing.clone();
                    std::thread::spawn(move || {
                        let url = app
                            .get_webview_window(bootstrap::MAIN_WINDOW)
                            .and_then(|window| window.url().ok())
                            .map(|url| url.to_string())
                            .unwrap_or_default();
                        let shell = app.state::<bootstrap::Shell>();
                        shell.engine.quiesce(&url);
                        // The run's own job, closed by name before the
                        // exit: `app.exit` runs no destructor, and on
                        // Linux this close is the one thing that ends
                        // the engine's process group.
                        shell.engine.end_job();
                        app.exit(0);
                    });
                }
            });

            // Discovery runs off the drawing thread: it asks a uv on this
            // machine for its version, and a probe is not instant.
            let opening = app.handle().clone();
            std::thread::spawn(move || bootstrap::open(&opening));
            Ok(())
        })
        .run(context)
        .expect("the window did not run");
}

#[cfg(test)]
mod tests {
    use super::{clear_env, WEBVIEW2_ARGS};

    // The name is load-bearing: it is the one variable WebView2 reads
    // for extra browser arguments, and the release build clears it.
    #[test]
    fn the_cleared_variable_is_the_webview2_one() {
        assert_eq!(WEBVIEW2_ARGS, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS");
    }

    // The call site in main() runs in no test profile: the cfg compiles
    // it out of the very build the tests run in, and no test calls
    // main(). This pin reads the source instead, so deleting the call,
    // inverting the cfg, or moving it below the builder fails a test
    // that runs in every profile.
    #[test]
    fn the_release_build_clears_the_door_before_the_builder() {
        // A checkout may carry CRLF endings; the pin reads one form.
        let source = include_str!("main.rs").replace("\r\n", "\n");
        let call = "#[cfg(not(debug_assertions))]\n    clear_env(WEBVIEW2_ARGS);";
        let at = source.find(call).expect("the gated clear_env call is missing");
        let builder = source
            .find("tauri::Builder::default()")
            .expect("the builder line is missing");
        assert!(at < builder, "the clear must come before the builder");
    }

    // PLAN-0021 phase 2, prescription 1. The removal run has no app
    // handle, so it is handed the identifier out of the context this
    // build compiled, which is the one a `--config` overlay changes, and
    // that same context is the one the builder runs on. A literal, or a
    // second context, could name a different app's folders than the ones
    // `app_data_dir()` resolves. Read from the source for the reason the
    // pin above is: no test runs main(). The code is read with its
    // whitespace taken out, so a reflow of a call does not move the pin.
    #[test]
    fn the_removal_run_is_handed_the_identifier_this_build_compiled() {
        let source = include_str!("main.rs");
        let code = &source[..source.find("#[cfg(test)]").expect("the test module")];
        let code: String = code.split_whitespace().collect();
        let made = code
            .find("letcontext=tauri::generate_context!();")
            .expect("the context is made before the flag is answered");
        let handed = code
            .find("removal::run(&context.config().identifier,")
            .expect("the removal run is given the compiled identifier");
        let builder = code
            .find("tauri::Builder::default()")
            .expect("the builder line is missing");
        assert!(made < handed && handed < builder);
        assert_eq!(code.matches("generate_context!").count(), 1, "one context");
        assert!(code.contains(".run(context)"), "the builder runs that context");
    }

    #[test]
    fn clear_env_removes_the_variable_and_says_so() {
        // A scratch name, so parallel tests never race on the real one.
        let name = "RADIALRIG_TEST_DEBUG_DOOR";
        std::env::set_var(name, "--remote-debugging-port=9333");
        assert!(clear_env(name));
        assert!(std::env::var_os(name).is_none());
        assert!(!clear_env(name));
    }
}
