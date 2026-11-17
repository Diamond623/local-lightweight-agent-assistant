import Cocoa
import WebKit
import CoreLocation

final class Delegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply, CLLocationManagerDelegate {
    private let locator = CLLocationManager()
    private let geocoder = CLGeocoder()
    private var locationReply: ((Any?, String?) -> Void)?
    private var locationTimeout: DispatchWorkItem?
    private func finishLocation(_ city: String? = nil, error: String? = nil) {
        guard let reply = locationReply else { return }
        locationReply = nil
        locationTimeout?.cancel()
        locator.stopUpdatingLocation()
        geocoder.cancelGeocode()
        if let city = city { reply(["city": city], nil) }
        else { reply(nil, error ?? "无法识别所在城市，请手动填写。") }
    }
    func userContentController(_ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.frameInfo.isMainFrame,
              let source = message.frameInfo.request.url,
              let base = URL(string: CommandLine.arguments[1]),
              source.scheme == base.scheme, source.host == base.host, source.port == base.port,
              source.path == "/", message.body as? String == "locate" else {
            replyHandler(nil, "不允许此页面访问定位。"); return
        }
        guard locationReply == nil else { replyHandler(nil, "正在定位，请稍等。"); return }
        locationReply = replyHandler
        locator.delegate = self
        locator.desiredAccuracy = kCLLocationAccuracyThreeKilometers
        let timeout = DispatchWorkItem { [weak self] in self?.finishLocation(error: "定位超时，请手动填写城市或重试。") }
        locationTimeout = timeout
        DispatchQueue.main.asyncAfter(deadline: .now() + 35, execute: timeout)
        handleLocationAuthorization()
    }
    private func handleLocationAuthorization() {
        switch locator.authorizationStatus {
        case .notDetermined: locator.requestWhenInUseAuthorization()
        case .authorizedAlways, .authorizedWhenInUse: locator.requestLocation()
        case .denied, .restricted: finishLocation(error: "定位未获授权。可在系统设置的隐私与安全性中允许 Aurora 定位，或手动填写城市。")
        @unknown default: finishLocation(error: "定位服务不可用。")
        }
    }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if locationReply != nil { handleLocationAuthorization() }
    }
    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        finishLocation(error: "暂时无法定位，请检查系统定位服务，或手动填写城市。")
    }
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard locationReply != nil, let location = locations.last else { return }
        guard abs(location.timestamp.timeIntervalSinceNow) < 300, location.horizontalAccuracy >= 0 else { return }
        manager.stopUpdatingLocation()
        geocoder.reverseGeocodeLocation(location, preferredLocale: Locale(identifier: "zh_CN")) { [weak self] places, error in
            guard let place = places?.first, let city = place.locality ?? place.subAdministrativeArea ?? place.administrativeArea else {
                self?.finishLocation(error: "位置已获取，但无法识别城市；请手动填写。"); return
            }
            self?.finishLocation(city)
        }
    }

    var window: NSWindow!
    private var chatView: WKWebView?
    @objc private func returnToChat() {
        guard CommandLine.arguments.count > 1, let url = URL(string: CommandLine.arguments[1]) else { return }
        chatView?.load(URLRequest(url: url))
    }
    private func installMenus() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem(title: "Aurora", action: nil, keyEquivalent: "")
        let appMenu = NSMenu(title: "Aurora")
        appMenu.addItem(withTitle: "退出 Aurora", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)

        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        // nil targets route standard actions through the focused WebKit editor.
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)
        let navigationItem = NSMenuItem(title: "导航", action: nil, keyEquivalent: "")
        let navigationMenu = NSMenu(title: "导航")
        let home = navigationMenu.addItem(withTitle: "返回对话", action: #selector(returnToChat), keyEquivalent: "h")
        home.keyEquivalentModifierMask = [.command, .shift]
        home.target = self
        navigationItem.submenu = navigationMenu
        mainMenu.addItem(navigationItem)
        NSApp.mainMenu = mainMenu
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        installMenus()
        NSApp.appearance = NSAppearance(named: .darkAqua)
        let config = WKWebViewConfiguration()
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "auroraLocation")
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.websiteDataStore = WKWebsiteDataStore.default()
        let web = WKWebView(frame: .zero, configuration: config)
        chatView = web
        web.navigationDelegate = self
        web.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Aurora · 本地助手"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(srgbRed: 5.0/255, green: 9.0/255, blue: 22.0/255, alpha: 1)
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        web.load(URLRequest(url: URL(string: CommandLine.arguments[1])!))
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        let base = URL(string: CommandLine.arguments[1])!
        if url.host == base.host && url.port == base.port { decisionHandler(.allow) }
        else {
            if ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "确认")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { result in
            completionHandler(result == .alertFirstButtonReturn)
        }
    }
}
if CommandLine.arguments.count == 1 {
    let root = Bundle.main.bundleURL.deletingLastPathComponent()
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = ["python3", root.appendingPathComponent("app.py").path]
    var childEnvironment = ProcessInfo.processInfo.environment
    childEnvironment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (childEnvironment["PATH"] ?? "/usr/bin:/bin")
    process.environment = childEnvironment
    process.currentDirectoryURL = root
    // Python owns the server and the actual window process. The launcher must
    // exit instead of remaining a second app with the same bundle identifier.
    do { try process.run(); exit(0) }
    catch {
        let alert = NSAlert()
        alert.messageText = "Aurora启动失败"
        alert.informativeText = error.localizedDescription
        alert.runModal()
        exit(1)
    }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.run()
