#if canImport(AuthenticationServices)
import AuthenticationServices
#endif
import Foundation

/// OIDC discovery document — the subset we care about.
private struct OIDCDiscovery: Decodable {
    let authorizationEndpoint: URL
    let tokenEndpoint: URL
    let endSessionEndpoint: URL?

    enum CodingKeys: String, CodingKey {
        case authorizationEndpoint = "authorization_endpoint"
        case tokenEndpoint = "token_endpoint"
        case endSessionEndpoint = "end_session_endpoint"
    }
}

private struct TokenResponse: Decodable {
    let accessToken: String
    let idToken: String?
    let refreshToken: String?
    let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case idToken = "id_token"
        case refreshToken = "refresh_token"
        case expiresIn = "expires_in"
    }
}

public enum AuthError: Error, LocalizedError {
    case userCancelled
    case missingCode
    case stateMismatch
    case invalidAuthority
    case discoveryFailed
    case tokenExchangeFailed(String)
    case notAvailable

    public var errorDescription: String? {
        switch self {
        case .userCancelled: return "Sign-in cancelled."
        case .missingCode: return "No authorization code returned by Cognito."
        case .stateMismatch: return "OAuth state mismatch — possible CSRF."
        case .invalidAuthority: return "Authority URL is invalid."
        case .discoveryFailed: return "Failed to load OIDC discovery document."
        case let .tokenExchangeFailed(detail): return "Token exchange failed: \(detail)"
        case .notAvailable: return "Web auth is unavailable on this platform."
        }
    }
}

/// OAuth Authorization Code + PKCE flow against the Cognito User Pool.
///
/// On iOS, we drive `ASWebAuthenticationSession` so the system Safari
/// view chrome handles the federated login (including SSO). Tokens are
/// persisted in the Keychain via `KeychainTokenStore`.
public final class CognitoAuthClient: NSObject {
    private let config: AgentCoreConfig
    private let store: KeychainTokenStore
    private let session: URLSession

    #if canImport(AuthenticationServices)
    /// Strong reference to the in-flight web auth session. `ASWebAuthenticationSession`
    /// is cancelled if it gets deallocated mid-flow, so we hold it here until the
    /// completion handler fires.
    @MainActor private var currentAuthSession: ASWebAuthenticationSession?
    #endif

    public init(
        config: AgentCoreConfig,
        store: KeychainTokenStore = KeychainTokenStore(),
        session: URLSession = .shared
    ) {
        self.config = config
        self.store = store
        self.session = session
    }

    public var currentTokens: KeychainTokenStore.Tokens? { store.load() }

    public func signOut() { store.clear() }

    /// Returns a non-expired access token, refreshing if needed.
    public func validAccessToken() async throws -> String? {
        guard let tokens = store.load() else { return nil }
        if tokens.expiresAt > Date().addingTimeInterval(30) {
            return tokens.accessToken
        }
        guard let refresh = tokens.refreshToken else { return nil }
        let discovery = try await discoverEndpoints()
        let refreshed = try await refresh(token: refresh, tokenEndpoint: discovery.tokenEndpoint)
        return refreshed.accessToken
    }

    #if canImport(AuthenticationServices)
    /// Drives the full OAuth flow. Must be called from the main actor; the
    /// presentation context provider keeps a reference to a host window for
    /// `ASWebAuthenticationSession`.
    @MainActor
    public func signIn(presentationAnchor: ASPresentationAnchor) async throws -> KeychainTokenStore.Tokens {
        let discovery = try await discoverEndpoints()
        let pkce = PKCE.generate()
        let expectedState = UUID().uuidString

        guard var components = URLComponents(url: discovery.authorizationEndpoint, resolvingAgainstBaseURL: false) else {
            throw AuthError.discoveryFailed
        }
        components.queryItems = [
            URLQueryItem(name: "client_id", value: config.clientId),
            URLQueryItem(name: "response_type", value: config.responseType),
            URLQueryItem(name: "scope", value: config.scope),
            URLQueryItem(name: "redirect_uri", value: config.redirectUri),
            URLQueryItem(name: "code_challenge", value: pkce.challenge),
            URLQueryItem(name: "code_challenge_method", value: pkce.method),
            URLQueryItem(name: "state", value: expectedState),
        ]
        guard let authURL = components.url else { throw AuthError.discoveryFailed }

        let callbackScheme = URL(string: config.redirectUri)?.scheme

        let callbackURL: URL = try await withCheckedThrowingContinuation { cont in
            let session = ASWebAuthenticationSession(
                url: authURL,
                callbackURLScheme: callbackScheme
            ) { [weak self] url, error in
                Task { @MainActor [weak self] in self?.currentAuthSession = nil }
                if let error {
                    if (error as NSError).code == ASWebAuthenticationSessionError.canceledLogin.rawValue {
                        cont.resume(throwing: AuthError.userCancelled)
                    } else {
                        cont.resume(throwing: error)
                    }
                    return
                }
                guard let url else { cont.resume(throwing: AuthError.missingCode); return }
                cont.resume(returning: url)
            }
            session.presentationContextProvider = AnchorProvider(anchor: presentationAnchor)
            session.prefersEphemeralWebBrowserSession = false
            currentAuthSession = session
            session.start()
        }

        let queryItems = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false)?.queryItems
        guard let returnedState = queryItems?.first(where: { $0.name == "state" })?.value,
              returnedState == expectedState
        else { throw AuthError.stateMismatch }
        guard let code = queryItems?.first(where: { $0.name == "code" })?.value else {
            throw AuthError.missingCode
        }

