import Markdown, {type Components} from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

import type {ArtifactRef} from './types.js';

export type MarkdownArtifactLink = {
  ref: ArtifactRef;
  label: string;
  onOpen: () => void;
};

export type MarkdownResultLink = {
  keys: string[];
  label: string;
  summary?: string;
  kind: 'interactive_view' | 'report' | 'file' | 'table';
  onOpen: () => void;
};

const RESULT_MARKER_RE = /\[\[(?:result|output):([^\]|\r\n]+)(?:\|([^\]\r\n]+))?\]\]/g;
const LABELED_RESULT_ITEM_RE = /^\s*\[\[(?:result|output):([^\]\r\n]+)\]\]\s*[（(]([^）)\r\n]+)[）)]\s*[。.]*\s*$/;

/** Keep previously saved, dot-separated result references readable in the compact list layout. */
export function normalizeLabeledResultLists(content: string): string {
  return content.split(/\r?\n/).map((line) => {
    const parts = line.split(/\s*·\s*/);
    if (parts.length < 2) return line;
    const items = parts.map((part) => part.match(LABELED_RESULT_ITEM_RE));
    if (items.some((item) => item === null)) return line;
    return items.map((item) => {
      const key = item?.[1].trim() ?? '';
      const label = item?.[2].trim() ?? '';
      return `- [[result:${key}|${label}]]`;
    }).join('\n');
  }).join('\n');
}

export function referencedResultKeys(content: string): Set<string> {
  return new Set(Array.from(content.matchAll(RESULT_MARKER_RE), (match) => match[1].trim()));
}

export function safeExternalHref(value: string | undefined): string | null {
  if (!value) {
    return null;
  }

  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null;
  } catch {
    return null;
  }
}

const baseComponents: Components = {
  a: ({href, children}) => {
    const safeHref = safeExternalHref(href);
    return safeHref ? (
      <a href={safeHref} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    );
  },
  code: ({className, children}) => <code className={className}>{children}</code>,
  img: ({alt}) => <span className="markdown-image-placeholder">[Image: {alt || 'unavailable'}]</span>,
  pre: ({children}) => <pre className="markdown-code-block">{children}</pre>,
  table: ({children}) => (
    <div className="markdown-table">
      <table>{children}</table>
    </div>
  ),
};

function artifactKeys(ref: ArtifactRef): string[] {
  return [
    `${ref.artifact_id}@v${ref.version}`,
    `${ref.artifact_id}@v${String(ref.version).padStart(4, '0')}`,
  ];
}

export function MessageMarkdown({
  content,
  artifactLinks = [],
  resultLinks = [],
}: {
  content: string;
  artifactLinks?: MarkdownArtifactLink[];
  resultLinks?: MarkdownResultLink[];
}) {
  const links = new Map(artifactLinks.flatMap((item) => artifactKeys(item.ref).map((key) => [key, item] as const)));
  const results = new Map(resultLinks.flatMap((item) => item.keys.map((key) => [key, item] as const)));
  const markerTokens = new Map<string, {key: string; label?: string}>();
  let markerIndex = 0;
  const renderedContent = normalizeLabeledResultLists(content).replace(
    RESULT_MARKER_RE,
    (_match, key: string, label?: string) => {
      const token = `ocean-result-${markerIndex}`;
      markerIndex += 1;
      markerTokens.set(token, {key: key.trim(), label: label?.trim() || undefined});
      return `\`${token}\``;
    },
  );
  const components: Components = {
    ...baseComponents,
    code: ({className, children}) => {
      const value = String(children).trim();
      const marker = markerTokens.get(value);
      const result = marker ? results.get(marker.key) : undefined;
      if (result) {
        return (
          <button
            aria-label={`Open ${result.label}`}
            className="markdown-result-link"
            type="button"
            onClick={result.onOpen}
            title={result.summary ? `${result.label} — ${result.summary}` : result.label}
          >
            {marker?.label ?? result.label}
          </button>
        );
      }
      if (marker) {
        return <span className="markdown-result-unavailable" title="This supporting result is not available in the current task snapshot">{marker.label ?? 'Supporting result unavailable'}</span>;
      }
      const link = links.get(value);
      return link ? <button className="markdown-artifact-link" type="button" onClick={link.onOpen} title={`Open ${link.label} in the right workbench`}>{link.label}</button> : <code className={className}>{children}</code>;
    },
  };
  return (
    <div className="message-markdown">
      <Markdown
        components={components}
        rehypePlugins={[rehypeKatex]}
        remarkPlugins={[remarkGfm, remarkMath]}
        skipHtml
      >
        {renderedContent}
      </Markdown>
    </div>
  );
}
