import XCTest
@testable import AgentSOARKit

final class PKCETests: XCTestCase {
    func testPairsAreUniqueAcrossInvocations() {
        let a = PKCE.generate()
        let b = PKCE.generate()
        XCTAssertNotEqual(a.verifier, b.verifier)
        XCTAssertNotEqual(a.challenge, b.challenge)
    }

    func testVerifierUsesURLSafeAlphabet() {
        let p = PKCE.generate()
        let allowed = CharacterSet(charactersIn:
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        XCTAssertNil(p.verifier.rangeOfCharacter(from: allowed.inverted))
        XCTAssertNil(p.challenge.rangeOfCharacter(from: allowed.inverted))
    }

    func testMethodIsS256() {
        XCTAssertEqual(PKCE.generate().method, "S256")
    }
}
