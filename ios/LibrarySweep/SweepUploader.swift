// SweepUploader.swift
// Thin HTTP client for the backend ingest endpoints. Multipart for frames, JSON for finalize.

import Foundation

struct BackendConfig {
    // Set this to your Mac's LAN address, e.g. "http://192.168.1.20:8000".
    // On device + Mac on the same Wi-Fi. Find it with `ipconfig getifaddr en0` on the Mac.
    static let baseURL = "http://MacBook-Pro-2.local:8000"  // this Mac's LAN IP; re-check with `ipconfig getifaddr en0`
}

final class SweepUploader {

    private let session = URLSession(configuration: .default)

    func uploadFrame(sweepId: String, payload: CaptureFramePayload, jpeg: Data) async throws {
        let url = URL(string: "\(BackendConfig.baseURL)/sweeps/\(sweepId)/frame")!
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let metaJSON = try JSONEncoder().encode(payload)
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }

        // metadata field
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"metadata\"\r\n\r\n")
        body.append(metaJSON)
        append("\r\n")
        // image field
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\n")
        append("Content-Type: image/jpeg\r\n\r\n")
        body.append(jpeg)
        append("\r\n--\(boundary)--\r\n")

        req.httpBody = body
        let (_, resp) = try await session.data(for: req)
        try Self.check(resp)
    }

    func finalize(sweepId: String, meta: SweepMetaPayload, planes: [PlaneAnchorPayload]) async throws {
        let url = URL(string: "\(BackendConfig.baseURL)/sweeps/\(sweepId)/finalize")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        struct Body: Codable { let meta: SweepMetaPayload; let planes: [PlaneAnchorPayload] }
        req.httpBody = try JSONEncoder().encode(Body(meta: meta, planes: planes))
        let (_, resp) = try await session.data(for: req)
        try Self.check(resp)
    }

    private static func check(_ resp: URLResponse) throws {
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw NSError(domain: "SweepUploader", code: (resp as? HTTPURLResponse)?.statusCode ?? -1,
                          userInfo: [NSLocalizedDescriptionKey: "upload failed"])
        }
    }
}
