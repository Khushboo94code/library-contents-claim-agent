// Logo.swift — the app's brand mark, drawn in SwiftUI so it scales crisply anywhere.
// Motif: an open book framed by a camera viewfinder — "read + capture", the whole product
// in one glyph. Used on the welcome hero, the report header and the claims header, and
// exported (scripts/make_icon.swift) as the home-screen app icon so branding is consistent.

import SwiftUI

/// The glyph only (book + viewfinder), tinted white — compose it over any fill.
struct LogoGlyph: View {
    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width, geo.size.height)
            ZStack {
                // Open book: two pages fanning from a center spine.
                OpenBook()
                    .fill(.white)
                    .frame(width: s * 0.56, height: s * 0.42)
                    .shadow(color: .black.opacity(0.18), radius: s * 0.015, y: s * 0.01)
                // Viewfinder brackets around it.
                ViewfinderBrackets(lineWidth: s * 0.055, corner: s * 0.16, inset: s * 0.11)
                    .stroke(Color.white.opacity(0.95),
                            style: StrokeStyle(lineWidth: s * 0.055, lineCap: .round, lineJoin: .round))
            }
            .frame(width: s, height: s)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

/// The full mark: the glyph on the accent-gradient rounded tile.
struct LogoMark: View {
    var size: CGFloat = 64
    var corner: CGFloat? = nil

    var body: some View {
        let r = corner ?? size * 0.3
        ZStack {
            RoundedRectangle(cornerRadius: r, style: .continuous)
                .fill(Theme.accentGradient)
                .shadow(color: Theme.accent.opacity(0.5), radius: size * 0.24, y: size * 0.12)
            LogoGlyph().padding(size * 0.14)
        }
        .frame(width: size, height: size)
    }
}

/// Two pages meeting at a center spine, with a couple of text lines hinted on each page.
private struct OpenBook: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let w = rect.width, h = rect.height
        let cx = rect.midX
        let spineTop = CGPoint(x: cx, y: rect.minY + h * 0.08)
        let spineBot = CGPoint(x: cx, y: rect.maxY)

        // Left page.
        p.move(to: spineTop)
        p.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.minY + h * 0.02),
                       control: CGPoint(x: cx - w * 0.28, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY - h * 0.06))
        p.addQuadCurve(to: spineBot, control: CGPoint(x: cx - w * 0.26, y: rect.maxY - h * 0.02))
        p.closeSubpath()

        // Right page (mirror).
        p.move(to: spineTop)
        p.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY + h * 0.02),
                       control: CGPoint(x: cx + w * 0.28, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - h * 0.06))
        p.addQuadCurve(to: spineBot, control: CGPoint(x: cx + w * 0.26, y: rect.maxY - h * 0.02))
        p.closeSubpath()
        return p
    }
}

/// Four rounded corner brackets forming a camera viewfinder frame.
private struct ViewfinderBrackets: Shape {
    var lineWidth: CGFloat
    var corner: CGFloat
    var inset: CGFloat

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let r = rect.insetBy(dx: inset, dy: inset)
        let c = corner
        // Top-left.
        p.move(to: CGPoint(x: r.minX, y: r.minY + c)); p.addLine(to: CGPoint(x: r.minX, y: r.minY)); p.addLine(to: CGPoint(x: r.minX + c, y: r.minY))
        // Top-right.
        p.move(to: CGPoint(x: r.maxX - c, y: r.minY)); p.addLine(to: CGPoint(x: r.maxX, y: r.minY)); p.addLine(to: CGPoint(x: r.maxX, y: r.minY + c))
        // Bottom-right.
        p.move(to: CGPoint(x: r.maxX, y: r.maxY - c)); p.addLine(to: CGPoint(x: r.maxX, y: r.maxY)); p.addLine(to: CGPoint(x: r.maxX - c, y: r.maxY))
        // Bottom-left.
        p.move(to: CGPoint(x: r.minX + c, y: r.maxY)); p.addLine(to: CGPoint(x: r.minX, y: r.maxY)); p.addLine(to: CGPoint(x: r.minX, y: r.maxY - c))
        return p
    }
}

#Preview {
    ZStack { Color.black.ignoresSafeArea(); LogoMark(size: 160) }
}
