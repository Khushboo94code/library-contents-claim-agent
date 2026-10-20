// ReportView.swift
// Shows the claim report (HTML, dark) in-app, and lets the user DOWNLOAD it as a PDF.
// The PDF is rendered from a light, print-friendly variant (report?pdf=1) via WKWebView.createPDF.
// Everything is served over http://…:8000 (single server; no HTTPS/cert needed).

import SwiftUI
import WebKit

private let REPORT_BASE = "http://MacBook-Pro-2.local:8000"

struct ReportScreen: View {
    let sweepId: String
    var onNewSweep: () -> Void
    var onClaims: () -> Void = {}

    @State private var exporter: PDFExporter?
    @State private var exporting = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                iconButton("folder.fill", action: onClaims)
                LogoMark(size: 26, corner: 7)
                Text("CLAIM PACKET").font(Theme.display(17)).tracking(-0.2).foregroundStyle(Theme.textPrimary)
                Spacer()
                Button(action: downloadPDF) {
                    HStack(spacing: 6) {
                        if exporting { ProgressView().tint(Theme.onAccent).scaleEffect(0.8) }
                        else { Image(systemName: "arrow.down.doc.fill") }
                        Text("PDF").font(.subheadline.weight(.bold))
                    }
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Theme.lime).foregroundStyle(Theme.onAccent)
                    .clipShape(Capsule())
                }
                .disabled(exporting)
                iconButton("camera.viewfinder", action: onNewSweep)
            }
            .padding(14)
            .background(Theme.surface)

            ReportWebView(sweepId: sweepId)
        }
        .background(Theme.bg.ignoresSafeArea())
        .preferredColorScheme(.light)
    }

    private func iconButton(_ name: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.textPrimary)
                .frame(width: 36, height: 36).background(Theme.surface2).clipShape(Circle())
        }
    }

    private func downloadPDF() {
        exporting = true
        let url = URL(string: "\(REPORT_BASE)/sweeps/\(sweepId)/report?pdf=1")!
        let exp = PDFExporter()
        exporter = exp  // retain while it works
        exp.export(url: url, filename: "\(sweepId)-claim.pdf") { pdfURL in
            exporting = false
            exporter = nil
            if let pdfURL { presentShare(pdfURL) }
        }
    }
}

/// Renders a URL to a PDF off-screen (light report variant), then returns the file URL.
final class PDFExporter: NSObject, WKNavigationDelegate {
    private var web: WKWebView?
    private var filename = "claim.pdf"
    private var completion: ((URL?) -> Void)?

    func export(url: URL, filename: String, completion: @escaping (URL?) -> Void) {
        self.filename = filename
        self.completion = completion
        // A4-ish canvas at ~96dpi so the PDF paginates nicely.
        let w = WKWebView(frame: CGRect(x: 0, y: 0, width: 794, height: 1123))
        w.navigationDelegate = self
        web = w
        w.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // Let layout settle (fonts/SVG) before capturing.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            webView.createPDF { [weak self] result in
                guard let self else { return }
                var out: URL?
                if case .success(let data) = result {
                    let u = FileManager.default.temporaryDirectory.appendingPathComponent(self.filename)
                    try? data.write(to: u)
                    out = u
                }
                self.completion?(out)
                self.web = nil
            }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        completion?(nil); web = nil
    }
}

/// Present a share sheet for the generated file.
@MainActor func presentShare(_ url: URL) {
    guard let scene = UIApplication.shared.connectedScenes.first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene,
          let root = scene.keyWindow?.rootViewController else { return }
    let vc = UIActivityViewController(activityItems: [url], applicationActivities: nil)
    // iPad popover anchor.
    vc.popoverPresentationController?.sourceView = root.view
    vc.popoverPresentationController?.sourceRect = CGRect(x: root.view.bounds.midX, y: 60, width: 0, height: 0)
    root.present(vc, animated: true)
}

private struct ReportWebView: UIViewRepresentable {
    let sweepId: String

    func makeUIView(context: Context) -> WKWebView {
        let web = WKWebView(frame: .zero)
        web.load(URLRequest(url: URL(string: "\(REPORT_BASE)/sweeps/\(sweepId)/report")!))
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
