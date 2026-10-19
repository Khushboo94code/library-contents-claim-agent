// LiveVoiceManager.swift
// Native Gemini Live voice client. Replaces the WKWebView voice path (which WKWebView blocked
// at the wss-TLS layer with code 1006). URLSessionWebSocketTask DOES honor cert trust for
// WebSockets, and AVAudioEngine does mic capture + playback natively — no WebView involved.
//
// Protocol (same backend /live bridge as before):
//   send    : binary PCM16 16kHz (while holding) · {"type":"activity_start"|"activity_end"}
//             {"type":"text","text":...} · {"type":"tool_result","id","name","output"}
//   receive : binary PCM16 24kHz audio · {"type":"ready"|"out_transcript"|"in_transcript"|
//             "turn_complete"|"tool_call"|"error", ...}

import AVFoundation
import Combine
import Foundation

enum VoiceState { case connecting, ready, listening, thinking, speaking, error }

struct InventoryEntry: Identifiable {
    let id = UUID()
    let kind: String
    let label: String
    let note: String
}

final class LiveVoiceManager: NSObject, ObservableObject, URLSessionDelegate {

    // Native doesn't need TLS (that was only for the browser's getUserMedia), so use the
    // plain ws:// server on 8000 — no cert trust needed. ATS allows this via NSAllowsLocalNetworking.
    private let wsURL = URL(string: "ws://172.20.10.2:8000/live")!
    private let obsBase = "http://172.20.10.2:8000"

    @Published var state: VoiceState = .connecting
    @Published var stateText = "Connecting…"
    @Published var caption = ""
    @Published var inventory: [InventoryEntry] = []
    @Published var liveVision = false   // set from the backend's "ready" (GEMINI_LIVE_VISION)

    // Locale the sweep prices in. Defaulted from the DEVICE (never hardcoded), then confirmed
    // or corrected by voice at the start — the agent calls set_locale, which updates these,
    // and they flow into the sweep meta so pricing hits the right local Amazon marketplace.
    @Published var country = LiveVoiceManager.deviceCountry()
    @Published var currency = LiveVoiceManager.deviceCurrency()

    private static func deviceCountry() -> String {
        Locale.current.region?.identifier ?? "US"
    }
    private static func deviceCurrency() -> String {
        Locale.current.currency?.identifier ?? currencyFor(deviceCountry())
    }
    /// Fallback country -> currency map (matches the backend's supported Amazon marketplaces).
    static func currencyFor(_ cc: String) -> String {
        ["IN": "INR", "US": "USD", "GB": "GBP", "DE": "EUR", "AE": "AED",
         "SA": "SAR", "MX": "MXN", "TR": "TRY", "BR": "BRL"][cc.uppercased()] ?? "USD"
    }

    var currentSweepId = ""
    private var seenLabels = Set<String>()

    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var converter: AVAudioConverter?
    private var holding = false
    private var started = false
    private var reconnectAttempts = 0

    // lazy so they're created only when voice starts (not at app launch) — a nil here
    // must never crash the whole app before any UI renders.
    private lazy var targetIn = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private lazy var playFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!

    // MARK: - Lifecycle

    func start() {
        guard !started else { return }
        started = true
        seenLabels.removeAll()
        DispatchQueue.main.async { self.inventory = []; self.caption = "" }
        session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        setStatus(.connecting, "Connecting…")
        do {
            try configureAudio()
            try startEngine()
        } catch {
            setStatus(.error, "audio error: \(error.localizedDescription)")
            return
        }
        task = session.webSocketTask(with: wsURL)
        task?.resume()
        receiveLoop()
    }

