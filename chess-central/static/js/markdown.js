// Minimal markdown renderer for coach notes (headers, bold, italics, lists).
// Escapes HTML first — LLM output is treated as untrusted text.
export function renderMarkdown(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

  const lines = esc(md).split("\n");
  const out = [];
  let inList = false;
  for (const line of lines) {
    const li = line.match(/^\s*[-*]\s+(.*)/);
    const oli = line.match(/^\s*\d+\.\s+(.*)/);
    if (li || oli) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline((li || oli)[1])}</li>`);
      continue;
    }
    if (inList) { out.push("</ul>"); inList = false; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      const lvl = Math.min(h[1].length + 2, 5);
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
    } else if (line.trim() === "") {
      out.push("");
    } else {
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (inList) out.push("</ul>");
  return out.join("\n");
}
