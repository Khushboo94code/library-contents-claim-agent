// LiveAgentView.swift — native live-agent overlay, styled with the design system.

import SwiftUI

struct LiveAgentView: View {
    @ObservedObject var voice: LiveVoiceManager
    @ObservedObject var ar: ARSessionManager
    @State private var sweeping = false

    var body: some View {
        VStack(spacing: 10) {
            stateBanner
            statusPill
            Spacer()
            if !voice.inventory.isEmpty { inventoryPanel }
            if !voice.caption.isEmpty { captionView }
            Text("Point at the floor, then pan each shelf slowly · Hold the button to talk")
                .font(.caption2).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
            controls
        }
        .padding(16)
        .onAppear { ar.onCaptureIssue = { msg in voice.notify(msg) } }
        .onReceive(ar.$latestFrameDataURL.compactMap { $0 }) { url in voice.sendVisionFrame(url) }
    }

    private var dotColor: Color {
        switch voice.state {
        case .ready: return Theme.success
        case .listening: return Theme.danger
        case .thinking: return Theme.warn
        case .speaking: return Theme.speaking
        case .error: return Theme.danger
        case .connecting: return .gray
        }
    }

    private var stateBanner: some View {
        HStack(spacing: 9) {
            Circle().fill(dotColor).frame(width: 10, height: 10)
                .shadow(color: dotColor.opacity(0.8), radius: voice.state == .connecting ? 0 : 5)
            Text(voice.stateText).font(.subheadline.weight(.semibold)).foregroundStyle(.white)
        }
        .padding(.horizontal, 16).padding(.vertical, 9)
        .background(.ultraThinMaterial, in: Capsule())
        .environment(\.colorScheme, .dark)
    }

    private var statusPill: some View {
        HStack(spacing: 10) {
            label("TRACK", ar.trackingState)
            dividerDot
            label("FRAMES", "\(ar.frameCount)")
            dividerDot
            label("PLANES", "\(ar.planeCount)")
            dividerDot
            label("PRICE", "\(voice.country)·\(voice.currency)")
            if voice.liveVision { dividerDot; label("VISION", "on") }
        }
        .padding(.horizontal, 14).padding(.vertical, 7)
        .background(Color.black.opacity(0.4), in: Capsule())
    }

    private var dividerDot: some View { Circle().fill(.white.opacity(0.25)).frame(width: 3, height: 3) }

    private func label(_ k: String, _ v: String) -> some View {
        HStack(spacing: 4) {
            Text(k).font(.system(size: 9, weight: .semibold)).foregroundStyle(.white.opacity(0.5))
            Text(v).font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
        }
    }

    private var inventoryPanel: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(voice.inventory.enumerated()), id: \.element.id) { i, e in
                    HStack(alignment: .top, spacing: 8) {
                        Text(e.kind.uppercased()).font(.system(size: 9, weight: .bold))
                            .foregroundStyle(Theme.accentB).frame(width: 46, alignment: .leading).padding(.top, 1)
                        Text(e.label + (e.note.isEmpty ? "" : " — \(e.note)")).font(.caption).foregroundStyle(.white)
                        Spacer()
                    }
                    .padding(.vertical, 5)
                    if i < voice.inventory.count - 1 { Divider().overlay(.white.opacity(0.08)) }
                }
            }
        }
        .frame(maxHeight: 170).padding(12)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .environment(\.colorScheme, .dark)
    }

    private var captionView: some View {
        Text(voice.caption).font(.subheadline).foregroundStyle(.white)
            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .environment(\.colorScheme, .dark)
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Button(action: toggleSweep) {
                Image(systemName: sweeping ? "stop.fill" : "record.circle")
                    .font(.title2).frame(width: 58, height: 58)
                    .background(sweeping ? Theme.danger : Color.white.opacity(0.12))
                    .foregroundStyle(.white).clipShape(Circle())
                    .overlay(Circle().stroke(.white.opacity(0.15), lineWidth: 1))
            }
            TalkButton(voice: voice)
        }
    }

    private func toggleSweep() {
        sweeping.toggle()
        if sweeping {
            ar.startSweep(country: voice.country, currency: voice.currency)
            voice.currentSweepId = ar.currentSweepId
            voice.notify("The sweep has started and the camera is on. Tell me which area to point at first.")
        } else {
            ar.stopSweep()
            voice.notify("The sweep has ended. One short closing sentence; the packet is being compiled.")
        }
    }
}

private struct TalkButton: View {
    @ObservedObject var voice: LiveVoiceManager
    @State private var held = false

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: held ? "waveform" : "mic.fill")
            Text(held ? "Listening…" : "Hold to talk")
        }
        .font(.headline.weight(.bold)).frame(maxWidth: .infinity).padding(.vertical, 18)
        .background(held ? AnyShapeStyle(Theme.danger) : AnyShapeStyle(Theme.accentGradient))
        .foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: (held ? Theme.danger : Theme.accent).opacity(0.4), radius: 12, y: 6)
        .opacity(voice.state == .ready || held ? 1 : 0.45)
        .scaleEffect(held ? 0.98 : 1)
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in if !held { held = true; voice.pttDown() } }
                .onEnded { _ in held = false; voice.pttUp() }
        )
    }
}
