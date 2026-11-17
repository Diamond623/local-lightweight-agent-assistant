import Cocoa
import ApplicationServices

func emit(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}
let arguments = CommandLine.arguments
let allowed = ["calculator":"com.apple.calculator", "textedit":"com.apple.TextEdit", "finder":"com.apple.finder"]
func buttons(_ app: NSRunningApplication) -> [(AXUIElement, String)] {
    let root = AXUIElementCreateApplication(app.processIdentifier)
    var found: [(AXUIElement, String)] = []
    func walk(_ element: AXUIElement, _ depth: Int) {
        if depth > 6 || found.count >= 40 { return }
        var role: CFTypeRef?
        AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role)
        if role as? String == kAXButtonRole as String {
            var label: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &label)
            let title = String((label as? String ?? "").prefix(80))
            if !title.isEmpty { found.append((element,title)) }
        }
        var children: CFTypeRef?
        if AXUIElementCopyAttributeValue(element,kAXChildrenAttribute as CFString,&children) == .success,
           let list = children as? [AXUIElement] {
            for child in list.prefix(70) { walk(child,depth+1) }
        }
    }
    walk(root,0)
    return found
}
if arguments.count >= 2 && (arguments[1] == "inspect" || arguments[1] == "click") {
    guard AXIsProcessTrusted(), let app = NSWorkspace.shared.frontmostApplication,
          ["com.apple.calculator","com.apple.TextEdit"].contains(app.bundleIdentifier ?? "") else {
        emit(["error":"请授权 AuroraDesktop 的辅助功能权限，并切换到计算器或文本编辑。"]);exit(1)
    }
    let items=buttons(app)
    if arguments[1] == "inspect", arguments.count == 2 {
        emit(["app":app.localizedName ?? "", "buttons":items.enumerated().map { ["index":$0.offset,"title":$0.element.1] }]);exit(0)
    }
    guard arguments.count == 4, let index=Int(arguments[2]), index >= 0, index < items.count,
          items[index].1 == arguments[3] else {emit(["error":"按钮已变化，请重新读取窗口。"]);exit(1)}
    let alert=NSAlert()
    alert.messageText="Aurora 请求点击按钮"
    alert.informativeText="应用：\(app.localizedName ?? "")\n按钮：\(items[index].1)\n只有你确认后才会执行。"
    alert.addButton(withTitle:"允许这一次")
    alert.addButton(withTitle:"取消")
    NSApplication.shared.setActivationPolicy(.regular)
    NSApplication.shared.activate(ignoringOtherApps:true)
    guard alert.runModal() == .alertFirstButtonReturn else {emit(["error":"用户取消了本次点击"]);exit(1)}
    guard !app.isTerminated else {emit(["error":"目标应用已经退出，未点击"]);exit(1)}
    let fresh=buttons(app)
    guard index < fresh.count, fresh[index].1 == arguments[3] else {emit(["error":"按钮已变化，未点击"]);exit(1)}
    guard AXUIElementPerformAction(fresh[index].0,kAXPressAction as CFString) == .success else {
        emit(["error":"系统未允许点击按钮"]);exit(1)
    }
    emit(["clicked":fresh[index].1,"app":app.localizedName ?? ""]);exit(0)
}
if arguments.count == 3 && arguments[1] == "open", let bundle = allowed[arguments[2]] {
    guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else { emit(["error":"应用未安装"]); exit(1) }
    let configuration = NSWorkspace.OpenConfiguration()
    NSWorkspace.shared.openApplication(at: url, configuration: configuration) { application, error in
        if let application = application { emit(["opened":application.localizedName ?? bundle, "bundle":bundle]) }
        else { emit(["error":"应用启动失败"] ) }
        exit(error == nil ? 0 : 1)
    }
    RunLoop.main.run()
} else if arguments.count == 2 && arguments[1] == "status" {
    guard let app = NSWorkspace.shared.frontmostApplication else { emit(["error":"未找到前台应用"]); exit(1) }
    var result: [String:Any] = ["app": app.localizedName ?? "", "bundle":app.bundleIdentifier ?? "", "accessibility":AXIsProcessTrusted()]
    if AXIsProcessTrusted() {
        let element = AXUIElementCreateApplication(app.processIdentifier)
        var value: CFTypeRef?
        if AXUIElementCopyAttributeValue(element, kAXWindowsAttribute as CFString, &value) == .success, let windows = value as? [AXUIElement] {
            result["windows"] = windows.prefix(5).map { window -> String in
                var title: CFTypeRef?
                AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &title)
                return String((title as? String ?? "").prefix(200))
            }
        }
    } else { result["note"] = "尚未授予辅助功能权限；只能识别前台应用，不能读取窗口标题。" }
    emit(result)
} else { emit(["error":"不支持的桌面操作"]); exit(1) }
