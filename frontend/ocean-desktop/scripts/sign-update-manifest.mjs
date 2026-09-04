import {createHash, createPrivateKey, sign} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {copyFile, lstat, mkdtemp, readFile, rm, unlink, writeFile, constants as fsConstants} from 'node:fs/promises';
import {basename, dirname, resolve} from 'node:path';
import {tmpdir} from 'node:os';

const schemaVersion = 'ocean-desktop-update/v1';

function usage() {
  return 'Usage: node scripts/sign-update-manifest.mjs --input RELEASE.json --private-key ED25519-PRIVATE-KEY.pem --output MANIFEST.json';
}

function argumentsFrom(argv) {
  const names = new Set(['--input', '--private-key', '--output']);
  const values = {};
  for (let index = 2; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!names.has(name) || !value || value.startsWith('--') || values[name]) throw new Error(usage());
    values[name] = value;
  }
  if (Object.keys(values).length !== names.size) throw new Error(usage());
  return {input: values['--input'], privateKey: values['--private-key'], output: values['--output']};
}

function object(value, path) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${path} must be an object.`);
  return value;
}

function exactKeys(value, keys, path) {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${path} has unsupported or missing fields.`);
  }
}

function string(value, path) {
  if (typeof value !== 'string' || !value) throw new Error(`${path} must be a non-empty string.`);
  return value;
}

function integer(value, path) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) throw new Error(`${path} must be a safe integer.`);
  return value;
}

function canonicalJson(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('Signed payload contains a non-finite number.');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  const item = object(value, 'signed payload');
  return `{${Object.keys(item).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`).join(',')}}`;
}

async function archiveDigest(path) {
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error(`release artifact must be a regular non-symlink file: ${path}`);
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path)) digest.update(chunk);
  return {sha256: digest.digest('hex'), sizeBytes: info.size};
}

async function parseReleaseInput(value, inputDirectory) {
  const envelope = object(value, 'release input');
  exactKeys(envelope, ['key_id', 'release', 'schema_version'], 'release input');
  if (envelope.schema_version !== schemaVersion) throw new Error('release input schema_version is unsupported.');
  const keyId = string(envelope.key_id, 'release input.key_id');
  const release = object(envelope.release, 'release');
  exactKeys(release, ['backend_schema', 'packages', 'protocol_version', 'version'], 'release');
  const version = string(release.version, 'release.version');
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.test(version)) {
    throw new Error('release.version must be a semantic version.');
  }
  const protocolVersion = integer(release.protocol_version, 'release.protocol_version');
  if (protocolVersion < 1) throw new Error('release.protocol_version must be positive.');
  const backendSchema = string(release.backend_schema, 'release.backend_schema');
  if (!Array.isArray(release.packages) || !release.packages.length) throw new Error('release.packages must be a non-empty array.');
  const packages = await Promise.all(release.packages.map(async (value, index) => {
    const item = object(value, `release.packages[${index}]`);
    exactKeys(item, ['architecture', 'artifact_path', 'platform', 'url'], `release.packages[${index}]`);
    const platform = string(item.platform, `release.packages[${index}].platform`);
    const architecture = string(item.architecture, `release.packages[${index}].architecture`);
    if (!['darwin', 'win32'].includes(platform) || !['arm64', 'x64'].includes(architecture)) {
      throw new Error(`release.packages[${index}] has an unsupported target.`);
    }
    const url = string(item.url, `release.packages[${index}].url`);
    let parsedUrl;
    try {
      parsedUrl = new URL(url);
    } catch {
      throw new Error(`release.packages[${index}].url is invalid.`);
    }
    if (parsedUrl.protocol !== 'https:' || parsedUrl.username || parsedUrl.password || parsedUrl.hash) {
      throw new Error(`release.packages[${index}].url must be an HTTPS URL without credentials or a fragment.`);
    }
    const artifactPath = resolve(inputDirectory, string(item.artifact_path, `release.packages[${index}].artifact_path`));
    const {sha256, sizeBytes} = await archiveDigest(artifactPath);
    if (sizeBytes <= 0) throw new Error(`release.packages[${index}].artifact_path must not be empty.`);
    return {platform, architecture, url, sha256, size_bytes: sizeBytes};
  }));
  return {
    schema_version: schemaVersion,
    key_id: keyId,
    release: {version, protocol_version: protocolVersion, backend_schema: backendSchema, packages},
  };
}

async function writeNewFileAtomically(output, contents) {
  const destination = resolve(output);
  const temporaryDirectory = await mkdtemp(resolve(dirname(destination), `.${basename(destination)}.tmp-`));
  const temporary = resolve(temporaryDirectory, basename(destination));
  try {
    await writeFile(temporary, contents, {encoding: 'utf8', mode: 0o644, flag: 'wx'});
    await copyFile(temporary, destination, fsConstants.COPYFILE_EXCL);
  } finally {
    await unlink(temporary).catch(() => undefined);
    await rm(temporaryDirectory, {recursive: true, force: true});
  }
}

async function main() {
  const paths = argumentsFrom(process.argv);
  const inputPath = resolve(paths.input);
  const rawInput = await readFile(inputPath, 'utf8');
  const payload = await parseReleaseInput(JSON.parse(rawInput), dirname(inputPath));
  const privateKey = createPrivateKey(await readFile(resolve(paths.privateKey), 'utf8'));
  if (privateKey.asymmetricKeyType !== 'ed25519') throw new Error('private key must be an Ed25519 key.');
  const signature = sign(null, Buffer.from(canonicalJson(payload), 'utf8'), privateKey).toString('base64');
  await writeNewFileAtomically(paths.output, JSON.stringify({...payload, signature}, null, 2) + '\n');
  process.stdout.write(`${resolve(paths.output)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
