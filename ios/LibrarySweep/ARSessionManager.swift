// ARSessionManager.swift
// Owns the ARKit session and is the whole "brain" of the thin native layer:
// run world tracking, throttle + capture frames (image + intrinsics + pose), collect
// plane anchors, and push everything to the backend. No measurement happens here.
//
// Concurrency note: ARSession delivers delegate callbacks on a BACKGROUND queue. Heavy
// work (image conversion) happens there, which is good; but every @Published write must
// be marshalled back to the main thread, or SwiftUI complains / crashes. We do that via
// `publish { ... }`. The class is intentionally NOT @MainActor (that would conflict with
// ARSessionDelegate's non-isolated requirements).

import ARKit
import Combine
import CoreImage
import UIKit

final class ARSessionManager: NSObject, ObservableObject, ARSessionDelegate {

    let session = ARSession()

    // Published state for the SwiftUI overlay.
    @Published var isSweeping = false
    @Published var frameCount = 0
    @Published var planeCount = 0
    @Published var trackingState = "—"
    @Published var lastUploadError: String?
    // Latest camera frame as a data URL, throttled — fed to the agent so it can "see".
    @Published var latestFrameDataURL: String?
    private var lastAgentFrameTime: TimeInterval = 0
    // Phase signalling for the UI (Welcome -> Sweep -> Report).
    @Published var currentSweepId: String = ""
    @Published var sweepEnded = false

    // Capture throttle: ARKit delivers ~60 fps; we save ~3 fps of sharp, well-tracked frames.
    private let captureInterval: TimeInterval = 0.33
    private var lastCaptureTime: TimeInterval = 0
    private var sweepStart: TimeInterval = 0
    private var sweepId = ""
    private var country = ""
    private var currency = ""

    // Final-state plane anchors, keyed by identifier (last write wins).
    private var planes: [UUID: ARPlaneAnchor] = [:]

    private let ciContext = CIContext()
    private let uploader = SweepUploader()

    // Upload downscale: smaller frames upload faster; intrinsics are rescaled to match.
    private let uploadMaxWidth: CGFloat = 960

    override init() {
        super.init()
        session.delegate = self
    }

    private func publish(_ block: @escaping () -> Void) {
        if Thread.isMainThread { block() } else { DispatchQueue.main.async(execute: block) }
    }

    // MARK: - Lifecycle (called from the UI / main thread)