        return try await exchangeCode(
            code: code,
            verifier: pkce.verifier,
            tokenEndpoint: discovery.tokenEndpoint
        )
    }
    #else
    public func signIn(presentationAnchor: Any) async throws -> KeychainTokenStore.Tokens {
        throw AuthError.notAvailable
    }
    #endif

    // MARK: - HTTP

    private func discoverEndpoints() async throws -> OIDCDiscovery {
        // Cognito User Pool authorities serve OIDC discovery at
        // `{authority}/.well-known/openid-configuration`.
        guard let authorityURL = URL(string: config.authority),
              let discoveryURL = URL(string: "\(authorityURL.absoluteString)/.well-known/openid-configuration")
        else { throw AuthError.invalidAuthority }
        let (data, response) = try await session.data(from: discoveryURL)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw AuthError.discoveryFailed
        }
        return try JSONDecoder().decode(OIDCDiscovery.self, from: data)
    }

    private func exchangeCode(
        code: String,
        verifier: String,
        tokenEndpoint: URL
    ) async throws -> KeychainTokenStore.Tokens {
        var request = URLRequest(url: tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        let form: [String: String] = [
            "grant_type": "authorization_code",
            "client_id": config.clientId,
            "code": code,
            "redirect_uri": config.redirectUri,
            "code_verifier": verifier,
        ]
        request.httpBody = form.formURLEncoded()

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AuthError.tokenExchangeFailed(body)
        }
        let decoded = try JSONDecoder().decode(TokenResponse.self, from: data)
        let tokens = KeychainTokenStore.Tokens(
            accessToken: decoded.accessToken,
            idToken: decoded.idToken,
            refreshToken: decoded.refreshToken,
            expiresAt: Date().addingTimeInterval(TimeInterval(decoded.expiresIn))
        )
        try store.save(tokens)
        return tokens
    }

    private func refresh(token refreshToken: String, tokenEndpoint: URL) async throws -> KeychainTokenStore.Tokens {
        var request = URLRequest(url: tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        let form: [String: String] = [
            "grant_type": "refresh_token",
            "client_id": config.clientId,
            "refresh_token": refreshToken,
        ]
        request.httpBody = form.formURLEncoded()

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AuthError.tokenExchangeFailed(body)
        }
        let decoded = try JSONDecoder().decode(TokenResponse.self, from: data)
        // Cognito refresh responses don't always include a new refresh_token;
        // keep the existing one if absent.
        let tokens = KeychainTokenStore.Tokens(
            accessToken: decoded.accessToken,
            idToken: decoded.idToken,
            refreshToken: decoded.refreshToken ?? refreshToken,
            expiresAt: Date().addingTimeInterval(TimeInterval(decoded.expiresIn))
        )
        try store.save(tokens)
        return tokens
    }
}

#if canImport(AuthenticationServices)
private final class AnchorProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
    private let anchor: ASPresentationAnchor
    init(anchor: ASPresentationAnchor) { self.anchor = anchor }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        anchor
    }
}
#endif

private extension Dictionary where Key == String, Value == String {
    func formURLEncoded() -> Data {
        let allowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&+="))
        let body = self
            .map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")" }
            .joined(separator: "&")
        return Data(body.utf8)
    }
}
