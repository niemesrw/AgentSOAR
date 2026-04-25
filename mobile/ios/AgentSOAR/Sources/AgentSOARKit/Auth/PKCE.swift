import CryptoKit
import Foundation

/// Generates PKCE code verifier / challenge pairs per RFC 7636.
public enum PKCE {
    public struct Pair: Sendable, Equatable {
        public let verifier: String
        public let challenge: String
        public let method: String = "S256"
    }

    public static func generate() -> Pair {
        let verifier = randomURLSafeString(byteCount: 32)
        let challenge = sha256Base64URL(verifier)
        return Pair(verifier: verifier, challenge: challenge)
    }

    private static func randomURLSafeString(byteCount: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        _ = SecRandomCopyBytes(kSecRandomDefault, byteCount, &bytes)
        return Data(bytes).base64URLEncodedString()
    }

    private static func sha256Base64URL(_ value: String) -> String {
        let digest = SHA256.hash(data: Data(value.utf8))
        return Data(digest).base64URLEncodedString()
    }
}

extension Data {
    /// Base64URL encoding (RFC 4648 §5) — `+` → `-`, `/` → `_`, no padding.
    public func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
