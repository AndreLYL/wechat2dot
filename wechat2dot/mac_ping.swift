// Send only to a positively identified conversation in the owner's running WeChat.
import AppKit
import ApplicationServices
import Foundation

func attribute(_ e: AXUIElement, _ key: String) -> CFTypeRef? {
    var result: CFTypeRef?
    guard AXUIElementCopyAttributeValue(e, key as CFString, &result) == .success else { return nil }
    return result
}
func string(_ e: AXUIElement, _ key: String) -> String { attribute(e,key) as? String ?? "" }
func nodes(_ root: AXUIElement) -> [AXUIElement] {
    var list = [root], idx = 0
    while idx < list.count && list.count < 3000 {
        list.append(contentsOf: attribute(list[idx], kAXChildrenAttribute) as? [AXUIElement] ?? [])
        idx += 1
    }
    return list
}
func pause(_ seconds: Double) { Thread.sleep(forTimeInterval: seconds) }
func unlocked() -> Bool {
    guard let s = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    return (s["CGSSessionScreenIsLocked"] as? Bool) != true && s["kCGSSessionOnConsoleKey"] as? Bool == true
}
let prompt = CommandLine.arguments.contains("--permission")
if prompt {
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    exit(AXIsProcessTrustedWithOptions(options) ? 0 : 1)
}
let demo = CommandLine.arguments.contains("--demo")
let check = CommandLine.arguments.contains("--eligible")
guard CommandLine.arguments.count >= 2, unlocked(), AXIsProcessTrusted(),
      demo || CGEventSource.secondsSinceLastEventType(.combinedSessionState,eventType:CGEventType(rawValue:~UInt32(0))!) >= 120,
      let app = NSRunningApplication.runningApplications(withBundleIdentifier:"com.tencent.xinWeChat").first
else { exit(2) }
if check { exit(0) }
let name = CommandLine.arguments[1], old = NSWorkspace.shared.frontmostApplication
func sendPing() -> Bool {
    guard !name.isEmpty else { return false }
    let ax = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(ax, 2)
    guard app.activate(options:[]) else { return false }
    defer { if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier { old?.activate(options:[]) } }
    pause(0.4)
    guard let window = (attribute(ax,kAXWindowsAttribute) as? [AXUIElement])?.first else { return false }
    func active() -> Bool { unlocked() && NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier }
    func exact(_ e: AXUIElement) -> Bool {
        return [kAXTitleAttribute,kAXValueAttribute,kAXDescriptionAttribute].contains { string(e,$0 as String)==name }
    }
    func verified() -> Bool {
        let search = nodes(window)
        return search.contains { n in
            let role=string(n,kAXRoleAttribute)
            return (role=="AXHeading" || role=="AXStaticText") && exact(n) &&
              (string(n,kAXIdentifierAttribute).lowercased().contains("title") || string(n,kAXRoleDescriptionAttribute)=="heading")
        }
    }
    if !verified() {
        let candidates=nodes(window).filter { n in
            let role=string(n,kAXRoleAttribute)
            return ["AXRow","AXCell","AXButton"].contains(role) && exact(n)
        }
        if candidates.count == 1, active() {
            guard AXUIElementPerformAction(candidates[0],kAXPressAction as CFString) == .success else {return false}
        } else {
            let search=nodes(window).filter { string($0,kAXSubroleAttribute)=="AXSearchField" }
            guard search.count==1,active(),
                AXUIElementSetAttributeValue(search[0],kAXValueAttribute as CFString,name as CFString) == .success else {return false}
            pause(0.6)
            let matches=nodes(window).filter { ["AXRow","AXCell","AXButton"].contains(string($0,kAXRoleAttribute)) && exact($0) }
            guard matches.count==1,active(),AXUIElementPerformAction(matches[0],kAXPressAction as CFString) == .success else {return false}
        }
        pause(0.5)
    }
    let fields=nodes(window).filter { string($0,kAXRoleAttribute)=="AXTextArea" }
    guard fields.count==1,active(),verified(),string(fields[0],kAXValueAttribute).isEmpty,
          AXUIElementSetAttributeValue(fields[0],kAXFocusedAttribute as CFString,kCFBooleanTrue) == .success,
          AXUIElementSetAttributeValue(fields[0],kAXValueAttribute as CFString,"/ping" as CFString) == .success,
          active(),verified(),(attribute(fields[0],kAXFocusedAttribute) as? Bool)==true,
          string(fields[0],kAXValueAttribute)=="/ping" else {return false}
    for down in [true,false] {
        guard let event=CGEvent(keyboardEventSource:nil,virtualKey:36,keyDown:down) else {return false}
        event.postToPid(app.processIdentifier)
    }
    return true
}
exit(sendPing() ? 0 : 2)
