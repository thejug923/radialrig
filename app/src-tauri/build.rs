// D12: the app's two commands are declared here so that Tauri generates
// an allow-permission for each. Without the declaration the commands are
// ungated and any document the window holds could invoke them; with it,
// the capability file's local scope is what makes the engine's own page
// able to invoke nothing.
fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["bootstrap_proceed", "bootstrap_retry"]),
        ),
    )
    .expect("the shell manifest did not build")
}