    func startSweep(country: String, currency: String) {
        sweepId = "sweep-\(Int(Date().timeIntervalSince1970))"
        sweepStart = 0
        lastCaptureTime = 0
        planes.removeAll()
        self.country = country
        self.currency = currency

        publish {
            self.frameCount = 0
            self.planeCount = 0
            self.lastUploadError = nil
            self.isSweeping = true
            self.sweepEnded = false
            self.currentSweepId = self.sweepId
        }

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        config.worldAlignment = .gravity
        if type(of: config).supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)  // non-Pro devices just skip this
        }
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
    }

    func stopSweep() {
        publish { self.isSweeping = false }
        session.pause()
        finalizeAndUpload()
    }

    // MARK: - ARSessionDelegate (background queue)

    private var lastTrackingStateRaw = ""
    // On-device capture directing: the agent speaks these cues (no LLM/vision needed).
    var onCaptureIssue: ((String) -> Void)?
    private var lastCueTime: TimeInterval = 0

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        // Only publish when the tracking state actually CHANGES — publishing every frame
        // (~60/sec) re-renders the whole SwiftUI UI continuously and can freeze the device.
        let state = frame.camera.trackingStateString()
        if state != lastTrackingStateRaw {
            lastTrackingStateRaw = state
            publish { self.trackingState = state }
        }

        guard isSweeping else { return }

        // Direct the capture from ARKit's own tracking signal (free, in the moment).
        if case .limited(let reason) = frame.camera.trackingState,
           frame.timestamp - lastCueTime > 6.0 {
            lastCueTime = frame.timestamp
            let cue: String
            switch reason {
            case .excessiveMotion: cue = "I'm panning too fast and losing tracking — tell me to slow down."
            case .insufficientFeatures: cue = "The view is low on detail — tell me to move a little closer or add light."
            default: cue = "Tracking is unstable — tell me to hold steady for a moment."
            }
            let handler = onCaptureIssue
            publish { handler?(cue) }
        }
        if sweepStart == 0 { sweepStart = frame.timestamp }

        // Throttle, and skip frames while tracking is not usable.
        guard frame.timestamp - lastCaptureTime >= captureInterval else { return }
        if case .normal = frame.camera.trackingState {} else { return }
        lastCaptureTime = frame.timestamp

        captureAndUpload(frame: frame)
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        for case let p as ARPlaneAnchor in anchors { planes[p.identifier] = p }
        let n = planes.count
        publish { self.planeCount = n }
    }

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        for case let p as ARPlaneAnchor in anchors { planes[p.identifier] = p }
    }

    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        for case let p as ARPlaneAnchor in anchors { planes.removeValue(forKey: p.identifier) }
        let n = planes.count
        publish { self.planeCount = n }
    }

    // MARK: - Capture (background queue)

    private func captureAndUpload(frame: ARFrame) {
        let ts = frame.timestamp - sweepStart

        // Convert the YCbCr pixel buffer to a downscaled JPEG.
        let pixelBuffer = frame.capturedImage
        let fullW = CGFloat(CVPixelBufferGetWidth(pixelBuffer))
        let fullH = CGFloat(CVPixelBufferGetHeight(pixelBuffer))
        let scale = min(1.0, uploadMaxWidth / fullW)
        let outW = Int(fullW * scale)
        let outH = Int(fullH * scale)

        var ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        if scale < 1.0 {
            ciImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        guard let cg = ciContext.createCGImage(ciImage, from: ciImage.extent) else { return }
        guard let jpeg = UIImage(cgImage: cg).jpegData(compressionQuality: 0.8) else { return }

        // Rescale intrinsics to the uploaded image size (simd is column-major: K[col][row]).
        var K = frame.camera.intrinsics
        if scale < 1.0 {
            K[0][0] *= Float(scale); K[2][0] *= Float(scale)  // fx, cx
            K[1][1] *= Float(scale); K[2][1] *= Float(scale)  // fy, cy
        }

        let payload = CaptureFramePayload(
            timestamp: ts,
            image: "pending",
            image_width: outW,
            image_height: outH,
            intrinsics: rowMajor(K),
            transform: rowMajor(frame.camera.transform),
            quality: FrameQuality(blur_score: nil,
                                  tracking_state: frame.camera.trackingStateString(),
                                  exposure_offset: nil)
        )

        publish { self.frameCount += 1 }

        // Feed the agent a frame every ~2s so it can comment on what it sees.
        if frame.timestamp - lastAgentFrameTime >= 2.0 {
            lastAgentFrameTime = frame.timestamp
            let durl = "data:image/jpeg;base64," + jpeg.base64EncodedString()
            publish { self.latestFrameDataURL = durl }
        }

        let id = sweepId
        Task.detached { [uploader] in
            do {
                try await uploader.uploadFrame(sweepId: id, payload: payload, jpeg: jpeg)
            } catch {
                self.publish { self.lastUploadError = error.localizedDescription }
            }
        }
    }

    private func finalizeAndUpload() {
        let meta = SweepMetaPayload(
            id: sweepId,
            captured_at: ISO8601DateFormatter().string(from: Date()),
            device: UIDevice.current.model,
            duration_s: lastCaptureTime,
            country: country,
            currency: currency,
            fiducial_marker_cm: Double(FiducialConfig.markerCm),
            fiducial_dict: FiducialConfig.dict
        )
        let planePayloads = planes.values.map { $0.toPayload() }
        let id = sweepId
        Task.detached { [uploader] in
            do {
                try await uploader.finalize(sweepId: id, meta: meta, planes: planePayloads)
            } catch {
                self.publish { self.lastUploadError = error.localizedDescription }
            }
            // Signal the UI to move to the report screen once the bundle is saved.
            self.publish { self.sweepEnded = true }
        }
    }
}

enum FiducialConfig {
    // The printed marker's edge length in cm; must match .env FIDUCIAL_MARKER_CM.
    static let markerCm: Float = 15.0
    static let dict = "DICT_4X4_50"
}
