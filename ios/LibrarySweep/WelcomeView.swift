// WelcomeView.swift — onboarding / home, styled with the app design system.

import SwiftUI

struct WelcomeView: View {
    var backendOnline: Bool = false
    var onStart: () -> Void
    var onViewClaims: () -> Void = {}

    var body: some View {
        ZStack {
            Theme.bgGradient.ignoresSafeArea()
            // Soft accent glow behind the hero.
            Circle().fill(Theme.accent.opacity(0.22)).frame(width: 320, height: 320)
                .blur(radius: 90).offset(x: -80, y: -260)

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    hero
                    sectionCard(icon: "mic.fill", tint: Theme.accentB, title: "How it works", rows: [
                        "Talk to the agent — it greets you and guides the sweep by voice.",
                        "Walk the room once, panning slowly across every shelf, wall and floor.",
                        "On stop, it builds a priced claim packet and reads you a summary.",
                    ])
                    sectionCard(icon: "checkmark.seal.fill", tint: Theme.success, title: "For a good capture", rows: [
                        "Move slowly and actually walk — don't just rotate in place.",
                        "Point at the floor and walls so the room is measured.",
                        "Keep the printed marker in view — it sets real-world scale.",
                        "Good, even lighting; hold steady so spines stay readable.",
                    ])
                    sectionCard(icon: "lock.shield.fill", tint: Theme.speaking, title: "What it won't do", rows: [
                        "Never invents prices — every value is sourced and dated.",
                        "Unreadable spines are flagged for review, never guessed.",
                        "Art and rare books are routed to a human appraiser.",
                    ])
                    buttons
                }
                .padding(20)
            }
        }
        .preferredColorScheme(.dark)
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: 12) {
            LogoMark(size: 66, corner: 20)
            Text("Library Contents\nClaim Agent").font(.system(size: 34, weight: .bold, design: .rounded))
                .foregroundStyle(Theme.textPrimary)
            Text("Inventory and value your home library in one guided camera sweep.")
                .font(.title3).foregroundStyle(Theme.textSecondary)
        }
        .padding(.top, 36).padding(.bottom, 4)
    }

    private var buttons: some View {
        VStack(spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(backendOnline ? Theme.success : Theme.danger).frame(width: 8, height: 8)
                Text(backendOnline ? "Backend connected" : "Backend offline — start the server")
                    .font(.caption).foregroundStyle(Theme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button(action: onStart) {
                HStack { Image(systemName: "camera.viewfinder"); Text("Start a sweep") }
            }
            .buttonStyle(PrimaryButtonStyle(enabled: backendOnline))
            .disabled(!backendOnline)

            Button(action: onViewClaims) {
                HStack { Image(systemName: "folder.fill"); Text("View past claims") }
            }
            .buttonStyle(SecondaryButtonStyle())
        }
        .padding(.top, 6)
    }

    private func sectionCard(icon: String, tint: Color, title: String, rows: [String]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 9, style: .continuous).fill(tint.opacity(0.18)).frame(width: 32, height: 32)
                    Image(systemName: icon).font(.system(size: 15, weight: .semibold)).foregroundStyle(tint)
                }
                Text(title).font(.headline).foregroundStyle(Theme.textPrimary)
            }
            ForEach(rows, id: \.self) { r in
                HStack(alignment: .top, spacing: 10) {
                    Circle().fill(tint).frame(width: 5, height: 5).padding(.top, 7)
                    Text(r).font(.subheadline).foregroundStyle(Theme.textSecondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }
}
