// ClaimsListView.swift — all insurance-claim sweeps, styled with the design system.

import SwiftUI

struct ClaimSummary: Identifiable, Decodable {
    let id: String
    var captured_at: String?
    var country: String?
    var currency: String?
    var book_count: Int?
    var items: Int?
    var books_cost: Double?
    var has_packet: Bool
}

struct ClaimsListView: View {
    var onOpen: (String) -> Void
    var onBack: () -> Void
    var onNewSweep: () -> Void

    @State private var claims: [ClaimSummary] = []
    @State private var loading = true
    private let base = "http://MacBook-Pro-2.local:8000"

    var body: some View {
        ZStack {
            Theme.bgGradient.ignoresSafeArea()
            VStack(spacing: 0) {
                header
                content
                Button(action: onNewSweep) {
                    HStack { Image(systemName: "camera.viewfinder"); Text("Start a new sweep") }
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(20)
            }
        }
        .preferredColorScheme(.light)
        .task { await load() }
    }

    @ViewBuilder private var content: some View {
        if loading {
            Spacer(); ProgressView().tint(Theme.textSecondary); Spacer()
        } else if claims.isEmpty {
            Spacer()
            VStack(spacing: 10) {
                Image(systemName: "tray").font(.system(size: 40)).foregroundStyle(Theme.textSecondary)
                Text("No claims yet").font(.headline).foregroundStyle(Theme.textPrimary)
                Text("Start a sweep to create one.").font(.subheadline).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
        } else {
            ScrollView {
                VStack(spacing: 12) {
                    ForEach(claims) { c in
                        Button { onOpen(c.id) } label: { row(c) }.buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 20).padding(.top, 4)
            }
        }
    }

    private var header: some View {
        HStack {
            Button(action: onBack) {
                Image(systemName: "chevron.left").font(.headline).foregroundStyle(Theme.textPrimary)
                    .frame(width: 38, height: 38).background(Theme.surface2).clipShape(Circle())
            }
            Spacer()
            HStack(spacing: 8) {
                LogoMark(size: 24, corner: 7)
                Text("INSURANCE CLAIMS").font(Theme.display(19)).tracking(-0.3).foregroundStyle(Theme.textPrimary)
            }
            Spacer()
            Button { Task { await load() } } label: {
                Image(systemName: "arrow.clockwise").font(.headline).foregroundStyle(Theme.textPrimary)
                    .frame(width: 38, height: 38).background(Theme.surface2).clipShape(Circle())
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    private func row(_ c: ClaimSummary) -> some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill((c.has_packet ? Theme.success : Theme.warn).opacity(0.16)).frame(width: 44, height: 44)
                Image(systemName: c.has_packet ? "doc.text.fill" : "hourglass")
                    .foregroundStyle(c.has_packet ? Theme.success : Theme.warn)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(title(c)).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.textPrimary)
                Text(subtitle(c)).font(.caption).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption.bold()).foregroundStyle(Theme.textSecondary.opacity(0.7))
        }
        .card(14)
    }

    private func title(_ c: ClaimSummary) -> String {
        if let d = c.captured_at, !d.isEmpty { return String(d.prefix(16).replacingOccurrences(of: "T", with: " ")) }
        return c.id
    }

    private func subtitle(_ c: ClaimSummary) -> String {
        guard c.has_packet else { return "processing…" }
        let cur = c.currency ?? ""
        return "\(c.book_count ?? 0) books · \(c.items ?? 0) items · \(cur) \(Int(c.books_cost ?? 0))"
    }

    private func load() async {
        loading = true; defer { loading = false }
        guard let url = URL(string: "\(base)/sweeps"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let list = try? JSONDecoder().decode([ClaimSummary].self, from: data) else { return }
        claims = list
    }
}