    private func reconnect() {
        guard started else { return }
        reconnectAttempts += 1
        if reconnectAttempts > 6 {
            setStatus(.error, "connection lost")
            return
        }
        setStatus(.connecting, "Reconnecting…")
        task?.cancel()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self, self.started else { return }
            self.task = self.session.webSocketTask(with: self.wsURL)
            self.task?.resume()
            self.receiveLoop()
        }
    }

    func stop() {
        started = false
        holding = false
        reconnectAttempts = 0
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
    }

    // MARK: - Audio setup

    private func configureAudio() throws {
        let s = AVAudioSession.sharedInstance()
        // .mixWithOthers + .default mode coexists with ARKit's camera/capture pipeline
        // (.voiceChat reconfigures the hardware aggressively and fought ARKit -> camera -17281).
        try s.setCategory(.playAndRecord, mode: .default,
                          options: [.defaultToSpeaker, .allowBluetoothA2DP, .mixWithOthers])
        try s.setActive(true, options: [])
    }

    private func startEngine() throws {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)

        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: inFormat, to: targetIn)
        input.installTap(onBus: 0, bufferSize: 4096, format: inFormat) { [weak self] buffer, _ in
            self?.captureTap(buffer, inFormat: inFormat)
        }
        engine.prepare()
        try engine.start()
        player.play()
    }

    private func captureTap(_ buffer: AVAudioPCMBuffer, inFormat: AVAudioFormat) {
        guard holding, let converter, let task else { return }
        let ratio = targetIn.sampleRate / inFormat.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let out = AVAudioPCMBuffer(pcmFormat: targetIn, frameCapacity: capacity) else { return }
        var err: NSError?
        var fed = false
        converter.convert(to: out, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true; status.pointee = .haveData; return buffer
        }
        guard err == nil, out.frameLength > 0,
              let ch = out.int16ChannelData else { return }
        let data = Data(bytes: ch[0], count: Int(out.frameLength) * 2)
        task.send(.data(data)) { _ in }
    }

    private func playPCM24(_ data: Data) {
        let count = data.count / 2
        guard count > 0, let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(count)) else { return }
        buf.frameLength = AVAudioFrameCount(count)
        let ch = buf.floatChannelData![0]
        data.withUnsafeBytes { raw in
            let i16 = raw.bindMemory(to: Int16.self)
            for i in 0..<count { ch[i] = Float(Int16(littleEndian: i16[i])) / 32768.0 }
        }
        player.scheduleBuffer(buf, completionHandler: nil)
        if state != .speaking { setStatus(.speaking, "Speaking…") }
    }

    // MARK: - WebSocket

    private func receiveLoop() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                switch message {
                case .data(let d): self.playPCM24(d)
                case .string(let s): self.handleText(s)
                @unknown default: break
                }
                self.receiveLoop()
            case .failure:
                // Transient drop during an active session -> auto-reconnect (don't surface
                // an error unless reconnects are exhausted or we stopped intentionally).
                if self.started { self.reconnect() }
            }
        }
    }

    private func sendJSON(_ obj: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: obj),
              let s = String(data: data, encoding: .utf8) else { return }
        task?.send(.string(s)) { _ in }
    }

    private func handleText(_ s: String) {
        guard let data = s.data(using: .utf8),
              let ev = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = ev["type"] as? String else { return }
        switch type {
        case "ready":
            reconnectAttempts = 0  // healthy connection
            let lv = (ev["live_vision"] as? Bool) ?? false
            DispatchQueue.main.async { self.liveVision = lv }
            setStatus(.ready, "Ready — hold to talk")
            sendJSON(["type": "text", "text":
                "Greet the user warmly in one short sentence. The device is set to country \(country) " +
                "and currency \(currency); tell them you'll price in that currency and ask them to " +
                "confirm or tell you a different country — when they answer, call set_locale. " +
                "Then explain the sweep in ONE sentence: they walk the room once, slowly panning " +
                "across every shelf, wall and the floor, while you log what you see. Finally tell " +
                "them to tap Start sweep and hold the button to talk."])
        case "recognition":
            if let items = ev["items"] as? [[String: Any]] {
                for it in items {
                    let label = (it["label"] as? String) ?? ""
                    let key = label.lowercased()
                    guard !label.isEmpty, !seenLabels.contains(key) else { continue }
                    seenLabels.insert(key)
                    let entry = InventoryEntry(kind: (it["kind"] as? String) ?? "item", label: label, note: "seen live")
                    DispatchQueue.main.async { self.inventory.insert(entry, at: 0) }
                }
            }
        case "out_transcript":
            let t = (ev["text"] as? String) ?? ""
            DispatchQueue.main.async { self.caption += t }
        case "in_transcript":
            let t = (ev["text"] as? String) ?? ""
            DispatchQueue.main.async { self.caption = "you: " + t }
        case "turn_complete":
            DispatchQueue.main.async { self.caption = "" }
            if state != .listening { setStatus(.ready, "Ready — hold to talk") }
        case "tool_call":
            handleToolCall(ev)
        case "error":
            setStatus(.error, "⚠︎ " + ((ev["message"] as? String) ?? "error"))
        default: break
        }
    }

    private func handleToolCall(_ ev: [String: Any]) {
        let name = (ev["name"] as? String) ?? ""
        let args = (ev["args"] as? [String: Any]) ?? [:]
        if name == "log_observation" {
            let entry = InventoryEntry(kind: (args["kind"] as? String) ?? "note",
                                       label: (args["label"] as? String) ?? "",
                                       note: (args["note"] as? String) ?? "")
            DispatchQueue.main.async { self.inventory.insert(entry, at: 0) }
            syncObservation(entry)
        } else if name == "set_locale" {
            let cc = ((args["country"] as? String) ?? "").uppercased()
            guard !cc.isEmpty else {
                sendJSON(["type": "tool_result", "id": ev["id"] as? String ?? "", "name": name, "output": "ignored"])
                return
            }
            // Trust the agent's currency if given; otherwise derive from the country.
            let cur = (args["currency"] as? String).flatMap { $0.isEmpty ? nil : $0.uppercased() }
                ?? Self.currencyFor(cc)
            DispatchQueue.main.async { self.country = cc; self.currency = cur }
            sendJSON(["type": "tool_result", "id": ev["id"] as? String ?? "", "name": name,
                      "output": "pricing set to \(cc)/\(cur)"])
            return
        }
        sendJSON(["type": "tool_result", "id": ev["id"] as? String ?? "", "name": name, "output": "logged"])
    }

    private func syncObservation(_ e: InventoryEntry) {
        guard !currentSweepId.isEmpty,
              let url = URL(string: "\(obsBase)/sweeps/\(currentSweepId)/observation") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "kind": e.kind, "label": e.label, "note": e.note, "frame_ref": "", "at": 0])
        URLSession.shared.dataTask(with: req).resume()
    }

    // MARK: - Public controls (from the UI)

    func pttDown() {
        guard state == .ready else { return }
        holding = true
        sendJSON(["type": "activity_start"])
        setStatus(.listening, "● Listening — speak now")
    }

    func pttUp() {
        guard holding else { return }
        holding = false
        sendJSON(["type": "activity_end"])
        setStatus(.thinking, "Thinking…")
    }

    func notify(_ text: String) { sendJSON(["type": "text", "text": text]) }

    /// After a sweep, poll the backend until the packet is ready, then have the agent read
    /// the summary back by voice (scenario: "the agent reads back a summary"). Keeps the live
    /// session alive across background processing; gives up after ~5 minutes.
    func readBackSummary(sweepId: String) {
        guard !sweepId.isEmpty,
              let url = URL(string: "\(obsBase)/sweeps/\(sweepId)/summary") else { return }
        let deadline = Date().addingTimeInterval(300)
        func poll() {
            guard started, Date() < deadline else { return }
            var req = URLRequest(url: url)
            req.cachePolicy = .reloadIgnoringLocalCacheData
            URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
                guard let self else { return }
                if let data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   (obj["ready"] as? Bool) == true, let text = obj["text"] as? String, !text.isEmpty {
                    self.notify("The claim packet is ready. Read this summary back to the user " +
                                "warmly, as-is, in a few short sentences: " + text)
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 4) { poll() }
                }
            }.resume()
        }
        poll()
    }

    /// Stream a camera frame to the backend for live recognition (only when enabled).
    func sendVisionFrame(_ dataURL: String) {
        guard liveVision, state != .listening else { return }  // don't send while the user is talking
        let b64 = dataURL.contains(",") ? String(dataURL.split(separator: ",")[1]) : dataURL
        sendJSON(["type": "vision_frame", "data": b64])
    }

    // MARK: - Cert trust (this is why native works where WKWebView didn't)

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }

    private func setStatus(_ s: VoiceState, _ text: String) {
        DispatchQueue.main.async { self.state = s; self.stateText = text }
    }
}
