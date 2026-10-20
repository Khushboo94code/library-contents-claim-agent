// BackendMonitor.swift
// Polls the backend /health every few seconds so the UI can show whether the backend is
// reachable (green) or not (red) — no more black box.

import Combine
import Foundation

final class BackendMonitor: ObservableObject {
    @Published var online = false
    @Published var lastChecked: Date?

    private var timer: Timer?
    // Uploads server; if this is reachable the backend process is alive.
    private let url = URL(string: "http://MacBook-Pro-2.local:8000/health")!

    func start() {
        check()
        timer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in self?.check() }
    }

    func stop() { timer?.invalidate(); timer = nil }

    private func check() {
        var req = URLRequest(url: url)
        req.timeoutInterval = 2.5
        req.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: req) { [weak self] _, resp, err in
            let ok = err == nil && (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async {
                self?.online = ok
                self?.lastChecked = Date()
            }
        }.resume()
    }
}
