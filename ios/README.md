# LibrarySweep — iOS capture app

A deliberately *thin* native layer: it runs an ARKit world-tracking session, saves
sharp well-tracked frames (image + camera intrinsics + world pose) plus the room's
plane anchors, and uploads them to the Python backend. **All measurement happens on
the backend** from this data.

> Requires a **physical iPhone** (ARKit does not run in the Simulator). Any iPhone
> works — LiDAR is *not* required; metric scale comes from ARKit world tracking plus
> a fiducial marker.

## 0. Install Xcode (one-time, large download ~7–12 GB)

1. Open the **App Store** on your Mac → search **Xcode** → Install.
2. Launch Xcode once, accept the license, let it install components.
3. (Optional CLI) `xcode-select --install`.

## 1. Create the project

Xcode's wizard handles signing and the project file for you — easier than hand-editing.

1. Xcode → **File ▸ New ▸ Project… ▸ iOS ▸ App** → Next.
2. Product Name: **LibrarySweep**. Interface: **SwiftUI**. Language: **Swift**. → create it
   anywhere (e.g. `~/Desktop/LibrarySweepApp`).
3. Delete the auto-generated `ContentView.swift` and `LibrarySweepApp.swift`.
4. **Drag in** every file from `ios/LibrarySweep/` in this repo
   (`LibrarySweepApp.swift`, `ContentView.swift`, `ARViewContainer.swift`,
   `ARSessionManager.swift`, `SweepUploader.swift`, `Models.swift`).
   Check **"Copy items if needed"** and your app target.

## 2. Permissions + capabilities

In the project editor → target **LibrarySweep** → **Info** tab, add:

| Key | Value |
|-----|-------|
| `NSCameraUsageDescription` | "Used to scan the room and books." |
| `NSMicrophoneUsageDescription` | "Used by the live voice agent." (for the later increment) |

ARKit needs a real device; nothing else to toggle for world tracking.

## 3. Point the app at your backend

Edit `SweepUploader.swift` → `BackendConfig.baseURL`:

1. Start the backend on your Mac (see repo root README): it listens on `:8000`.
2. On the Mac run `ipconfig getifaddr en0` to get its Wi-Fi IP (e.g. `192.168.1.20`).
3. Set `baseURL = "http://192.168.1.20:8000"` (phone + Mac on the **same Wi-Fi**).
   - Local HTTP is fine for dev; App Transport Security allows LAN HTTP to IP addresses.
     If a request is blocked, add an ATS exception in Info for your Mac's IP.

## 4. Run on your iPhone

1. Plug in the iPhone, unlock it, **Trust** the Mac.
2. In Xcode, select your device in the toolbar.
3. Signing: target → **Signing & Capabilities** → check **Automatically manage signing**,
   pick your personal **Team** (a free Apple ID works). Xcode makes a provisioning profile.
4. Press **▶ Run**. First launch: on the phone, **Settings ▸ General ▸ VPN & Device
   Management** → trust your developer certificate.

## 5. Capture a sweep

1. Tap **Start sweep**, walk the room slowly, pan across each shelf.
2. Watch the status bar: `tracking` should read `normal`; `frames` and `planes` climb.
3. Tap **Stop sweep** — this finalizes and uploads plane anchors.
4. On the Mac, confirm data landed: `data/sweeps/<sweep-id>/` has `frames/` + `bundle.json`.

## Notes / next increments

- The **fiducial marker** (printed ArUco of `FiducialConfig.markerCm` cm) should be in
  frame at the start of the sweep — it corrects global scale and validates the coordinate
  conventions. Generation + detection live on the backend (`backend/geometry/fiducial.py`).
- The **live voice agent + on-screen inventory** (OpenAI Realtime in a `WKWebView`) is the
  next native increment; this version proves capture→upload first.
- Frames are downscaled to 960 px wide on upload and the intrinsics are rescaled to match
  (see `ARSessionManager.captureAndUpload`) — do not change one without the other.
