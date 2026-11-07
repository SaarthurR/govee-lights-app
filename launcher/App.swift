import Cocoa
import WebKit

final class App: NSObject, NSApplicationDelegate {
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
        NSApp.mainMenu = menu
        web = WKWebView()
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
}
let delegate = App()
NSApplication.shared.delegate = delegate
NSApplication.shared.setActivationPolicy(.regular)
NSApplication.shared.run()
