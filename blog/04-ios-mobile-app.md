# Part 4 — Native iOS Client for AgentSOAR

The web frontend has been the only way to talk to AgentSOAR. That's fine at a
desk, but a SOAR platform is a thing you reach for when an alert pages you at
2am — which probably means you're holding a phone, not opening a laptop. This
post adds a native SwiftUI client that streams from the same AG-UI runtime as
the web app.

## Goals

1. **Same wire format as the web client.** No bespoke "mobile API" — anything
   the web app does, the iOS app does by hitting the same Bedrock AgentCore
   endpoint with a Cognito JWT.
2. **Cognito sign-in via the system browser.** No embedded WKWebView with
   stored credentials — `ASWebAuthenticationSession` so federated SSO and
   biometric password autofill keep working.
3. **Real-time streaming.** Token-by-token text deltas plus tool-call inline
   rendering, exactly like the web `ChatInterface`.
4. **No third-party SDKs.** Foundation, CryptoKit, AuthenticationServices,
   Security, SwiftUI — everything in the box.

## Layout

The iOS code lives at `mobile/ios/AgentSOAR/`:

```
mobile/ios/AgentSOAR/
├── Package.swift                Swift Package: AgentSOARKit + tests
├── Sources/
│   ├── AgentSOARKit/            Reusable SDK
│   │   ├── AgentCore/           Client, AG-UI parser, SSE reader
│   │   ├── Auth/                Cognito PKCE + Keychain
│   │   └── Config/              aws-exports.json decoder
│   └── AgentSOARApp/            SwiftUI app sources
└── Tests/                       XCTest suites
```

The SDK is split out so it can be reused — a future macOS menubar app, a
Shortcuts extension, an Apple Watch complication — without dragging the UI in.

## Wire format

Direct port of `frontend/src/lib/agentcore-client/client.ts`:

```swift
let endpoint = "https://bedrock-agentcore.\(region).amazonaws.com"
let url = URL(string: "\(endpoint)/runtimes/\(escapedArn)/invocations?qualifier=DEFAULT")!

var request = URLRequest(url: url)
request.httpMethod = "POST"
request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
request.setValue(traceId, forHTTPHeaderField: "X-Amzn-Trace-Id")
request.setValue(sessionId, forHTTPHeaderField: "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
request.httpBody = try JSONSerialization.data(withJSONObject: [
    "threadId": sessionId,
    "runId": UUID().uuidString.lowercased(),
    "messages": [["id": UUID().uuidString.lowercased(), "role": "user", "content": query]],
    "state": [:], "tools": [], "context": [], "forwardedProps": [:],
])

let (bytes, response) = try await URLSession.shared.bytes(for: request)
```

`URLSession.bytes(for:)` returns an `AsyncBytes` sequence — the same iterator
the web client gets from `response.body.getReader()`. We split on `\n`, hand
each line to the AG-UI parser, and yield `StreamEvent`s.

## AG-UI parser

The parser is a 1:1 translation of `parsers/agui.ts`. AG-UI events look like:

```
data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Hello"}
data: {"type":"TOOL_CALL_START","toolCallId":"t1","toolCallName":"lookup_finding"}
data: {"type":"TOOL_CALL_RESULT","toolCallId":"t1","content":"…"}
data: {"type":"RUN_FINISHED"}
```

`AGUIParser.parse(line:emit:)` strips the `data: ` prefix, decodes the JSON,
and emits typed events:

```swift
public enum StreamEvent: Sendable, Equatable {
    case text(String)
    case toolUseStart(toolUseId: String, name: String)
    case toolUseDelta(toolUseId: String, input: String)
    case toolResult(toolUseId: String, result: String)
    case result(stopReason: String)
    case lifecycle(String)
}
```

The XCTest suite covers each event type, malformed JSON, missing fields, and
a full end-to-end stream.

## Cognito sign-in

OAuth Authorization Code + PKCE through `ASWebAuthenticationSession`:

