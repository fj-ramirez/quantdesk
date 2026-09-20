/**
 * `/terminal/brief` — the daily brief as a page (T80).
 *
 * It was printed to stdout by a CLI command; it is a document, and documents belong on screens.
 *
 * **The count of unanswered questions is shown at the top, prominently.** The brief asks five
 * standing questions of the data and answers as many as it can; how many it could *not* answer
 * is the most useful single fact about it, because it says how much of the picture is missing
 * today. Burying that inside the prose — which is where stdout put it — makes an incomplete
 * brief look complete.
 *
 * Rendered from markdown with a deliberately small renderer rather than a dependency: the brief
 * emits headings, paragraphs, tables, blockquotes and lists, and that is all. Pulling in a full
 * markdown stack (and a sanitiser, since it would then be rendering HTML) to cover constructs
 * this document never produces would be more surface area than the feature is worth.
 */
import { useMemo } from 'react';
import { PageHeader } from '../../../components/ui/PageHeader';
import { Surface } from '../../../components/ui/Surface';
import { ErrorState } from '../../gex/components/ErrorState';
import { useBrief } from '../api/queries';
import { useAsOf } from '../state/asOf';

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'quote'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'rule' };

function splitRow(line: string): string[] {
  return line
    .split('|')
    .slice(1, -1)
    .map((cell) => cell.trim());
}

/** Markdown -> blocks. Only the constructs `brief.py` actually emits. */
function parse(markdown: string): Block[] {
  const lines = markdown.split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
    } else if (line.startsWith('---')) {
      blocks.push({ kind: 'rule' });
      i += 1;
    } else if (line.startsWith('#')) {
      const level = line.match(/^#+/)?.[0].length ?? 1;
      blocks.push({ kind: 'heading', level, text: line.replace(/^#+\s*/, '') });
      i += 1;
    } else if (line.startsWith('>')) {
      const text: string[] = [];
      while (i < lines.length && lines[i].startsWith('>')) {
        text.push(lines[i].replace(/^>\s?/, ''));
        i += 1;
      }
      blocks.push({ kind: 'quote', text: text.join(' ') });
    } else if (line.trimStart().startsWith('|')) {
      const header = splitRow(line.trim());
      i += 1;
      // The separator row (|---|---:|) carries alignment we do not use.
      if (i < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) i += 1;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trimStart().startsWith('|')) {
        rows.push(splitRow(lines[i].trim()));
        i += 1;
      }
      blocks.push({ kind: 'table', header, rows });
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i += 1;
      }
      blocks.push({ kind: 'list', items });
    } else {
      const text: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !lines[i].startsWith('#') &&
        !lines[i].startsWith('>') &&
        !lines[i].trimStart().startsWith('|') &&
        !lines[i].startsWith('---') &&
        !/^\s*[-*]\s+/.test(lines[i])
      ) {
        text.push(lines[i]);
        i += 1;
      }
      blocks.push({ kind: 'paragraph', text: text.join(' ') });
    }
  }
  return blocks;
}

/** `**bold**` and `` `code` `` — the only inline markup the brief uses. Never raw HTML. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={index}>{part.slice(1, -1)}</code>;
        }
        return <span key={index}>{part}</span>;
      })}
    </>
  );
}

function Rendered({ blocks }: { blocks: Block[] }) {
  return (
    <div className="brief-doc">
      {blocks.map((block, index) => {
        switch (block.kind) {
          case 'heading': {
            const Tag = (`h${Math.min(block.level + 1, 6)}`) as 'h2';
            return (
              <Tag key={index}>
                <Inline text={block.text} />
              </Tag>
            );
          }
          case 'quote':
            return (
              <blockquote key={index}>
                <Inline text={block.text} />
              </blockquote>
            );
          case 'list':
            return (
              <ul key={index}>
                {block.items.map((item, j) => (
                  <li key={j}>
                    <Inline text={item} />
                  </li>
                ))}
              </ul>
            );
          case 'table':
            return (
              <div className="terminal-table__scroll" key={index}>
                <table className="terminal-table">
                  <thead>
                    <tr>
                      {block.header.map((cell, j) => (
                        <th scope="col" key={j}>
                          <Inline text={cell} />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, j) => (
                      <tr key={j}>
                        {row.map((cell, k) => (
                          <td key={k}>
                            <Inline text={cell} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case 'rule':
            return <hr key={index} />;
          default:
            return (
              <p key={index}>
                <Inline text={block.text} />
              </p>
            );
        }
      })}
    </div>
  );
}

export function Brief() {
  const { asOf } = useAsOf();
  const brief = useBrief(asOf);
  const blocks = useMemo(() => (brief.data ? parse(brief.data.markdown) : []), [brief.data]);

  return (
    <div className="terminal-page">
      <PageHeader
        title="Cross-asset brief"
        description="The five standing questions, answered from stored observations at the selected as-of."
      />

      {brief.isError ? (
        <ErrorState message="Could not load the brief." />
      ) : brief.isPending ? (
        <p className="terminal-loading">Generating…</p>
      ) : (
        <>
          <Surface
            className={`brief-banner${brief.data.unanswered > 0 ? ' brief-banner--gaps' : ''}`}
            level="subtle"
            bordered
          >
            {brief.data.unanswered === 0 ? (
              <p>All five standing questions could be answered from stored data.</p>
            ) : (
              <p>
                <strong>
                  {brief.data.unanswered} of the five standing questions could not be answered
                </strong>{' '}
                from what is stored at this as-of. The brief says which, and why, where the answer
                would have been.
              </p>
            )}
          </Surface>
          <Rendered blocks={blocks} />
        </>
      )}
    </div>
  );
}
