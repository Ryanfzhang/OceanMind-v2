export class StrictJsonError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'StrictJsonError';
  }
}

const maxDepth = 128;

class Parser {
  private index = 0;

  constructor(private readonly source: string) {}

  parse(): unknown {
    this.whitespace();
    const value = this.value(0);
    this.whitespace();
    if (this.index !== this.source.length) this.fail('Unexpected trailing data.');
    return value;
  }

  private value(depth: number): unknown {
    if (depth > maxDepth) this.fail('JSON nesting exceeds the supported depth.');
    const character = this.source[this.index];
    if (character === '{') return this.object(depth + 1);
    if (character === '[') return this.array(depth + 1);
    if (character === '"') return this.string();
    if (character === 't') return this.literal('true', true);
    if (character === 'f') return this.literal('false', false);
    if (character === 'n') return this.literal('null', null);
    if (character === '-' || (character !== undefined && character >= '0' && character <= '9')) return this.number();
    this.fail('Expected a JSON value.');
  }

  private object(depth: number): Record<string, unknown> {
    this.expect('{');
    this.whitespace();
    const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    const keys = new Set<string>();
    if (this.source[this.index] === '}') {
      this.index += 1;
      return result;
    }
    while (true) {
      if (this.source[this.index] !== '"') this.fail('Object keys must be strings.');
      const key = this.string();
      if (keys.has(key)) this.fail(`Duplicate object key ${JSON.stringify(key)}.`);
      keys.add(key);
      this.whitespace();
      this.expect(':');
      this.whitespace();
      result[key] = this.value(depth);
      this.whitespace();
      const separator = this.source[this.index];
      if (separator === '}') {
        this.index += 1;
        return result;
      }
      if (separator !== ',') this.fail('Expected a comma or object terminator.');
      this.index += 1;
      this.whitespace();
    }
  }

  private array(depth: number): unknown[] {
    this.expect('[');
    this.whitespace();
    const result: unknown[] = [];
    if (this.source[this.index] === ']') {
      this.index += 1;
      return result;
    }
    while (true) {
      result.push(this.value(depth));
      this.whitespace();
      const separator = this.source[this.index];
      if (separator === ']') {
        this.index += 1;
        return result;
      }
      if (separator !== ',') this.fail('Expected a comma or array terminator.');
      this.index += 1;
      this.whitespace();
    }
  }

  private string(): string {
    this.expect('"');
    let result = '';
    while (this.index < this.source.length) {
      const character = this.source[this.index]!;
      this.index += 1;
      if (character === '"') return result;
      if (character === '\\') {
        const escape = this.source[this.index];
        this.index += 1;
        if (escape === '"' || escape === '\\' || escape === '/') result += escape;
        else if (escape === 'b') result += '\b';
        else if (escape === 'f') result += '\f';
        else if (escape === 'n') result += '\n';
        else if (escape === 'r') result += '\r';
        else if (escape === 't') result += '\t';
        else if (escape === 'u') result += this.unicodeEscape();
        else this.fail('Invalid string escape.');
        continue;
      }
      if (character.charCodeAt(0) < 0x20) this.fail('Control characters are not allowed in strings.');
      result += character;
    }
    this.fail('Unterminated string.');
  }

  private unicodeEscape(): string {
    const hex = this.source.slice(this.index, this.index + 4);
    if (!/^[0-9A-Fa-f]{4}$/.test(hex)) this.fail('Invalid Unicode escape.');
    this.index += 4;
    return String.fromCharCode(Number.parseInt(hex, 16));
  }

  private number(): number {
    const match = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
    match.lastIndex = this.index;
    const value = match.exec(this.source)?.[0];
    if (!value) this.fail('Invalid number.');
    this.index += value.length;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) this.fail('JSON number is outside the finite JavaScript range.');
    return parsed;
  }

  private literal<T>(literal: string, value: T): T {
    if (!this.source.startsWith(literal, this.index)) this.fail(`Expected ${literal}.`);
    this.index += literal.length;
    return value;
  }

  private whitespace(): void {
    while (this.source[this.index] === ' ' || this.source[this.index] === '\n' || this.source[this.index] === '\r' || this.source[this.index] === '\t') {
      this.index += 1;
    }
  }

  private expect(character: string): void {
    if (this.source[this.index] !== character) this.fail(`Expected ${character}.`);
    this.index += 1;
  }

  private fail(message: string): never {
    throw new StrictJsonError(`${message} At character ${this.index}.`);
  }
}

export function parseStrictJson(source: string): unknown {
  if (typeof source !== 'string') throw new StrictJsonError('JSON source must be a string.');
  return new Parser(source).parse();
}

export function parseStrictJsonBytes(source: Uint8Array): unknown {
  let decoded: string;
  try {
    decoded = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(source);
  } catch {
    throw new StrictJsonError('JSON source is not valid UTF-8.');
  }
  return parseStrictJson(decoded);
}
