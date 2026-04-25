#if canImport(SwiftUI)
import SwiftUI
import AgentSOARKit

public struct ChatView: View {
    @StateObject private var vm: ChatViewModel
    private let onSignOut: () -> Void

    public init(client: AgentCoreClient, auth: CognitoAuthClient, onSignOut: @escaping () -> Void) {
        _vm = StateObject(wrappedValue: ChatViewModel(client: client, auth: auth))
        self.onSignOut = onSignOut
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                transcript
                composer
            }
            .navigationTitle("AgentSOAR")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button(action: vm.resetSession) {
                        Label("New", systemImage: "square.and.pencil")
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Sign out", action: onSignOut)
                }
            }
            .alert(
                "Error",
                isPresented: Binding(
                    get: { vm.error != nil },
                    set: { isPresented in if !isPresented { vm.error = nil } }
                ),
                actions: { Button("OK") { vm.error = nil } },
                message: { Text(vm.error ?? "") }
            )
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(vm.messages) { msg in
                        MessageBubble(message: msg)
                            .id(msg.id)
                    }
                }
                .padding()
            }
            .onChange(of: vm.messages.last?.id) { last in
                guard let last else { return }
                withAnimation { proxy.scrollTo(last, anchor: .bottom) }
            }
        }
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask the SOAR agent…", text: $vm.draft, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...5)
                .disabled(vm.isLoading)
            Button {
                if vm.isLoading {
                    vm.cancel()
                } else {
                    vm.send()
                }
            } label: {
                Image(systemName: vm.isLoading ? "stop.circle.fill" : "arrow.up.circle.fill")
                    .font(.system(size: 28))
            }
            .disabled(!vm.isLoading && vm.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        VStack(alignment: alignment, spacing: 6) {
            ForEach(message.toolCalls) { call in
                ToolCallRow(call: call)
            }
            if !message.text.isEmpty || message.isStreaming {
                Text(message.text + (message.isStreaming ? "▍" : ""))
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(message.role == .user ? Color.accentColor.opacity(0.15) : Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
        }
        .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
    }

    private var alignment: HorizontalAlignment {
        message.role == .user ? .trailing : .leading
    }
}

struct ToolCallRow: View {
    let call: ChatMessage.ToolCall

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "wrench.and.screwdriver")
                    .font(.caption)
                Text(call.name)
                    .font(.caption.weight(.semibold))
                if call.result == nil {
                    ProgressView().controlSize(.mini)
                } else {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.green)
                }
            }
            if !call.args.isEmpty {
                Text(call.args)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            if let result = call.result, !result.isEmpty {
                Text(result)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(6)
            }
        }
        .padding(8)
        .background(Color(.tertiarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
#endif
