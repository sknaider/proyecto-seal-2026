/** Read only the paste event supplied by the user; no Clipboard API permission. */
export function pastedImages(data: Pick<DataTransfer, "items" | "files">): File[] {
  const items = Array.from(data.items || [])
    .filter(item => item.kind === "file" && item.type.startsWith("image/"))
    .map(item => item.getAsFile()).filter((file): file is File => file !== null);
  // Browsers can expose the same image in both collections. Never double-add it.
  return items.length ? items : Array.from(data.files || []).filter(file => file.type.startsWith("image/"));
}
