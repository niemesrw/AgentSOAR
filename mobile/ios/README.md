# AgentSOAR iOS App

Native SwiftUI client for the AgentSOAR Bedrock AgentCore runtime. Talks to the
same `bedrock-agentcore.{region}.amazonaws.com/runtimes/{arn}/invocations`
endpoint as the React frontend, streams AG-UI events over SSE, and renders text
deltas + tool calls as they arrive.

## Layout

```
mobile/ios/AgentSOAR/
├── Package.swift                      Swift Package: AgentSOARKit + tests
├── Sources/
│   ├── AgentSOARKit/                  Reusable SDK (auth, streaming, parser)
│   │   ├── AgentCore/
│   │   │   ├── AgentCoreClient.swift  POST /invocations + SSE streaming
│   │   │   ├── AGUIParser.swift       Port of frontend agui parser
│   │   │   ├── SSEStream.swift        URLSession.AsyncBytes → events
│   │   │   └── StreamEvent.swift
│   │   ├── Auth/
│   │   │   ├── CognitoAuthClient.swift  PKCE OAuth via ASWebAuthenticationSession
│   │   │   ├── PKCE.swift               RFC 7636 code verifier/challenge
│   │   │   └── TokenStore.swift         Keychain-backed token persistence
│   │   └── Config/
│   │       └── AgentCoreConfig.swift  Decodes aws-exports.json
│   └── AgentSOARApp/                  SwiftUI app sources (drag into Xcode)
│       ├── App/AgentSOARApp.swift     @main entry + RootView
│       ├── Login/LoginView.swift      Sign-in button → ASWebAuthenticationSession
│       ├── Chat/ChatView.swift        Streaming transcript UI
│       ├── Chat/ChatViewModel.swift
│       ├── Chat/ChatMessage.swift
│       └── Resources/
│           ├── Info.plist             URL scheme: agentsoar://
│           └── aws-exports.example.json
└── Tests/AgentSOARKitTests/           XCTest suites
```

## Building the iOS app

The Swift Package compiles the SDK + tests on macOS / Linux. The full app needs
Xcode (SwiftUI, Keychain, ASWebAuthenticationSession).

### 1. Create the Xcode project

```bash
open -a Xcode mobile/ios/AgentSOAR/Package.swift   # opens the package
```

Then in Xcode:

1. **File → New → Project → iOS → App**, name `AgentSOAR`, interface SwiftUI,
   minimum deployment iOS 16.
2. Save it inside `mobile/ios/AgentSOAR/` (sibling to `Package.swift`).
3. Delete the default `ContentView.swift` and `*App.swift` Xcode generated.
4. Drag `Sources/AgentSOARApp/` into the project (Add to target: `AgentSOAR`).
5. Drag `Sources/AgentSOARApp/Resources/Info.plist` into the project, then in
   Build Settings set **Info.plist File** to that path.
6. **Add Package Dependency → Add Local…** and pick `mobile/ios/AgentSOAR`
   (the `Package.swift`). Add the `AgentSOARKit` library to the app target.

### 2. Wire up Cognito

Copy `Sources/AgentSOARApp/Resources/aws-exports.example.json` to
`aws-exports.json`, fill in real values from the deployed CDK stack, and add it
to the app bundle (drag into Xcode → Add to target).

The mobile redirect URI must be registered as an allowed callback on the
Cognito User Pool client. The example uses a custom scheme:

```
agentsoar://auth/callback
```

Add it via the AWS console or CDK:

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

### 3. Run

Plug in a device or pick a simulator and **⌘R**. First launch shows the login
screen; after Cognito sign-in tokens are stored in the iOS Keychain and the
chat view streams from the AG-UI runtime.

## Running tests

```bash
cd mobile/ios/AgentSOAR
swift test
```

The tests cover the AG-UI parser (event mapping, malformed inputs, full
end-to-end stream), PKCE generation, and `AgentCoreConfig` decoding. They run
on macOS or Linux without simulators.

## Wire format

Identical to the web client (`frontend/src/lib/agentcore-client/client.ts`):

```http
POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escapedArn}/invocations?qualifier=DEFAULT
Authorization: Bearer {cognito_access_token}
Content-Type: application/json
X-Amzn-Trace-Id: 1-{epochHex}-{uuid}
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: {sessionUuid}

{
  "threadId":   "{sessionUuid}",
  "runId":      "{uuid}",
  "messages":   [{"id": "{uuid}", "role": "user", "content": "..."}],
  "state":      {},
  "tools":      [],
  "context":    [],
  "forwardedProps": {}
}
```

Responses are SSE: each `data: <json>` line is fed through `AGUIParser` and
emitted as `StreamEvent`s.

## Security notes

- Access tokens live in the iOS Keychain (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`).
- User identity is extracted server-side from the JWT — never sent in the
  payload, mirroring the web client.
- PKCE S256 is mandatory for the auth code flow.
- `ASWebAuthenticationSession` is preferred over a custom WKWebView so federated
  identity providers and Cognito SSO cookies work transparently.

## Roadmap

- Push notifications for incoming security findings
- Background refresh for long-running runs
- Per-finding deep links (`agentsoar://findings/{id}`)
- Add Strands / LangGraph parsers for non-AG-UI patterns
