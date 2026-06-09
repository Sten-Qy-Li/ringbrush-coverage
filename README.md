# ringbrush-coverage

Submitted to the Institute of Computer Science in fulfillment of the requirements for the courses:
 * Pervasive Data Science Seminar (3 ECTS, LTAT.06.010); and
 * Distributed Systems Project (3 ECTS, LTAT.00.010)

 at the University of Tartu, in the spring of 2026.

https://github.com/user-attachments/assets/13e0ac8a-ce0f-4071-a721-43946dbc97ab

---

Turn a smart-ring sensor log into an MP4 that shows where someone brushed their teeth. The video renders a stylized mouth, a dead-reckoned brush cursor with a short motion trail, and per-zone coverage bars that fill as each surface is brushed.

> **For developers and engineers:** if you want to extend or modify the project, see the [Developer's README](DEVELOPERS.md) for a feature-by-feature map of where each piece of functionality lives and where to edit it.

## Install

From the repository root:

```powershell
python -m pip install -e .
```

This installs the `ringbrush-coverage` CLI and its `imageio-ffmpeg` dependency.

## How to reproduce

All session and calibration logs used in the report are checked in under [recordings/](recordings/). After installing, reproduce the primary heuristic render with:

```powershell
ringbrush-coverage "recordings/2026-05-29_2203_full-session-with-video-recording.txt" `
  --calibration-dir recordings `
  --output outputs/reproduction.mp4
```

For the video-anchored render — the one in the final report — the synchronized MediaPipe CSV is already checked in, so step 3 of "Video-anchored dead reckoning" runs straight off the repo:

```powershell
ringbrush-coverage "recordings/2026-05-29_2203_full-session-with-video-recording.txt" `
  --calibration-dir recordings `
  --dr-method video-anchored `
  --video-sync-csv outputs/2026-05-29_video-sync/synchronized_video_on_imu_time.csv `
  --output outputs/reproduction-video-anchored.mp4
```

The raw front-camera MP4 (~157 MB) is **not** committed — it exceeds GitHub's per-file limit. Only the derived `recordings/*_format-3.csv` (MediaPipe hand landmarks) is checked in, which is what the pipeline actually consumes. Re-deriving that CSV with `tools/extract_video_motion.py` requires the raw video, available on request.

## Record a sensor log from the smart ring

This section explains how to make your own `.txt` sensor log using the smart ring prototype. You do not need any prior experience with Arduino IDE. Follow the steps in order.

**What you need before you start:**

- A laptop (Windows, macOS, or Linux).
- An [M5StickC Plus](https://docs.m5stack.com/en/core/m5stickc_plus) controller with a USB-C cable.
- A smart ring prototype with a BNO055 sensor (this is the small ring you wear on your finger).
- A mobile phone with a hotspot you can turn on, **or** any Wi-Fi network you control the password for. The M5StickC Plus needs Wi-Fi to start. If it cannot connect, it will keep restarting and will not produce any data.

### 1. Wire the smart ring to the M5StickC Plus

Connect the laptop to the M5StickC Plus with the USB-C cable. Then connect the smart ring to the M5StickC Plus with **four wires**:

| M5StickC Plus pin | Smart ring pin |
|---|---|
| `GND` | `GND` |
| `G26` | `SCL` (also labeled `Rx`) |
| `G0`  | `SDA` (also labeled `Tx`) |
| `3V3` | `VIN` |

Double-check the pins before you plug the USB-C cable in. A wrong wire on `3V3` or `GND` can damage the sensor.

### 2. Install the Arduino IDE and the required libraries

1. Download the **Arduino IDE** (version 2.x) from <https://www.arduino.cc/en/software> and install it.
2. Open the Arduino IDE. Go to **File → Preferences**. In the box called **Additional boards manager URLs**, paste this address and click **OK**:
   ```
   https://m5stack.oss-cn-shenzhen.aliyuncs.com/resource/arduino/package_m5stack_index.json
   ```
3. Open **Tools → Board → Boards Manager**. In the search box, type `M5Stack`. Find the entry called **M5Stack by M5Stack Official** and click **Install**.
4. Open **Tools → Manage Libraries** (or press `Ctrl+Shift+I`). Install these three libraries one by one:
   - `M5StickCPlus` (by M5Stack)
   - `Adafruit BNO055` (by Adafruit)
   - `Adafruit Unified Sensor` (by Adafruit — Arduino IDE may ask to install this automatically as a dependency of `Adafruit BNO055`. If it asks, click **Install all**.)

### 3. Open the firmware sketch and add your hotspot details

1. In the Arduino IDE, click **File → Open…** and select the file [firmware/bno055_udp_streamer/bno055_udp_streamer.ino](firmware/bno055_udp_streamer/bno055_udp_streamer.ino) from this repository.
2. Near the top of the file, find this block:
   ```cpp
   // ==== Mobile Tethering and/or Hotspot settings ====
   const char* WIFI_SSID = "Insert your Hotspot SSID here";
   const char* WIFI_PASS = "Insert your Hotspot password here";
   ```
3. Replace the two placeholder strings with the **name** of your mobile hotspot (or Wi-Fi network) and its **password**. Keep the quotation marks.
4. (Optional) A few lines below, you will see `IPAddress LAPTOP_IP(192, 168, 0, 207);`. You can leave this value as it is for now. The sketch will still write the data to the USB serial port, which is what we read in step 6.

Save the file (`Ctrl+S`).

### 4. Select the board and the port

1. Plug the M5StickC Plus into the laptop with the USB-C cable. A small screen on the device should light up.
2. In the Arduino IDE, click the board selector dropdown at the top of the window (it usually says **Select Board**). Click **Select other board and port…**.
3. In the **Boards** list, type `m5stick` and choose **M5StickCPlus**.
4. In the **Ports** list on the right, choose the port that ends with **(USB)** — for example `COM3 Serial Port (USB)` on Windows, or `/dev/cu.usbserial-…` on macOS / Linux. Click **OK**.

If the port list is empty, unplug the device, wait two seconds, plug it back in, and try again. On Windows you may also need the [CP210x USB driver](https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers).

### 5. Upload the sketch to the M5StickC Plus

Click the **Upload** button (the right-arrow icon in the top-left of the Arduino IDE). The IDE will compile the sketch and send it to the M5StickC Plus. This takes about 30–60 seconds the first time. When it is finished, the device screen will show "Connecting to Mobile Hotspot…" and then "Mobile Hotspot connected." once the Wi-Fi is up.

If you see "Failed to connect to Mobile Hotspot, restarting…", check that your hotspot is turned on and that you typed the password correctly in step 3, then re-upload.

### 6. Save the serial output as a `.txt` log

1. Make sure your mobile hotspot is on, and that the M5StickC Plus shows "Mobile Hotspot connected.".
2. In the Arduino IDE, open **Tools → Serial Monitor** (or press `Ctrl+Shift+M`).
3. In the Serial Monitor toolbar, change the baud-rate dropdown on the right to **115200 baud**.
4. You should now see lines like this scrolling past:
   ```
   159381,37.50,-84.13,-178.00,-10.360,1.050,-1.500
   159393,36.81,-83.56,-177.38,-10.870,1.260,-1.340
   ```
   Each line is one sample: `t_ms, roll, pitch, yaw, ax, ay, az`. The device produces ~100 lines per second.
5. **Start brushing.** Wear the smart ring on your finger and brush your teeth as you normally would.
6. When you are done, **stop the Serial Monitor**, then click the small **copy** icon at the top right of the Serial Monitor window (or select all the text with `Ctrl+A` and copy with `Ctrl+C`).
7. Open any plain-text editor (Notepad, VS Code, etc.), paste the text, and save it as a file ending in `.txt` — for example `2026-06-09_2030_my-first-session.txt`. Place it anywhere you like; you will pass its path to the CLI in the next section.

That file is your sensor log. You can now feed it to `ringbrush-coverage` exactly as shown in [How to reproduce](#how-to-reproduce) or in the next section.

> **Tip — labeled calibration recordings.** If you also want to retrain the region classifier on yourself (see "Point at labeled calibration logs" in the next section), repeat step 6 once per region while brushing only that region, and save each file with a name that contains the matching marker: `outer-front-only`, `outer-left-only`, `outer-right-only`, `inner-upper-only`, `inner-lower-only`, and `no-movement-idle` (the device sitting still on a table).

## Generate a video from a sensor log

### 1. Have a sensor log ready

A log is plain-text CSV with seven columns: `t_ms, roll, pitch, yaw, ax, ay, az`. Header rows, boot messages, malformed lines, and non-monotonic timestamps are tolerated and skipped. Angles are degrees, accelerations are m/s², and the expected ring sample rate is ~80 Hz.

### 2. (Optional) Point at labeled calibration logs

If you have one-region-at-a-time recordings, pass `--calibration-dir <folder>`. The region classifier is rebuilt when these six filename patterns are all present in that folder:

```
*outer-front-only*.txt   *inner-upper-only*.txt
*outer-left-only*.txt    *inner-lower-only*.txt
*outer-right-only*.txt   *no-movement-idle*.txt
```

If any are missing, the app falls back to bundled defaults derived from the original sample recordings.

### 3. Run the CLI

```powershell
ringbrush-coverage "C:/path/to/session.txt" `
  --calibration-dir "C:/path/to/labeled-logs" `
  --output ".\outputs\session.mp4" `
  --summary-json ".\outputs\session.json"
```

Or equivalently via the module entry point:

```powershell
python -m ringbrush_coverage "C:/path/to/session.txt" `
  --output ".\outputs\session.mp4"
```

Default render: **1280x720 at 30 FPS**. Override with `--width`, `--height`, `--fps`. Lower `--fps` for faster renders at the cost of smoothness.

### 4. Read the outputs

- **`session.mp4`** — mouth map with green intensity rising with accumulated coverage, a brush cursor, a short motion trail, and live per-zone coverage bars.
- **`session.json`** — parsed-row and skipped-row counts, session duration, calibration source, weighted coverage seconds and 0–100% coverage per zone.

## Other useful flags

- `--report-only` — skip the MP4 and just write the JSON + print the per-zone summary. Much faster for sanity checks.
- `--dr-method aeolus` — replace the default in-house heuristic dead reckoning with a port of the Radeta-2023 AEOLUS pipeline (Earth-frame gravity removal from roll/pitch, Algorithm 1 ZVU drift reduction, heading-projected position update). Returns metres internally and is rescaled per-session to the same visualization range as the heuristic.
- `--dr-method video-anchored --video-sync-csv <path>` — use a synchronized front-camera recording as ground truth. See "Video-anchored dead reckoning" below.
- `--heuristic-params <path>` — load JSON overrides for the heuristic DR constants (produced by `tools/calibrate_dr_from_video.py`).

## Video-anchored dead reckoning

If you also recorded a front-camera video of the session, the wrist position from each frame is a much stronger ground truth than IMU integration alone. The pipeline is:

```powershell
# Install the optional video deps
python -m pip install -e ".[video]"

# 1. Extract per-frame hand landmarks ("format-3" CSV)
python tools\extract_video_motion.py "C:/path/to/session.mp4" -o "C:/path/to/session_format-3.csv"

# 2. Cross-correlate with the IMU log to recover the time offset and produce a synchronized CSV
python tools\sync_video_imu.py "C:/path/to/session.txt" "C:/path/to/session_format-3.csv" --output-dir .\outputs\session-sync

# 3. Render with the new DR method
ringbrush-coverage "C:/path/to/session.txt" `
  --dr-method video-anchored `
  --video-sync-csv .\outputs\session-sync\synchronized_video_on_imu_time.csv `
  --output .\outputs\session-video-anchored.mp4
```

For windows where the video has hand-landmark coverage, the cursor is driven directly by the per-session normalized wrist position; for the rest (start/end gaps, MediaPipe miss bursts), the existing heuristic DR fills in. On the bundled 2026-05-29 session this drops mean cursor-to-GT distance from 0.31 (heuristic) and 0.32 (AEOLUS) to 0.03 mouth units — a 90% reduction.

## Compare both dead-reckoning methods on one log

```powershell
python tools\compare_dead_reckoning.py "C:/path/to/session.txt" `
  --output-dir .\outputs\dr-comparison
```

Emits a side-by-side PNG, a JSON stats summary, and an animated MP4. Add `--skip-mp4` for just the PNG + JSON.

## How the coverage map is built

For each ~1-second window the app:

1. Extracts orientation, acceleration, and angular-speed features.
2. Classifies the window into a region (`outer-front`, `outer-left`, `outer-right`, `inner-upper`, `inner-lower`, or `idle`).
3. Dead-reckons a per-window displacement and nudges the cursor.
4. **Gates coverage accumulation** by the median per-window displacement and acceleration std over the last few windows. Sustained out-of-mouth motion (e.g. demonstration sweeps much wider than a real mouth) still moves the cursor visually but stops adding to the coverage bars. This prevents false-positive coverage when motion is too wild to be real brushing.
5. Adds the gated, weighted coverage seconds to the dominant zone(s) and converts the totals to 0–100% bars.

## Notes and limitations

- Defaults are tuned to the bundled sample recordings under [recordings/](recordings/). Different rings or unusual brushing styles likely need fresh calibration.
- Dead reckoning is damped to keep cursor drift bounded — read it as a visual cue, not a medically precise trajectory.
- Retrain the heuristic dead-reckoning constants with `python tools\calibrate_dead_reckoning.py` after collecting new labeled left-right / up-down / inside-outside motion logs.
