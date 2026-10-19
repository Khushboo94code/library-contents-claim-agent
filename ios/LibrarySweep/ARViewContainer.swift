// ARViewContainer.swift
// Bridges ARKit's camera preview into SwiftUI and draws detected planes live so the
// user can SEE tracking working: horizontal planes (floor/table) = green, vertical
// planes (walls/shelf fronts) = blue. If no overlays appear, no planes are being
// detected — move more and point at the floor and walls.

import ARKit
import SwiftUI

struct ARViewContainer: UIViewRepresentable {
    let manager: ARSessionManager

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = manager.session         // share the manager's session
        view.delegate = context.coordinator    // SceneKit delegate for plane overlays
        view.automaticallyUpdatesLighting = true
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}

    final class Coordinator: NSObject, ARSCNViewDelegate {
        func renderer(_ renderer: SCNSceneRenderer, didAdd node: SCNNode, for anchor: ARAnchor) {
            guard let plane = anchor as? ARPlaneAnchor else { return }
            node.addChildNode(Self.overlay(for: plane))
        }

        func renderer(_ renderer: SCNSceneRenderer, didUpdate node: SCNNode, for anchor: ARAnchor) {
            guard let plane = anchor as? ARPlaneAnchor else { return }
            node.childNodes.forEach { $0.removeFromParentNode() }
            node.addChildNode(Self.overlay(for: plane))
        }

        private static func overlay(for plane: ARPlaneAnchor) -> SCNNode {
            let ext = plane.planeExtent
            let geom = SCNPlane(width: CGFloat(ext.width), height: CGFloat(ext.height))
            let color: UIColor = (plane.alignment == .horizontal) ? .systemGreen : .systemBlue
            geom.firstMaterial?.diffuse.contents = color.withAlphaComponent(0.35)
            geom.firstMaterial?.isDoubleSided = true

            let n = SCNNode(geometry: geom)
            n.simdPosition = plane.center
            // SCNPlane sits in the local XY plane; rotate it into the anchor's XZ plane.
            n.eulerAngles.x = -.pi / 2
            return n
        }
    }
}
