#if canImport(SwiftUI)
import SwiftUI
import AgentSOARKit

@main
struct AgentSOARApp: App {
    @StateObject private var session = AppSession()

    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(session)
        }
    }
}

@MainActor
final class AppSession: ObservableObject {
    @Published var config: AgentCoreConfig?
    @Published var isAuthenticated: Bool = false
    @Published var loadError: String?

    private(set) var auth: CognitoAuthClient?
    private(set) var client: AgentCoreClient?

    init() { Task { await bootstrap() } }

    func bootstrap() async {
        do {
            let cfg = try Self.loadConfig()
            let auth = CognitoAuthClient(config: cfg)
            self.config = cfg
            self.auth = auth
            self.client = AgentCoreClient(config: cfg)
            self.isAuthenticated = (try await auth.validAccessToken()) != nil
        } catch {
            self.loadError = error.localizedDescription
        }
    }

    func signOut() {
        auth?.signOut()
        isAuthenticated = false
    }

    private static func loadConfig() throws -> AgentCoreConfig {
        guard let url = Bundle.main.url(forResource: "aws-exports", withExtension: "json") else {
            throw NSError(
                domain: "AgentSOAR", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "aws-exports.json not bundled. Copy from infra-cdk output."]
            )
        }
        return try AgentCoreConfig.load(from: url)
    }
}

struct RootView: View {
    @EnvironmentObject var session: AppSession

    var body: some View {
        if let error = session.loadError {
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill").font(.largeTitle)
                Text("Configuration error").font(.headline)
                Text(error).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding()
        } else if let auth = session.auth, let client = session.client {
            if session.isAuthenticated {
                ChatView(client: client, auth: auth, onSignOut: session.signOut)
            } else {
                LoginView(auth: auth) { session.isAuthenticated = true }
            }
        } else {
            ProgressView("Loading…")
        }
    }
}
#endif
