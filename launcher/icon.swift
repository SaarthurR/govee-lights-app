import Cocoa
let image = NSImage(size: NSSize(width: 512, height: 512))
image.lockFocus()
NSColor(calibratedRed: 0.11, green: 0.13, blue: 0.2, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: 512, height: 512), xRadius: 110, yRadius: 110).fill()
for (i, color) in [NSColor.systemOrange, .systemPink, .systemPurple, .systemBlue].enumerated() {
    color.setFill()
    NSBezierPath(ovalIn: NSRect(x: 66 + i * 96, y: 214, width: 80, height: 80)).fill()
}
image.unlockFocus()
let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
