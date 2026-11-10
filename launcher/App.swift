import Cocoa
import WebKit

final class App: NSObject, NSApplicationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var loading = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Govee Lights", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        let editItem = NSMenuItem()
        menu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        NSApp.mainMenu = menu
        web = WKWebView()
        web.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Govee Lights"
        window.collectionBehavior = .fullScreenPrimary
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        load()
    }
    func load() {
        guard !loading else { return }
        loading = true
        DispatchQueue.global().async {
            let task = Process()
            let output = Pipe()
            task.executableURL = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "LightsPython") as! String)
            task.arguments = [Bundle.main.object(forInfoDictionaryKey: "LightsLauncher") as! String, "--url"]
            task.standardOutput = output
            task.standardError = Pipe()
            var url: URL?
            do {
                try task.run()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                if task.terminationStatus == 0 {
                    url = URL(string: String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
                }
            } catch {}
            let destination = url
            DispatchQueue.main.async {
                self.loading = false
                if let destination = destination {
                    self.web.load(URLRequest(url: destination))
                    self.window.makeKeyAndOrderFront(nil)
                    NSApp.activate(ignoringOtherApps: true)
                }
                else {
                    let alert = NSAlert()
                    alert.messageText = "Lights could not start"
                    alert.informativeText = "Open lights.log in your govee-lights folder for details, or run the installer again."
                    alert.runModal()
                }
            }
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        load()
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    // target="_blank" links (Send feedback, Claude Code help) do nothing in WKWebView without this; open them in the browser
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }
}
// The LaunchAgent runs this mode so macOS grants Local Network access to Govee Lights; a bare python agent is silently denied.
let server = Process()
if CommandLine.arguments.contains("--server") {
    server.executableURL = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "LightsPython") as! String)
    server.arguments = ["-u", Bundle.main.object(forInfoDictionaryKey: "LightsLauncher") as! String, "--server"]
    signal(SIGTERM) { _ in kill(server.processIdentifier, SIGTERM) }
    try server.run()
    server.waitUntilExit()
    exit(server.terminationStatus)
}
let delegate = App()
NSApplication.shared.delegate = delegate
NSApplication.shared.setActivationPolicy(.regular)
NSApplication.shared.run()
