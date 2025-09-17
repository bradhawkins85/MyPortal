import { URL } from 'url';

export interface NormalizedOpnformEmbed {
  sanitizedEmbedCode: string;
  formUrl: string;
}

export interface NormalizeOpnformEmbedOptions {
  allowedHost?: string | null;
}

const IFRAME_TAG_REGEX = /<iframe\b[^>]*>/i;
const SCRIPT_TAG_REGEX = /<script\b[^>]*>[\s\S]*?<\/script>/i;

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&');
}

function extractAttribute(tag: string, attribute: string): string | null {
  const attrRegex = new RegExp(
    `${attribute}\\s*=\\s*(\"([^\"]*)\"|'([^']*)'|([^\\s\"'>]+))`,
    'i'
  );
  const match = tag.match(attrRegex);
  if (!match) {
    return null;
  }
  return match[2] ?? match[3] ?? match[4] ?? null;
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/'/g, '&#39;');
}

function sanitizeStyle(value: string): string {
  const cleaned = value
    .replace(/\/\*.*?\*\//gs, '')
    .replace(/[^a-z0-9:;,%#\.\s\-()]/gi, '')
    .trim();
  return cleaned;
}

function normalizeIframeId(id: string | null): string | null {
  if (!id) {
    return null;
  }
  const trimmed = id.trim();
  if (!trimmed) {
    return null;
  }
  return /^[A-Za-z][A-Za-z0-9_:\-.]*$/.test(trimmed) ? trimmed : null;
}

export function normalizeOpnformEmbedCode(
  rawEmbedCode: string,
  options: NormalizeOpnformEmbedOptions = {}
): NormalizedOpnformEmbed {
  const trimmed = rawEmbedCode.trim();
  if (!trimmed) {
    throw new Error('OpnForm embed code is required');
  }

  const iframeTagMatch = trimmed.match(IFRAME_TAG_REGEX);
  if (!iframeTagMatch) {
    throw new Error('OpnForm embed code must include an iframe tag');
  }
  const iframeTag = iframeTagMatch[0];
  const rawSrc = extractAttribute(iframeTag, 'src');
  if (!rawSrc) {
    throw new Error('OpnForm iframe is missing a src attribute');
  }

  const decodedSrc = decodeHtmlEntities(rawSrc).trim();
  let formUrl: URL;
  try {
    formUrl = new URL(decodedSrc);
  } catch (err) {
    throw new Error('OpnForm iframe src must be an absolute URL');
  }

  if (!['http:', 'https:'].includes(formUrl.protocol)) {
    throw new Error('OpnForm iframe src must use HTTP or HTTPS');
  }

  const expectedHost = options.allowedHost ?? formUrl.host;
  if (options.allowedHost && formUrl.host !== expectedHost) {
    throw new Error('OpnForm iframe host is not allowed');
  }

  const iframeId = normalizeIframeId(extractAttribute(iframeTag, 'id'));
  let iframeStyle = extractAttribute(iframeTag, 'style');
  iframeStyle = iframeStyle ? sanitizeStyle(decodeHtmlEntities(iframeStyle)) : '';
  if (!iframeStyle) {
    iframeStyle = 'border:0;width:100%;min-height:480px;';
  }

  const attrs = [
    `src="${escapeHtmlAttribute(formUrl.toString())}"`,
    `style="${escapeHtmlAttribute(iframeStyle)}"`,
    'loading="lazy"',
    'allow="publickey-credentials-get *; publickey-credentials-create *"',
    'title="OpnForm form"',
  ];
  if (iframeId) {
    attrs.push(`id="${escapeHtmlAttribute(iframeId)}"`);
  }

  let scriptSnippet = '';
  const scriptTagMatch = trimmed.match(SCRIPT_TAG_REGEX);
  if (scriptTagMatch) {
    const scriptTag = scriptTagMatch[0];
    const rawScriptSrc = extractAttribute(scriptTag, 'src');
    if (!rawScriptSrc) {
      throw new Error('OpnForm script embed must include a src attribute');
    }
    let scriptUrl: URL;
    try {
      scriptUrl = new URL(decodeHtmlEntities(rawScriptSrc).trim(), formUrl);
    } catch (err) {
      throw new Error('OpnForm script src must resolve to an absolute URL');
    }
    if (!['http:', 'https:'].includes(scriptUrl.protocol)) {
      throw new Error('OpnForm script src must use HTTP or HTTPS');
    }
    if (scriptUrl.host !== expectedHost) {
      throw new Error('OpnForm script host must match the iframe host');
    }
    scriptSnippet = `\n<script src="${escapeHtmlAttribute(
      scriptUrl.toString()
    )}" async data-opnform-embed="support"></script>`;
  }

  const sanitizedEmbedCode = `<iframe ${attrs.join(' ')}></iframe>${scriptSnippet}`;

  return {
    sanitizedEmbedCode,
    formUrl: formUrl.toString(),
  };
}
