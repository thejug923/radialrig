//! D13 `picker.rs`: the native folder picker behind one menu item.
//!
//! The picked path is serialised as a JSON literal and written into the
//! folder field the engine's page already renders, with an `input` event
//! after it, so the picked path reaches exactly the field a typed path
//! reaches. The picker adds convenience and never behavior.
//!
//! When that field is not on screen the script says so through the one
//! channel a host script has back to the shell — the document's own name,
//! set to a marker and put straight back — and the shell shows the stop
//! in its own chrome with the picked path to copy.

use std::sync::Mutex;

use crate::strings;
use tauri::menu::{Menu, MenuEvent, MenuItem};
use tauri::{AppHandle, Manager, WebviewWindow, Window};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

/// The one menu item, by the id its press arrives under.
pub const MENU_ITEM: &str = "pick-folder";

/// The marker the script puts in the document's name when the field it
/// was written for is not on this view. It is put back the moment it is
/// read, so nothing a reader sees carries it.
pub const NO_TARGET: &str = "radialrig-app.no-target";

/// The marker the engine's page puts in the document's name when its
/// Browse button is pressed. The same channel as `NO_TARGET`, spoken in
/// the other direction: the page asks, this shell opens the picker. The
/// engine's `page.js` writes this literal; the engine's own test pins it
/// as a machine contract.
pub const PICK_REQUEST: &str = "radialrig-app.pick-folder";

/// The Browse button's id on the engine's manage page. The button ships
/// hidden, because only this shell can answer it; `reveal_script` shows
/// it once a page has loaded inside this window.
const BROWSE: &str = "browse";

/// The field the engine's manage page renders for a project folder. The
/// engine's own test pins this id as a machine contract.
const FIELD: &str = "root";

/// The last path the picker answered with, kept only until the stop that
/// needs it has been said.
static PICKED: Mutex<Option<String>> = Mutex::new(None);

/// The script that writes a picked path into the field, or says there is
/// no field to write it into.
///
/// The path is serialised with `serde_json::to_string` and the literal it
/// produces is embedded as the JavaScript string, which is what makes an
/// apostrophe, a run of backslashes, and a non-ASCII name all arrive
/// exactly as they were picked.
pub fn eval_script(path: &str) -> String {
    let picked = literal(path);
    let marker = literal(NO_TARGET);
    let field = literal(FIELD);
    format!(
        "(function(){{var was=document.title;\
var target=document.getElementById({field});\
if(target){{target.value={picked};\
target.dispatchEvent(new Event(\"input\",{{bubbles:true}}));}}\
else{{document.title={marker};\
setTimeout(function(){{document.title=was;}},0);}}}})()"
    )
}

fn literal(said: &str) -> String {
    serde_json::to_string(said).unwrap_or_else(|_| String::from("\"\""))
}

/// The script that shows the Browse button, run after a page loads in
/// this window. A page without the button — the shell's own screen, or
/// an engine state with no folder row — is left exactly as it was.
pub fn reveal_script() -> String {
    let browse = literal(BROWSE);
    format!(
        "(function(){{var b=document.getElementById({browse});\
if(b){{b.hidden=false;}}}})()"
    )
}

/// The menu, with the one item D11 allows, labelled from the strings file.
pub fn menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let label = strings::words().picker_menu_label.as_str();
    let item = MenuItem::with_id(app, MENU_ITEM, label, true, None::<&str>)?;
    Menu::with_items(app, &[&item])
}

/// The native picker, in its callback form, so nothing blocks the
/// thread the window is drawn on. Both roads in end here: the menu
/// item's press, and the page's Browse button asking through the
/// document's name.
fn open_picker(app: AppHandle) {
    app.clone().dialog().file().pick_folder(move |picked| {
        let path = match picked {
            Some(path) => path.to_string(),
            None => return,
        };
        if let Ok(mut held) = PICKED.lock() {
            *held = Some(path.clone());
        }
        if let Some(view) = app.get_webview_window(crate::bootstrap::MAIN_WINDOW) {
            let _ = view.eval(eval_script(&path));
        }
    });
}

