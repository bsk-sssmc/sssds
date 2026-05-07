"""
VLC-based video player that captures playback timestamps on user input.

Uses libVLC via the python-vlc bindings (NO subprocess / CLI scraping).

On macOS we host the video in a Cocoa NSWindow (via PyObjC). This is
required because libvlc loaded from a regular Python process cannot
create its own NSWindow, and Tk's `winfo_id()` does not return a valid
NSView pointer (which segfaults libvlc on `play()`).

On Linux/Windows we fall back to a small Tk window which works fine
because `winfo_id()` returns the platform's native window handle.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import timedelta

# Silence Apple's Tk deprecation chatter on macOS (Tk is only used as a
# fallback on non-Darwin platforms now, but the import still triggers it).
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

try:
    import vlc
except OSError as exc:  # libvlc failed to load (most often: arch mismatch on macOS)
    msg = str(exc)
    sys.stderr.write(f"\nFailed to load libVLC:\n  {msg}\n\n")
    if "incompatible architecture" in msg and sys.platform == "darwin":
        sys.stderr.write(
            "This is the classic Apple Silicon vs Intel mismatch.\n"
            "Your Python and your installed VLC.app are different architectures.\n\n"
            "Pick one of the following fixes:\n"
            "  1. (recommended) Replace VLC.app with the Apple Silicon build from\n"
            "     https://www.videolan.org/vlc/download-macosx.html\n"
            "  2. Run this script via Rosetta so Python matches the Intel VLC:\n"
            "       arch -x86_64 /usr/bin/python3 vlc_timestamp_tool.py\n"
            "     or just use the included wrapper:\n"
            "       ./run.sh\n\n"
        )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration -- edit these values, then just run `python vlc_timestamp_tool.py`
# ---------------------------------------------------------------------------

# Configuration is environment-driven so the same script runs on every
# node with a different video. The defaults below are only used when
# the env vars aren't set (e.g. when running directly from a dev shell).
#
# On a provisioned node the systemd unit pulls these out of
# /etc/sssds/identity.conf via EnvironmentFile=.

VIDEO_PATH = os.environ.get(
    "SSSDS_VIDEO_PATH", "/Volumes/1431/VANDANAM FINAL 4k.mov"
)

OUTPUT_FILE = os.environ.get("SSSDS_OUTPUT_FILE", "timestamps.txt")

FULLSCREEN = os.environ.get("SSSDS_FULLSCREEN", "1").lower() not in ("0", "false", "no")

# Disable terminal stdin trigger when running headless (via systemd).
ENTER_CAPTURES = os.environ.get("SSSDS_ENTER_CAPTURES", "1").lower() not in ("0", "false", "no")

# Initial windowed size (used when FULLSCREEN is False).
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720

# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def format_timestamp(ms: int) -> str:
    """Convert milliseconds to ``HH:MM:SS.mmm``."""
    if ms < 0:
        ms = 0
    total_seconds = ms / 1000.0
    td = timedelta(seconds=total_seconds)
    whole = int(td.total_seconds())
    hours, rem = divmod(whole, 3600)
    minutes, seconds = divmod(rem, 60)
    millis = int(round((total_seconds - whole) * 1000))
    if millis == 1000:
        millis = 0
        seconds += 1
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def build_player() -> tuple[vlc.Instance, vlc.MediaPlayer]:
    # input-repeat=65535 = effectively infinite; libvlc has no real
    # "loop forever" flag. Combined with Restart=always on the systemd
    # unit, the video plays continuously even if libvlc somehow exits.
    instance = vlc.Instance(
        "--no-video-title-show",
        "--quiet",
        "--input-repeat=65535",
    )
    if instance is None:
        raise RuntimeError("Failed to create VLC instance. Is libVLC installed?")
    player = instance.media_player_new()
    return instance, player


def save_timestamps(path: str, items: list[tuple[int, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("# index\thh:mm:ss.mmm\tseconds\n")
        for i, (ms, ts) in enumerate(items, 1):
            f.write(f"{i}\t{ts}\t{ms / 1000:.3f}\n")


def print_banner(out_path: str, enter_captures: bool) -> None:
    print()
    print("=" * 60)
    print("Controls (focus the video window for these keys):")
    print("  t           capture current timestamp")
    print("  p / SPACE   toggle play/pause")
    print("  f           toggle fullscreen")
    print("  ESC         exit fullscreen (or quit if already windowed)")
    print("  q           save timestamps and quit")
    if enter_captures:
        print()
        print("Or, in this terminal:")
        print("  <ENTER>     capture current timestamp")
        print("  p<ENTER>    toggle play/pause")
        print("  f<ENTER>    toggle fullscreen")
        print("  q<ENTER>    quit")
    print(f"Timestamps will be saved to: {out_path}")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# Shared playback controller (used by both the macOS and Tk paths)
# ---------------------------------------------------------------------------

class Controller:
    """Holds the libvlc player + timestamp list, exposes thread-safe-ish ops.

    All mutating calls are intended to run on the GUI's main thread; the
    stdin listener marshals work back to the main thread via the GUI's
    "call later" mechanism.
    """

    def __init__(self, video_path: str, out_path: str) -> None:
        self.video_path = video_path
        self.out_path = out_path
        self.timestamps: list[tuple[int, str]] = []
        self._closed = False

        self.instance, self.player = build_player()
        media = self.instance.media_new(video_path)
        self.player.set_media(media)

    # -- playback ---------------------------------------------------------

    def play(self) -> bool:
        print(f"Loading: {self.video_path}")
        if self.player.play() == -1:
            print("Error: VLC failed to start playback.", file=sys.stderr)
            return False
        return True

    def capture(self) -> None:
        ms = self.player.get_time()
        if ms < 0:
            print("(playback hasn't started yet)")
            return
        ts = format_timestamp(ms)
        self.timestamps.append((ms, ts))
        print(f"[{len(self.timestamps):>3}] {ts}   ({ms / 1000:.3f}s)")

    def toggle_pause(self) -> None:
        self.player.pause()
        time.sleep(0.05)
        state = "playing" if self.player.is_playing() else "paused"
        print(f"-- {state} --")

    def is_ended(self) -> bool:
        return self.player.get_state() == vlc.State.Ended

    # -- shutdown ---------------------------------------------------------

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.timestamps:
                save_timestamps(self.out_path, self.timestamps)
                print(f"\nSaved {len(self.timestamps)} timestamp(s) to {self.out_path}")
            else:
                print("\nNo timestamps captured.")
        finally:
            try:
                self.player.stop()
                self.player.release()
                self.instance.release()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# macOS / Cocoa path
# ---------------------------------------------------------------------------

def run_macos(controller: Controller, fullscreen: bool, enter_captures: bool) -> int:
    import signal
    import objc  # noqa: F401  (loads PyObjC runtime)
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyRegular,
        NSBackingStoreBuffered,
        NSColor,
        NSEvent,
        NSEventMaskKeyDown,
        NSScreen,
        NSView,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
    )
    from Foundation import NSMakeRect
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    # Always create a titled window. Titled windows are real key windows
    # (canBecomeKeyWindow == YES) so our keyDown monitor actually fires.
    # When fullscreen=True we enter native macOS fullscreen *after* play()
    # via toggleFullScreen:, which keeps keyboard handling working.
    screen_frame = NSScreen.mainScreen().frame()
    x = (screen_frame.size.width - WINDOW_WIDTH) / 2
    y = (screen_frame.size.height - WINDOW_HEIGHT) / 2
    rect = NSMakeRect(x, y, WINDOW_WIDTH, WINDOW_HEIGHT)
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskResizable
    )

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, NSBackingStoreBuffered, False
    )
    window.setTitle_("VLC Timestamp Tool  —  q to quit")
    window.setBackgroundColor_(NSColor.blackColor())
    window.setReleasedWhenClosed_(False)

    # Content view that libvlc draws into.
    view = NSView.alloc().initWithFrame_(window.contentView().bounds())
    view.setWantsLayer_(True)
    window.setContentView_(view)

    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    # Hand the NSView to libvlc -- this is the part Tk gets wrong on macOS.
    view_ptr = objc.pyobjc_id(view)
    controller.player.set_nsobject(view_ptr)

    if not controller.play():
        return 2

    # Track fullscreen state ourselves; macOS native fullscreen toggling
    # is asynchronous so we mirror it via a flag plus the window's
    # styleMask which gets the FullScreen bit set automatically.
    fs_state = {"on": False}

    def toggle_fullscreen() -> None:
        window.toggleFullScreen_(None)
        fs_state["on"] = not fs_state["on"]
        print(f"-- fullscreen {'on' if fs_state['on'] else 'off'} --")

    if fullscreen:
        # Defer until the window is fully on screen and libvlc has begun
        # producing frames; toggling fullscreen too early can confuse the
        # video output module on macOS.
        def _enter_fs() -> None:
            window.toggleFullScreen_(None)
            fs_state["on"] = True
        AppHelper.callLater(0.4, _enter_fs)

    def quit_app() -> None:
        controller.shutdown()
        try:
            AppHelper.stopEventLoop()
        except Exception:
            pass

    # Install a SIGINT handler so Ctrl+C in the terminal *always* exits.
    def _sigint(_sig, _frame) -> None:
        # Marshal back to the main thread; AppHelper.callAfter is safe.
        AppHelper.callAfter(quit_app)

    signal.signal(signal.SIGINT, _sigint)

    def on_key(event):
        chars = event.charactersIgnoringModifiers()
        key_code = event.keyCode()
        if not chars:
            return event
        ch = str(chars).lower()
        if ch == "t":
            controller.capture()
            return None
        if ch == "p" or ch == " ":
            controller.toggle_pause()
            return None
        if ch == "f":
            toggle_fullscreen()
            return None
        if ch == "q":
            quit_app()
            return None
        if key_code == 53:  # ESC
            if fs_state["on"]:
                toggle_fullscreen()
            else:
                quit_app()
            return None
        return event

    NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
        NSEventMaskKeyDown, on_key
    )

    # Periodic poll for end-of-media so the program exits when the file
    # finishes. Uses a Cocoa timer (main-thread safe).
    def poll_eom() -> None:
        if controller._closed:
            return
        if controller.is_ended():
            print("-- end of media --")
            quit_app()
            return
        AppHelper.callLater(0.5, poll_eom)

    AppHelper.callLater(0.5, poll_eom)

    if enter_captures:
        def stdin_listener() -> None:
            try:
                for line in sys.stdin:
                    if controller._closed:
                        break
                    cmd = line.strip().lower()
                    if cmd in ("q", "quit", "exit"):
                        AppHelper.callAfter(quit_app)
                        break
                    elif cmd == "p":
                        AppHelper.callAfter(controller.toggle_pause)
                    elif cmd == "f":
                        AppHelper.callAfter(toggle_fullscreen)
                    else:
                        AppHelper.callAfter(controller.capture)
            except (OSError, ValueError):
                pass

        threading.Thread(target=stdin_listener, daemon=True).start()

    try:
        AppHelper.runEventLoop(installInterrupt=True)
    except KeyboardInterrupt:
        quit_app()
    return 0


# ---------------------------------------------------------------------------
# Linux / Windows path: Tk
# ---------------------------------------------------------------------------

def run_tk(controller: Controller, fullscreen: bool, enter_captures: bool) -> int:
    import tkinter as tk

    root = tk.Tk()
    root.title("VLC Timestamp Tool")
    root.configure(bg="black")
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")

    video_frame = tk.Frame(root, bg="black")
    video_frame.pack(fill=tk.BOTH, expand=True)
    root.update_idletasks()

    handle = video_frame.winfo_id()
    if sys.platform.startswith("win"):
        controller.player.set_hwnd(handle)
    else:  # linux / X11
        controller.player.set_xwindow(handle)

    if fullscreen:
        # Belt-and-braces kiosk-mode fullscreen:
        #   overrideredirect: WM stops managing this window entirely (no
        #     decorations, doesn't get stacked under panels)
        #   explicit screen geometry: covers every pixel
        #   -topmost: even if the WM ignores overrideredirect, stay on
        #     top of any LXQt panel
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        root.overrideredirect(True)
        root.geometry(f"{screen_w}x{screen_h}+0+0")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.update_idletasks()
        root.lift()
        root.focus_force()
    video_frame.configure(cursor="none")

    def quit_app() -> None:
        controller.shutdown()
        try:
            root.destroy()
        except Exception:
            pass

    def toggle_fullscreen(_event=None) -> None:
        is_fs = bool(root.attributes("-fullscreen"))
        root.attributes("-fullscreen", not is_fs)
        print(f"-- fullscreen {'off' if is_fs else 'on'} --")

    def esc(_event=None) -> None:
        if bool(root.attributes("-fullscreen")):
            root.attributes("-fullscreen", False)
            print("-- fullscreen off --")
        else:
            quit_app()

    root.bind("<KeyPress-t>", lambda e: controller.capture())
    root.bind("<KeyPress-T>", lambda e: controller.capture())
    root.bind("<KeyPress-p>", lambda e: controller.toggle_pause())
    root.bind("<KeyPress-P>", lambda e: controller.toggle_pause())
    root.bind("<space>", lambda e: controller.toggle_pause())
    root.bind("<KeyPress-f>", toggle_fullscreen)
    root.bind("<KeyPress-F>", toggle_fullscreen)
    root.bind("<KeyPress-q>", lambda e: quit_app())
    root.bind("<KeyPress-Q>", lambda e: quit_app())
    root.bind("<Escape>", esc)
    root.protocol("WM_DELETE_WINDOW", quit_app)

    if not controller.play():
        return 2

    def poll_eom() -> None:
        if controller._closed:
            return
        if controller.is_ended():
            print("-- end of media --")
            quit_app()
            return
        root.after(500, poll_eom)

    root.after(500, poll_eom)

    if enter_captures:
        def stdin_listener() -> None:
            try:
                for line in sys.stdin:
                    if controller._closed:
                        break
                    cmd = line.strip().lower()
                    if cmd in ("q", "quit", "exit"):
                        root.after(0, quit_app)
                        break
                    elif cmd == "p":
                        root.after(0, controller.toggle_pause)
                    elif cmd == "f":
                        root.after(0, toggle_fullscreen)
                    else:
                        root.after(0, controller.capture)
            except (OSError, ValueError):
                pass

        threading.Thread(target=stdin_listener, daemon=True).start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        quit_app()
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(
    video_path: str,
    out_path: str,
    fullscreen: bool,
    enter_captures: bool,
) -> int:
    if not os.path.isfile(video_path):
        print(f"Error: file not found: {video_path}", file=sys.stderr)
        return 1

    controller = Controller(video_path, out_path)
    print_banner(out_path, enter_captures)

    if sys.platform == "darwin":
        return run_macos(controller, fullscreen, enter_captures)
    return run_tk(controller, fullscreen, enter_captures)


if __name__ == "__main__":
    sys.exit(main(VIDEO_PATH, OUTPUT_FILE, FULLSCREEN, ENTER_CAPTURES))