```swift
var components = URLComponents(url: discovery.authorizationEndpoint, resolvingAgainstBaseURL: false)!
components.queryItems = [
    URLQueryItem(name: "client_id", value: config.clientId),
    URLQueryItem(name: "response_type", value: "code"),
    URLQueryItem(name: "scope", value: config.scope),
    URLQueryItem(name: "redirect_uri", value: config.redirectUri),
    URLQueryItem(name: "code_challenge", value: pkce.challenge),
    URLQueryItem(name: "code_challenge_method", value: "S256"),
    URLQueryItem(name: "state", value: state),
]

let session = ASWebAuthenticationSession(url: components.url!, callbackURLScheme: "agentsoar") { url, error in
    // exchange code → access_token, store in Keychain
}
session.start()
```

The redirect URI is a custom scheme (`agentsoar://auth/callback`), registered
both in `Info.plist` and as an allowed callback on the Cognito User Pool
client. PKCE comes from `CryptoKit.SHA256` — no external deps.

Tokens go into the iOS Keychain with
`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. Refresh-token rotation is
automatic — the client checks `expiresAt` before every invoke and refreshes
silently if needed.

### Why not Amplify iOS?

Amplify drags in a chunk of AWS SDK and an opinionated state model. For an
app that only needs OIDC + a single REST endpoint, ~400 lines of native
Swift is smaller, easier to audit, and doesn't fight ARC over the URLSession
delegate lifecycle.

## Streaming UI

`ChatViewModel` consumes the `AsyncThrowingStream<StreamEvent, Error>` and
appends to a `@Published [ChatMessage]`. Tool calls are tracked alongside the
assistant message that triggered them so they render inline:

```swift
for try await event in stream {
    switch event {
    case let .text(content):
        messages[idx].text.append(content)
    case let .toolUseStart(id, name):
        messages[idx].toolCalls.append(.init(id: id, name: name))
    case let .toolUseDelta(id, input):
        // accumulate JSON arguments
    case let .toolResult(id, result):
        // mark tool complete
    …
    }
}
```

The view shows a `▍` cursor on the streaming bubble, a wrench icon on each
tool call with a spinner that flips to a green check when the result lands,
and `ScrollViewReader` auto-scrolls as new content arrives.

## Configuration

The mobile app loads the same `aws-exports.json` the web app does — minus
some fields it doesn't need:

```json
{
  "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:429971481640:runtime/…",
  "awsRegion": "us-east-1",
  "agentPattern": "agui-strands-agent",
  "authority": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_…",
  "client_id": "…",
  "redirect_uri": "agentsoar://auth/callback",
  "response_type": "code",
  "scope": "email openid profile"
}
```

The CDK stack already generates `aws-exports.json` for the web frontend; we
just bundle a copy into the iOS app target. Eventually we'll add a build
script that fetches it from the deployed Amplify origin so the mobile bundle
stays in sync automatically.

## Cognito callback URL

Cognito only allows pre-registered callback URLs. To accept the iOS scheme:

```bash
aws cognito-idp update-user-pool-client \
  --user-pool-id us-east-1_xxx \
  --client-id xxx \
  --callback-urls "https://app.example.com/" "agentsoar://auth/callback" \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes openid email profile \
  --allowed-o-auth-flows-user-pool-client \
  --profile blanxlait-security
```

This will go into a CDK property in a follow-up PR — for now it's a one-time
manual step.

## What's missing

- Push notifications for incoming GuardDuty findings (the
  [Part 3 integration](03-guardduty-integration.md) is the obvious source).
- Background refresh so a long-running playbook can finish while the app is
  backgrounded.
- A finding-deep-link handler (`agentsoar://findings/{id}`) so a notification
  can drop you straight into the relevant chat.
- macOS Catalyst build — most of the work is already done since the SDK has
  no UIKit dependencies.

## Try it

```bash
git clone https://github.com/niemesrw/AgentSOAR.git
cd AgentSOAR/mobile/ios/AgentSOAR
open Package.swift   # or follow mobile/ios/README.md to scaffold the Xcode app
swift test
```

The unit tests run on macOS or Linux without simulators. The full app needs
Xcode 15 and an iOS 16+ device.

Next up: wiring push notifications into the GuardDuty pipeline so the agent
can wake your phone.
