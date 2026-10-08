/**
 * Harness for .github/workflows/issue_validator.yml, ported from ha_washdata.
 *
 * GitHub Actions cannot run locally, so the caller (which has a YAML parser)
 * extracts the job scripts from the workflow and this runs them against a
 * stubbed `github`/`context`. It runs the SHIPPED script text, not a copy, so
 * the tests fail when the workflow drifts.
 *
 * Usage: node issue_validator_harness.mjs <spec.json>
 *   spec: { restoreScript, validateScript, templatelessScript?,
 *           scenarios: [{ name, body, labels, releases, minHa }] }
 *   out : JSON array of { name, restore: {...}, validate: {...},
 *         templateless?: { closed, comments } } on stdout
 */
import fs from 'node:fs';

const AsyncFn = Object.getPrototypeOf(async function () {}).constructor;

function makeStub({ body, labels, releases, minHa }) {
  const state = { labels: [...labels], comments: [], addedLabels: [], removedLabels: [], logs: [] };
  const issue = {
    number: 1, body, state: 'open',
    user: { login: 'reporter', type: 'User' },
    get labels() { return state.labels.map(n => ({ name: n })); },
  };
  const hacs = JSON.stringify({ name: 'x', homeassistant: minHa });
  const github = {
    rest: {
      issues: {
        get: async () => ({ data: { ...issue, body, labels: state.labels.map(n => ({ name: n })) } }),
        addLabels: async ({ labels: ls }) => { state.addedLabels.push(...ls); state.labels.push(...ls); },
        removeLabel: async ({ name }) => { state.removedLabels.push(name); state.labels = state.labels.filter(l => l !== name); },
        createComment: async ({ body: b }) => { state.comments.push(b); },
        update: async ({ state: s }) => { state.closed = s === 'closed'; },
        updateComment: async ({ body: b }) => { state.comments.push(b); },
        listComments: async () => ({ data: state.comments.map((b, i) => ({ user: { type: 'Bot' }, body: b, id: i })) }),
      },
      repos: {
        listReleases: async () => ({
          data: releases.map(r => ({ tag_name: r.tag, draft: false, prerelease: Boolean(r.prerelease) })),
        }),
        getContent: async ({ path }) => {
          if (path !== 'hacs.json') throw new Error(`unexpected path ${path}`);
          return { data: { content: Buffer.from(hacs).toString('base64') } };
        },
      },
    },
    paginate: async () => [],
  };
  const context = { eventName: 'issues', repo: { owner: 'o', repo: 'r' }, payload: { issue } };
  return { state, github, context };
}

async function run(src, stub) {
  const orig = console.log;
  console.log = (...a) => stub.state.logs.push(a.map(String).join(' '));
  try {
    await new AsyncFn('github', 'context', 'core', src)(stub.github, stub.context, {});
  } finally {
    console.log = orig;
  }
}

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
for (const sc of spec.scenarios) {
  let templateless;
  if (spec.templatelessScript) {
    const stub = makeStub(sc);
    await run(spec.templatelessScript, stub);
    templateless = { closed: Boolean(stub.state.closed), comments: stub.state.comments };
  }
  const restore = makeStub(sc);
  await run(spec.restoreScript, restore);
  // The validate job re-reads the issue, so it sees whatever restore applied.
  const validate = makeStub({ ...sc, labels: restore.state.labels });
  await run(spec.validateScript, validate);
  out.push({
    name: sc.name,
    templateless,
    restore: { addedLabels: restore.state.addedLabels, logs: restore.state.logs },
    validate: {
      addedLabels: validate.state.addedLabels,
      removedLabels: validate.state.removedLabels,
      comments: validate.state.comments,
      logs: validate.state.logs,
    },
  });
}
process.stdout.write(JSON.stringify(out, null, 2));
