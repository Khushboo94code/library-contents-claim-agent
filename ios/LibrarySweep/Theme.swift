// Theme.swift
// One design system for the whole app. Direction: warm editorial — a cream/beige base,
// muted sage green, a punchy lime accent and soft lavender, near-black "ink" text, big bold
// uppercase display headlines and generously rounded, softly shadowed cards.

import SwiftUI

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xff) / 255,
                  green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255,
                  opacity: alpha)
    }
}

enum Theme {
    // Surfaces (warm, light)
    static let bg = Color(hex: 0xE8E3D6)          // cream-beige app background
    static let bgDeep = Color(hex: 0xDAD4C4)      // slightly deeper beige (gradient end)
    static let surface = Color(hex: 0xF5F2EA)     // card / elevated cream
    static let surface2 = Color(hex: 0xDED8C8)    // neutral taupe panel
    static let stroke = Color(hex: 0x1C1C18, alpha: 0.08)

    // Accents
    static let accent = Color(hex: 0xA6C089)      // sage green (primary brand)
    static let accentA = Color(hex: 0xB2C994)     // sage light (gradient start)
    static let accentB = Color(hex: 0x8FB06E)     // sage deep (gradient end)
    static let lime = Color(hex: 0xDCEE4A)        // punchy chartreuse (CTA / highlight)
    static let lavender = Color(hex: 0xC7BCDF)    // soft purple (minor accent)

    // Status (tuned for readability on a light base)
    static let success = Color(hex: 0x3E9B57)
    static let warn = Color(hex: 0xC0871A)
    static let danger = Color(hex: 0xCC4436)
    static let speaking = Color(hex: 0x6E5FB0)

    // Text — near-black "ink" on warm surfaces
    static let textPrimary = Color(hex: 0x1C1C18)
    static let textSecondary = Color(hex: 0x1C1C18, alpha: 0.55)
    static let onAccent = Color(hex: 0x1C1C18)    // ink reads well on sage/lime

    static var accentGradient: LinearGradient {
        LinearGradient(colors: [accentA, accentB], startPoint: .topLeading, endPoint: .bottomTrailing)
    }
    static var bgGradient: LinearGradient {
        LinearGradient(colors: [bg, bgDeep], startPoint: .top, endPoint: .bottom)
    }

    /// Big, heavy, slightly-tightened display type for uppercase editorial headlines.
    static func display(_ size: CGFloat) -> Font { .system(size: size, weight: .heavy) }
}

// MARK: - Reusable styles

struct CardModifier: ViewModifier {
    var padding: CGFloat = 16
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .shadow(color: Color(hex: 0x1C1C18, alpha: 0.06), radius: 16, y: 8)
    }
}

extension View {
    func card(_ padding: CGFloat = 16) -> some View { modifier(CardModifier(padding: padding)) }
}

/// Punchy lime CTA with ink text — the one loud action on a calm, warm screen.
struct PrimaryButtonStyle: ButtonStyle {
    var enabled = true
    func makeBody(configuration c: Configuration) -> some View {
        c.label
            .font(.headline.weight(.bold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 17)
            .background(enabled ? AnyShapeStyle(Theme.lime) : AnyShapeStyle(Theme.surface2))
            .foregroundStyle(enabled ? Theme.onAccent : Theme.textSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .shadow(color: enabled ? Theme.lime.opacity(0.45) : .clear, radius: 14, y: 7)
            .opacity(c.isPressed ? 0.85 : 1)
            .scaleEffect(c.isPressed ? 0.99 : 1)
    }
}

/// Quiet neutral action — taupe fill, ink text, hairline stroke.
struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration c: Configuration) -> some View {
        c.label
            .font(.headline.weight(.semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(Theme.surface2)
            .foregroundStyle(Theme.textPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.stroke, lineWidth: 1))
            .opacity(c.isPressed ? 0.8 : 1)
    }
}
