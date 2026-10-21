; The two uninstaller hooks — PLAN-0008 phase 5, prescription 4 (U4, U5).
;
; The bundler includes this file at the top of the generated installer.nsi
; and inserts each macro where the template names it. Nothing here is
; inserted anywhere else: there is no install hook, because an install
; that needs a hook is an install the template does not already do.
;
; Both bodies sit inside the same guard the template uses for shortcuts,
; the autostart value and the app data — $UpdateMode <> 1. An upgrade
; keeps the Path entry, both record files and the webview profile, and
; shows nothing. A true uninstall takes them out and says so once.
;
; Nothing here speaks. Every sentence the removal shows comes from
; app/src/strings.json, said by the app's own binary, so the uninstaller
; carries no words of its own for a sweep to miss.

; Before anything is deleted, so the exe is still there to run and the
; record it reads is still on disk. The exit code is deliberately not
; read: a refusal must never stop the app's own uninstall.
;
; The running-app check comes first, ahead of our own run. The template
; puts its copy after this hook, which would let a reader cancel at that
; box with the Path entry and the record already gone — a canceled
; uninstall that stranded the machine half removed. The macro undefines
; its own per-line ID at the end of its body, so the template's later
; insertion of the same macro still compiles.
!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode <> 1
    !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
    ${If} ${Silent}
    ${OrIf} $PassiveMode = 1
      ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --uninstall-path-entry --quiet'
    ${Else}
      ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --uninstall-path-entry'
    ${EndIf}
  ${EndIf}
!macroend

; After the files, the shortcuts and the uninstall key are gone. The two
; directories are the app's own: the first holds path-entry.json and
; install-intent.json, the second holds the webview profile Tauri forces
; under the identifier. Both were written by this app and nothing else is
; touched — uv, the managed Python, the engine, the uv cache, the
; projects and the client entries all outlive it.
;
; The two directories are named by the template's own BUNDLEID, which the
; bundler fills from the config's identifier, the one a --config overlay
; changes. A build made under another identifier therefore clears its own
; two directories and never this app's (PLAN-0021 phase 2, prescription
; 1). The template deletes the same two itself when its app-data box is
; ticked; this hook deletes them on every true uninstall.
;
; The two keys are the template's own defines rather than the spelled
; keys they expand to, so the publisher and the product name are read
; from the bundle config at compile time and a rename cannot leave this
; file deleting a key nothing writes.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    RMDir /r "$APPDATA\${BUNDLEID}"
    RMDir /r "$LOCALAPPDATA\${BUNDLEID}"
    DeleteRegKey HKCU "${MANUPRODUCTKEY}"
    DeleteRegKey /ifempty HKCU "${MANUKEY}"
  ${EndIf}
!macroend
