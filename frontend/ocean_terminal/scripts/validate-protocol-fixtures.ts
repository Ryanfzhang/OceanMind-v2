import {readFile, readdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {dirname, join, resolve} from 'node:path';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, '../../..');
const fixtureRoot = join(repositoryRoot, 'protocol', 'v2', 'fixtures');
const schemaRoot = join(repositoryRoot, 'protocol', 'v2', 'schema');
const parseJson = async (path: string): Promise<Record<string, unknown>> => JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;

const ajv = new Ajv({strict: false});
addFormats(ajv);
const requestValidator = ajv.compile(await parseJson(join(schemaRoot, 'request-envelope.json')));
const eventValidator = ajv.compile(await parseJson(join(schemaRoot, 'event-envelope.json')));
let validCount = 0;
let invalidCount = 0;

for (const filename of await readdir(join(fixtureRoot, 'valid'))) {
	if (!filename.endsWith('.json')) {
		continue;
	}
	const fixture = await parseJson(join(fixtureRoot, 'valid', filename));
	const validator = 'event_id' in fixture ? eventValidator : requestValidator;
	if (!validator(fixture)) {
		throw new Error(`Expected valid fixture ${filename}: ${ajv.errorsText(validator.errors)}`);
	}
	validCount++;
}

for (const filename of await readdir(join(fixtureRoot, 'invalid'))) {
	if (!filename.endsWith('.json')) {
		continue;
	}
	const fixture = await parseJson(join(fixtureRoot, 'invalid', filename));
	if (requestValidator(fixture)) {
		throw new Error(`Expected invalid request fixture ${filename} to be rejected`);
	}
	invalidCount++;
}

console.log(JSON.stringify({validCount, invalidCount}));
