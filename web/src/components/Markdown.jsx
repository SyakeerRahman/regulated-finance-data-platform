/**
 * The small part of markdown a model writes: headings, bullets, tables, bold and code.
 *
 * Built from React elements, never from innerHTML. The text comes from a language model, and a
 * model can be talked into writing a script tag.
 */

function inline(text, keyBase) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, index) => {
    const key = `${keyBase}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={key} className="font-semibold text-ink">{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={key} className="tabular rounded bg-white/10 px-1 text-[0.9em]">{part.slice(1, -1)}</code>;
    return part;
  });
}

const cells = (line) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());

export default function Markdown({ text }) {
  const lines = String(text ?? "").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const key = `b${index}`;

    if (!line.trim()) {
      index += 1;
    } else if (/^#{1,4}\s/.test(line)) {
      blocks.push(<h3 key={key} className="mt-3 text-sm font-semibold text-ink first:mt-0">{inline(line.replace(/^#+\s*/, ""), key)}</h3>);
      index += 1;
    } else if (/^\s*[-*]\s/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s/, ""));
        index += 1;
      }
      blocks.push(
        <ul key={key} className="ml-4 list-disc space-y-1 marker:text-ink-muted">
          {items.map((item, n) => <li key={n}>{inline(item, `${key}-${n}`)}</li>)}
        </ul>,
      );
    } else if (line.trim().startsWith("|")) {
      const rows = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        // The |---|---| line only separates the header. It carries no data.
        if (!/^\s*\|?\s*:?-{2,}/.test(lines[index])) rows.push(cells(lines[index]));
        index += 1;
      }
      const [head, ...body] = rows;
      blocks.push(
        <div key={key} className="overflow-x-auto">
          <table className="w-full whitespace-nowrap text-xs">
            <thead>
              <tr className="border-b border-white/10 text-left text-ink-muted">
                {head.map((cell, n) => <th key={n} className="px-2 py-1 font-medium">{inline(cell, `${key}-h${n}`)}</th>)}
              </tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r} className="border-b border-white/5 last:border-0">
                  {row.map((cell, n) => <td key={n} className="tabular px-2 py-1">{inline(cell, `${key}-${r}-${n}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else {
      const paragraph = [];
      while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|\s*[-*]\s|\s*\|)/.test(lines[index])) {
        paragraph.push(lines[index]);
        index += 1;
      }
      blocks.push(<p key={key}>{inline(paragraph.join(" "), key)}</p>);
    }
  }

  return <div className="space-y-2 text-sm leading-relaxed text-ink-2">{blocks}</div>;
}
