RadialRig {version}, a Radial 47 product.

RadialRig mounts governed records onto a project you already have, and
its desktop app installs the engine on first run. This release carries
the Windows installer, the two Linux bundles, and the engine wheel
that first run installs from here.

**Which Linux versions.** Ubuntu 22.04 or newer and Debian 12 or
newer. Older releases are not supported. The deb needs WebKitGTK 4.1,
which those two are the first of their lines to carry in their
standard repositories, and a stock desktop does not have it
installed: put the deb in through your package manager, not by
opening the file alone, so that library is pulled in beside it. The
AppImage carries its own copy of WebKitGTK 4.1 and also runs on other
current distributions. The deb installs only on Debian and Ubuntu.
Anthropic's Claude Desktop for Linux names the same two versions, so
the client this product registers ships where the app does.

On Windows you may still meet two warnings before this product runs.
Both are expected. Read this before you download.

**The download.** In Edge the file arrives with a caution and no Keep
button in sight. Open the download item's "..." menu and choose Keep. A
second screen opens; the Keep anyway control sits under the drop-down
arrow beside Delete. Three screens from the download to the kept file.
Other browsers put those controls elsewhere, and one may not caution.

**The first run.** SmartScreen may open with one button, "Don't run".
The Run anyway button does not exist until you click "More info". Click
More info, then Run anyway. The dialog names the publisher Windows
checked, and on this file that name is Patrick Williams. Neither click
needs admin rights, and nothing here asks for them.

**Why the warnings.** This installer is signed by Patrick Williams,
through Microsoft's Artifact Signing service. Windows shows that name
where it asks you to decide. SmartScreen may still warn about a file it
has not seen before, and a new release starts over with no reputation,
so expect a warning on every version, not only this one. What the
signature changes is what the warning says: the publisher line reads
Patrick Williams rather than Unknown publisher.

**Smart App Control.** A machine with Smart App Control set to on
refuses a file it does not already trust, and offers no Run-anyway.
This release did not test that mode against this signed installer, so
it says nothing about what happens there. Check Windows Security, App
and browser control, Smart App Control before you download: if it reads
on and the install stops, that is why.

**What lands on Windows.** The app installs for the current user only, under
%LOCALAPPDATA%\RadialRig, with an uninstall entry named RadialRig. Its
first run names every other item it would install, where each lands,
and how to take it out, then waits for your press.

**Removing it.** Two removals, and each takes out different things.
Uninstalling RadialRig from Settings, Apps, Installed apps takes out
the app and the files it wrote, and nothing more: the engine, uv, the
Python uv installed for the engine, your projects and your entries in
your client's settings all stay. The manage page's removal section
takes those out. Open RadialRig, go to that page, and work down it: one
press reverses each listed project, the registration in your client's
settings first, then each installed skill, then the mount, and the
section below names the rest in the order it gives, the engine first,
then the Python uv installed for it, then uv last, so every command
still has a uv to run it. Do that page before you uninstall the app,
because the page opens from the app. When both removals have run, what
is left is what that page lists as left: every path a reversal named as
left, the projects listing file, and the log files beside it.
Uninstalling the app also takes out
the one entry it put on your user Path. That entry stays only where your
Path value no longer matches what the app recorded, and the uninstall
names the entry when that happens.

**On Linux.** The deb installs the app system-wide through your
package manager and needs your password for that one step; nothing
the app itself does afterward asks for root. The AppImage is the
whole app in one file: put it where you want it, mark it executable,
and run it. Either way, the app's first run names every other item it
would install, where each lands, and how to take it out, then waits
for your press. Removing the deb takes out what the package put in,
names what stays, and touches nothing else; removing the AppImage is
deleting the file. Before either removal, work down the manage page's
removal section in the order the Windows paragraph above gives, and
for the same reason: the page opens from the app.

**The files below.** Download RadialRig-Setup.exe and run it.
RadialRig_{version}_x64-setup.exe is the same installer under its
version-stamped name. On Ubuntu or Debian, download
RadialRig_{version}_amd64.deb and install it through your package
manager. RadialRig_{version}_amd64.AppImage is the same app as one
file, for those two and for the other distributions the Linux
paragraph names. radialrig-{version}-py3-none-any.whl is the engine;
the app's first run installs it from here, so you do not need to
download it yourself. The app reads latest.json from the newest release
each time it opens, on Windows and as the AppImage, and asks before it
installs a newer version; a deb install takes a new release by
installing that release's deb.
