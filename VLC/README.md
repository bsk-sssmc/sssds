# VLC Timestamp Capture Tool

A small Python utility that plays a video using **libVLC** (via the
[`python-vlc`](https://pypi.org/project/python-vlc/) bindings) and lets
you capture the current playback time whenever you press a key.

It does **not** spawn the `vlc` CLI as a subprocess and does **not**
scrape any UI. All timestamps come straight from `MediaPlayer.get_time()`.

## Features

- Loads and plays a given video file programmatically.
- Runs VLC with a minimal interface (no title overlay, no menus).
- Auto-starts playback and waits until the media is actually playing
  before reading timestamps.
- Two capture modes (selected via `ENTER_MODE` in the script):
  - **Single-key mode (default, `ENTER_MODE = False`):** press `t` to
    mark the current time, `p`/`SPACE` to pause/resume, `q` to quit.
  - **Enter mode (`ENTER_MODE = True`):** press `ENTER` to capture,
    type `p` or `q` followed by `ENTER` to pause or quit. Useful on
    terminals where raw-mode input doesn't work.
- Saves all captured timestamps to a text file on exit
  (default: `timestamps.txt`).
- Clean shutdown of libVLC threads on `q` or `Ctrl+C`.

## Requirements

1. **VLC media player must be installed on the host system** — the
   `python-vlc` package is just a binding; it loads the real `libvlc`
   shared library shipped with VLC.

   - macOS: install [VLC](https://www.videolan.org/vlc/) (the regular
     `.app` bundle is enough — `python-vlc` can locate it
     automatically).
   - Linux: `sudo apt install vlc` (or your distro's equivalent).
   - Windows: install VLC from the official site.

2. Python 3.9+.

> **macOS Apple Silicon note:** if Python is arm64 you must install
> the **arm64 (Apple Silicon) build** of VLC, otherwise `import vlc`
> will fail with
> `mach-o file, but is an incompatible architecture (have 'x86_64', need 'arm64')`.
> Check with `file $(which python3)` and download the matching VLC
> build from <https://www.videolan.org/vlc/download-macosx.html>.

## Install

```bash
python -m venv .venv
source .venv/bin/activate         # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

1. Open `vlc_timestamp_tool.py` and edit the **Configuration** block
   near the top of the file:

   ```python
   VIDEO_PATH = "/path/to/your/video.mp4"   # the video to play
   OUTPUT_FILE = "timestamps.txt"           # where captures are saved
   ENTER_MODE = False                       # True = press ENTER to capture
   ```

2. Run it:

   ```bash
   ./run.sh
   ```

   `run.sh` is a small wrapper that calls `python3 vlc_timestamp_tool.py`,
   automatically falling back to Rosetta (`arch -x86_64`) if it detects
   that your VLC.app is Intel-only on an Apple Silicon Mac. If you'd
   rather invoke Python directly:

   ```bash
   python3 vlc_timestamp_tool.py
   # or, on arm64 Mac with Intel VLC:
   arch -x86_64 /usr/bin/python3 vlc_timestamp_tool.py
   ```

### Controls

Single-key mode (default):

| Key       | Action                          |
|-----------|---------------------------------|
| `t`       | Capture current timestamp       |
| `p` / `␣` | Toggle play / pause             |
| `q`       | Save timestamps and quit        |
| `Ctrl+C`  | Same as `q`                     |

Enter mode (`ENTER_MODE = True`):

| Input              | Action                    |
|--------------------|---------------------------|
| `<ENTER>`          | Capture current timestamp |
| `p` then `<ENTER>` | Toggle play / pause       |
| `q` then `<ENTER>` | Save timestamps and quit  |

### Example session

```text
$ python vlc_timestamp_tool.py
Loading: /Users/me/Movies/sample.mp4

============================================================
Controls:
  t           capture current timestamp
  p / SPACE   toggle play/pause
  q           quit
Timestamps will be saved to: timestamps.txt
============================================================

[  1] 00:00:03.214   (3.214s)
[  2] 00:00:11.890   (11.890s)
-- paused --
-- playing --
[  3] 00:01:05.402   (65.402s)

Saved 3 timestamp(s) to timestamps.txt
```

`timestamps.txt`:

```
# index    hh:mm:ss.mmm    seconds
1    00:00:03.214    3.214
2    00:00:11.890    11.890
3    00:01:05.402    65.402
```

## Notes

- The video window itself is created by libVLC. We disable the title
  overlay (`--no-video-title-show`) and silence VLC's stderr chatter
  (`--quiet`). VLC's standard window has no menu bar / playback
  controls when launched this way, satisfying the "minimal interface"
  requirement. If you don't want a window at all, edit
  `build_player()` and add `"--vout=dummy"` to the `vlc.Instance(...)`
  args (audio will still play).
- `wait_until_playing()` polls until `is_playing()` is true and
  `get_time()` returns a non-negative value, which avoids the common
  pitfall of reading `0` immediately after `play()`.
