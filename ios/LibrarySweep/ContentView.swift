// ContentView.swift
// Top-level flow: Welcome -> Sweep (ARKit + live agent) -> Report.

import SwiftUI

enum AppPhase { case welcome, sweeping, report, claims }

struct ContentView: View {
    @StateObject private var manager = ARSessionManager()
    @StateObject private var voice = LiveVoiceManager()
    @StateObject private var backend = BackendMonitor()
    @State private var phase: AppPhase = .welcome
    @State private var reportSweepId = ""   // which sweep the report screen shows

    var body: some View {
        content
            .onAppear { backend.start() }
    }

    @ViewBuilder private var content: some View {
        switch phase {
        case .welcome:
            // Start voice ONCE on the transition (not in onAppear, which re-fires on the
            // frequent re-renders from ARKit's per-frame @Published updates and churns the socket).
            WelcomeView(backendOnline: backend.online,
                        onStart: { voice.start(); phase = .sweeping },
                        onViewClaims: { phase = .claims })

        case .sweeping:
            sweepScreen
                // When the sweep is finalized, advance to the report but KEEP voice alive so the
                // agent can read the summary back once the packet is ready (then it stops on exit).
                .onChange(of: manager.sweepEnded) { ended in
                    if ended {
                        reportSweepId = manager.currentSweepId
                        voice.readBackSummary(sweepId: reportSweepId)
                        phase = .report
                    }
                }

        case .report:
            ReportScreen(sweepId: reportSweepId,
                         onNewSweep: { voice.stop(); manager.sweepEnded = false; voice.start(); phase = .sweeping },
                         onClaims: { voice.stop(); manager.sweepEnded = false; phase = .claims })

        case .claims:
            ClaimsListView(
                onOpen: { reportSweepId = $0; phase = .report },
                onBack: { voice.stop(); phase = .welcome },
                onNewSweep: { voice.stop(); manager.sweepEnded = false; voice.start(); phase = .sweeping })
        }
    }

    private var sweepScreen: some View {
        ZStack {
            ARViewContainer(manager: manager).ignoresSafeArea()
            LiveAgentView(voice: voice, ar: manager).ignoresSafeArea()
        }
    }
}

#Preview { ContentView() }
