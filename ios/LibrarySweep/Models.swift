// Models.swift
// Codable mirrors of backend/ingest/capture_schema.py — this is the native↔backend contract.
// Keep these in sync with the Python side.

import ARKit
import simd

// MARK: - Matrix serialization (gotcha!)
// simd matrices are COLUMN-major; the backend reshapes row-major. We transpose here
// so element (row, col) lands at flat index row*N + col. Getting this wrong produces
// plausible-but-wrong 3D numbers, so it is centralized in one place.

func rowMajor(_ m: simd_float4x4) -> [Float] {
    var out = [Float](); out.reserveCapacity(16)
    for row in 0..<4 { for col in 0..<4 { out.append(m[col][row]) } }
    return out
}

func rowMajor(_ m: simd_float3x3) -> [Float] {
    var out = [Float](); out.reserveCapacity(9)
    for row in 0..<3 { for col in 0..<3 { out.append(m[col][row]) } }
    return out
}

// MARK: - Bundle models

struct FrameQuality: Codable {
    var blur_score: Double?      // left nil here; computed server-side via OpenCV Laplacian
    var tracking_state: String   // "normal" | "limited:<reason>" | "notAvailable"
    var exposure_offset: Double?
}

struct CaptureFramePayload: Codable {
    var timestamp: Double
    var image: String            // overwritten by the server with the stored path
    var image_width: Int
    var image_height: Int
    var intrinsics: [Float]      // 3x3 row-major, rescaled to the uploaded image size
    var transform: [Float]       // 4x4 row-major, ARKit camera-to-world
    var quality: FrameQuality
}

struct PlaneAnchorPayload: Codable {
    var identifier: String
    var alignment: String        // "horizontal" | "vertical"
    var classification: String
    var transform: [Float]       // 4x4 row-major, plane-to-world
    var center: [Float]
    var extent: [Float]          // [width, height]
    var boundary_vertices: [[Float]]
}

struct SweepMetaPayload: Codable {
    var id: String
    var captured_at: String
    var device: String
    var duration_s: Double
    var country: String
    var currency: String
    var fiducial_marker_cm: Double?
    var fiducial_dict: String
}

// MARK: - ARKit -> payload helpers

extension ARCamera {
    func trackingStateString() -> String {
        switch trackingState {
        case .normal: return "normal"
        case .notAvailable: return "notAvailable"
        case .limited(let reason):
            switch reason {
            case .initializing: return "limited:initializing"
            case .excessiveMotion: return "limited:excessiveMotion"
            case .insufficientFeatures: return "limited:insufficientFeatures"
            case .relocalizing: return "limited:relocalizing"
            @unknown default: return "limited:unknown"
            }
        }
    }
}

extension ARPlaneAnchor {
    func toPayload() -> PlaneAnchorPayload {
        let align = (alignment == .horizontal) ? "horizontal" : "vertical"
        var cls = "none"
        if #available(iOS 12.0, *) {
            switch classification {
            case .wall: cls = "wall"
            case .floor: cls = "floor"
            case .ceiling: cls = "ceiling"
            case .table: cls = "table"
            case .seat: cls = "seat"
            case .window: cls = "window"
            case .door: cls = "door"
            default: cls = "none"
            }
        }
        let verts = geometry.boundaryVertices.map { [$0.x, $0.y, $0.z] }
        return PlaneAnchorPayload(
            identifier: identifier.uuidString,
            alignment: align,
            classification: cls,
            transform: rowMajor(transform),
            center: [center.x, center.y, center.z],
            extent: [planeExtent.width, planeExtent.height],
            boundary_vertices: verts
        )
    }
}
