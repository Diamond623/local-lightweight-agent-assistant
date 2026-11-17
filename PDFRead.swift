import Foundation
import AppKit
import PDFKit

do {
    guard CommandLine.arguments.count == 2,
          let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
        throw NSError(domain: "Aurora", code: 1, userInfo: [NSLocalizedDescriptionKey: "PDF 无法打开，可能加密或已损坏"])
    }
    if document.isLocked { throw NSError(domain: "Aurora", code: 2, userInfo: [NSLocalizedDescriptionKey: "请先解锁 PDF"])}
    var parts: [String] = []
    var images: [String] = []
    var scanned = 0
    let count = min(document.pageCount, 200)
    for i in 0..<count {
        guard let page = document.page(at: i) else { continue }
        let text = page.string ?? ""
        parts.append("【第 \(i + 1) 页】\n" + text)
        if text.trimmingCharacters(in: .whitespacesAndNewlines).count < 20 {
            scanned += 1
            if images.count < 2 {
                let bounds = page.bounds(for: .mediaBox)
                let ratio = 1400 / max(bounds.width, bounds.height)
                let image = page.thumbnail(of: NSSize(width: bounds.width * ratio, height: bounds.height * ratio), for: .mediaBox)
                if let data = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data),
                   let jpg = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.85]) {
                    images.append("data:image/jpeg;base64," + jpg.base64EncodedString())
                }
            }
        }
        if parts.joined().count > 200000 { break }
    }
    let result: [String: Any] = ["text": parts.joined(separator: "\n\n"), "images": images,
                                "pages": document.pageCount, "scanned": scanned,
                                "limited": document.pageCount > 200 || scanned > 2]
    let output = try JSONSerialization.data(withJSONObject: result)
    FileHandle.standardOutput.write(output)
} catch {
    FileHandle.standardError.write(Data(error.localizedDescription.utf8))
    exit(1)
}
