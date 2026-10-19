// Theme.swift
// One design system for the whole app. Direction (from current insurance/fintech + 2026
// trends): a trust-oriented indigo→violet accent, refined/muted jewel tones, a deep dark
// base with soft gradients, and clean elevated cards.

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
    // Surfaces
    static let bg = Color(hex: 0x0B0B13)
    static let surface = Color(hex: 0x16161F)
    static let surface2 = Color(hex: 0x1F1F2B)
    static let stroke = Color.white.opacity(0.08)

    // Accent (indigo -> violet)
    static let accent = Color(hex: 0x6C5CE7)
    static let accentA = Color(hex: 0x5B5BF0)
    static let accentB = Color(hex: 0x9B6BF2)

    // Status
    static let success = Color(hex: 0x32D74B)
    static let warn = Color(hex: 0xFFB020)
    static let danger = Color(hex: 0xFF453A)
    static let speaking = Color(hex: 0x0A84FF)

    // Text
    static let textPrimary = Color.white
    static let textSecondary = Color.white.opacity(0.6)

    static var accentGradient: LinearGradient {
        LinearGradient(colors: [accentA, accentB], startPoint: .topLeading, endPoint: .bottomTrailing)
    }
    static var bgGradient: LinearGradient {
        LinearGradient(colors: [Color(hex: 0x14131F), Color(hex: 0x08080E)], startPoint: .top, endPoint: .bottom)
    }
}

// MARK: - Reusable styles

struct CardModifier: ViewModifier {
    var padding: CGFloat = 16
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.stroke, lineWidth: 1))
    }
}

extension View {
    func card(_ padding: CGFloat = 16) -> some View { modifier(CardModifier(padding: padding)) }
}

struct PrimaryButtonStyle: ButtonStyle {
    var enabled = true
    func makeBody(configuration c: Configuration) -> some View {
        c.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(enabled ? AnyShapeStyle(Theme.accentGradient) : AnyShapeStyle(Color.white.opacity(0.12)))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .shadow(color: enabled ? Theme.accent.opacity(0.35) : .clear, radius: 14, y: 7)
            .opacity(c.isPressed ? 0.85 : 1)
            .scaleEffect(c.isPressed ? 0.99 : 1)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration c: Configuration) -> some View {
        c.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(Color.white.opacity(0.07))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.stroke, lineWidth: 1))
            .opacity(c.isPressed ? 0.8 : 1)
    }
}