/// One press of the menu item.
pub fn pressed(window: &Window, event: MenuEvent) {
    if event.id() != MENU_ITEM {
        return;
    }
    open_picker(window.app_handle().clone());
}

/// The document's name changed. Only two values mean anything here: the
/// page asking for the picker, and the picked path finding no field.
pub fn renamed(window: &WebviewWindow, name: &str) {
    if name == PICK_REQUEST {
        open_picker(window.app_handle().clone());
        return;
    }
    if name != NO_TARGET {
        return;
    }
    let path = PICKED
        .lock()
        .ok()
        .and_then(|mut held| held.take())
        .unwrap_or_default();
    let words = strings::words();
    let said = strings::fill(&words.fail_picker_no_target, &path);
    window
        .app_handle()
        .dialog()
        .message(said)
        .title(words.window_title.clone())
        .kind(MessageDialogKind::Warning)
        .show(|_| {});
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The path back out of the script, read the way the page reads it.
    fn embedded(script: &str) -> String {
        let opened = script.find("target.value=").expect("the write") + "target.value=".len();
        let rest = &script[opened..];
        let closed = rest.find(";target.dispatchEvent").expect("the event");
        serde_json::from_str::<String>(&rest[..closed]).expect("a JSON string")
    }

    #[test]
    fn a_picked_path_reaches_the_field_exactly_as_it_was_picked() {
        let awkward = [
            "C:\\Users\\a\\it's a folder",
            "C:\\Users\\a\\\\double\\back",
            "C:\\Users\\a\\naïve\\日本語",
            "C:\\Users\\a\\a \"quoted\" folder",
            "C:\\Users\\a\\line\nbreak",
            "C:\\Users\\a\\tab\there",
            "",
        ];
        for path in awkward {
            let script = eval_script(path);
            assert_eq!(embedded(&script), path, "{path}");
        }
    }

    #[test]
    fn the_script_writes_the_field_and_says_so_when_there_is_none() {
        let script = eval_script("C:\\a");
        assert!(script.contains("getElementById(\"root\")"));
        assert!(script.contains("new Event(\"input\",{bubbles:true})"));
        assert!(script.contains("document.title=\"radialrig-app.no-target\""));
        // The name goes back the moment it has been read, so nothing a
        // reader sees ever carries the marker.
        assert!(script.contains("document.title=was;"));
        // One statement, evaluated where it lands, leaving no name behind.
        assert!(script.starts_with("(function(){"));
        assert!(script.ends_with("})()"));
    }

    #[test]
    fn the_reveal_script_shows_the_button_and_only_the_button() {
        let script = reveal_script();
        assert!(script.contains("getElementById(\"browse\")"));
        assert!(script.contains("b.hidden=false;"));
        // One statement, evaluated where it lands, leaving no name behind.
        assert!(script.starts_with("(function(){"));
        assert!(script.ends_with("})()"));
    }

    #[test]
    fn the_two_markers_share_a_prefix_and_never_collide() {
        // The engine's page.js writes PICK_REQUEST as a literal; this pin
        // is the Rust half of that machine contract.
        assert_eq!(PICK_REQUEST, "radialrig-app.pick-folder");
        assert_eq!(NO_TARGET, "radialrig-app.no-target");
        assert_ne!(PICK_REQUEST, NO_TARGET);
    }

    #[test]
    fn a_backslash_run_never_becomes_an_escape() {
        // The one failure the JSON literal exists to prevent: a path
        // ending in a backslash closing the string it was written into.
        let script = eval_script("C:\\a\\");
        assert_eq!(embedded(&script), "C:\\a\\");
        assert!(script.contains("target.value=\"C:\\\\a\\\\\";"));
    }
}
