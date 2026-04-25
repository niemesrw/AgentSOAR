import XCTest
@testable import AgentSOARKit

final class AgentCoreConfigTests: XCTestCase {
    func testDecodesAwsExportsShape() throws {
        let json = #"""
        {
          "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:111:runtime/abc",
          "awsRegion": "us-east-1",
          "agentPattern": "agui-strands-agent",
          "authority": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AAA",
          "client_id": "abc123",
          "redirect_uri": "agentsoar://auth/callback",
          "response_type": "code",
          "scope": "email openid profile"
        }
        """#
        let cfg = try JSONDecoder().decode(AgentCoreConfig.self, from: Data(json.utf8))
        XCTAssertEqual(cfg.awsRegion, "us-east-1")
        XCTAssertEqual(cfg.agentPattern, "agui-strands-agent")
        XCTAssertEqual(cfg.clientId, "abc123")
        XCTAssertEqual(cfg.redirectUri, "agentsoar://auth/callback")
    }

    func testDefaultsForOptionalFields() throws {
        let json = #"""
        {
          "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:111:runtime/abc",
          "awsRegion": "us-east-1",
          "agentPattern": "agui-strands-agent",
          "authority": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AAA",
          "client_id": "abc123",
          "redirect_uri": "agentsoar://auth/callback"
        }
        """#
        // scope and response_type are required by the Codable above to be present;
        // this test documents that the example config must include them.
        XCTAssertThrowsError(try JSONDecoder().decode(AgentCoreConfig.self, from: Data(json.utf8)))
    }
}
