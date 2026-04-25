#if canImport(SwiftUI) && canImport(AuthenticationServices) && canImport(UIKit)
import SwiftUI
import UIKit
import AuthenticationServices
import AgentSOARKit

public struct LoginView: View {
    @State private var isWorking = false
    @State private var error: String?
    private let auth: CognitoAuthClient
    private let onSignedIn: () -> Void

    public init(auth: CognitoAuthClient, onSignedIn: @escaping () -> Void) {
        self.auth = auth
        self.onSignedIn = onSignedIn
    }

    public var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "shield.lefthalf.filled.badge.checkmark")
                .font(.system(size: 64, weight: .light))
                .foregroundStyle(.tint)
            Text("AgentSOAR")
                .font(.largeTitle.weight(.semibold))
            Text("Agentic SOAR on Bedrock AgentCore")
                .font(.callout)
                .foregroundStyle(.secondary)
            Spacer()
            Button {
                Task { await signIn() }
            } label: {
                HStack {
                    if isWorking { ProgressView().controlSize(.small) }
                    Text(isWorking ? "Signing in…" : "Sign in")
                        .font(.headline)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)
            .disabled(isWorking)
            if let error {
                Text(error).font(.caption).foregroundStyle(.red).multilineTextAlignment(.center)
            }
            Spacer().frame(height: 40)
        }
        .padding(32)
    }

    @MainActor
    private func signIn() async {
        isWorking = true
        defer { isWorking = false }
        error = nil
        do {
            guard let anchor = ASPresentationAnchor.firstKeyWindow else {
                error = "No window available."
                return
            }
            _ = try await auth.signIn(presentationAnchor: anchor)
            onSignedIn()
        } catch let AuthError.userCancelled {
            // silent — user dismissed the sheet
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private extension ASPresentationAnchor {
    static var firstKeyWindow: ASPresentationAnchor? {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first(where: { $0.isKeyWindow })
    }
}
#endif
