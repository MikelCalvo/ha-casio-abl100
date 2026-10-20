/* Execute pinned upstream setConfig methods, not a re-invented schema.
 * Optional review gate: node check_frontend.cjs SOURCE_ROOT DASHBOARD_JSON
 * Requires TypeScript (TYPESCRIPT_PATH may point to its module). No network.
 * This is source-extracted structural/action composition, NOT browser rendering.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require(process.env.TYPESCRIPT_PATH || 'typescript');
const [sourceRoot, dashboardPath] = process.argv.slice(2);
assert(sourceRoot && dashboardPath, 'Supply pinned frontend source root and dashboard JSON');
const crypto = require('node:crypto');
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'frontend-manifest.json'), 'utf8'));
for (const [relative, expected] of Object.entries(manifest.files)) {
  const actual = crypto.createHash('sha256').update(fs.readFileSync(path.join(sourceRoot, relative))).digest('hex');
  assert.equal(actual, expected, `Pinned upstream source changed: ${relative}`);
}
const cache = new Map();
global.window = {};
function load(relative) {
  const filename = path.resolve(sourceRoot, relative.endsWith('.ts') ? relative : relative + '.ts');
  if (cache.has(filename)) return cache.get(filename).exports;
  const module = { exports: {} };
  cache.set(filename, module);
  const source = fs.readFileSync(filename, 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS
  }}).outputText;
  const localRequire = name => {
    assert(name.startsWith('.'), `Unexpected nonrelative dependency: ${name}`);
    return load(path.relative(sourceRoot, path.resolve(path.dirname(filename), name)));
  };
  vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename })(localRequire, module, module.exports);
  return module.exports;
}
function sourceFile(relative) {
  return ts.createSourceFile(relative, fs.readFileSync(path.join(sourceRoot, relative), 'utf8'),
                             ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
}
function declaration(relative, name) {
  const sf = sourceFile(relative);
  const statement = sf.statements.find(n => ts.isVariableStatement(n) &&
    n.declarationList.declarations.some(d => d.name.getText(sf) === name));
  assert(statement, `Upstream declaration missing: ${name}`);
  return statement.getText(sf).replace(/^export /, '');
}
function methods(relative, names) {
  const sf = sourceFile(relative);
  const cls = sf.statements.find(ts.isClassDeclaration);
  assert(cls);
  return names.map(name => {
    const method = cls.members.find(n => ts.isMethodDeclaration(n) && n.name.getText(sf) === name);
    assert(method, `Upstream method missing: ${name}`);
    return method.getText(sf);
  }).join('\n');
}
const common = 'src/panels/lovelace/common/';
const cards = 'src/panels/lovelace/cards/';
const context = vm.createContext({
  ...load(common + 'process-config-entities.ts'),
  ...load(cards + 'migrate-card-config.ts'),
  ...load('src/common/entity/valid_entity_id.ts'),
  ...load('src/common/entity/compute_domain.ts'),
  ...load('src/common/const.ts'),
  DEFAULT_HOURS_TO_SHOW: 24,
});
function evaluate(source) {
  const compiled = ts.transpileModule(source, {compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None
  }}).outputText;
  return vm.runInContext(compiled, context);
}
evaluate(declaration(cards + 'hui-entities-card.ts', 'computeShowHeaderToggle'));
evaluate(declaration(cards + 'hui-button-card.ts', 'getEntityDefaultButtonAction'));
const constructors = {
  entities: evaluate(`(class {${methods(cards + 'hui-entities-card.ts', ['setConfig'])}})`),
  button: evaluate(`(class {${methods(cards + 'hui-button-card.ts', ['setConfig'])}})`),
  'history-graph': evaluate(`(class {${methods(cards + 'hui-history-graph-card.ts', ['setConfig', '_computeNames', '_computeColors'])}})`),
  markdown: evaluate(`(class { _tryDisconnect() {} ${methods(cards + 'hui-markdown-card.ts', ['setConfig'])}})`),
};
const Attribute = evaluate(`(class {${methods('src/panels/lovelace/special-rows/hui-attribute-row.ts', ['setConfig'])}})`);
const dashboard = JSON.parse(fs.readFileSync(dashboardPath, 'utf8'));
let cardsChecked = 0, attrsChecked = 0, tapsChecked = 0;
const {toggleEntity} = load(common + 'entity/toggle-entity.ts');
function checkCard(card) {
    if (['vertical-stack', 'horizontal-stack'].includes(card.type)) {
      assert(Array.isArray(card.cards) && card.cards.length > 0);
      card.cards.forEach(checkCard);
      return;
    }
    if (card.type === 'conditional') {
      assert(Array.isArray(card.conditions) && card.conditions.length > 0);
      for (const condition of card.conditions) {
        assert.equal(condition.condition, 'state');
        assert.match(condition.entity, /^(switch|binary_sensor)\.[a-z0-9_]+$/);
        assert(condition.state !== undefined || condition.state_not !== undefined);
      }
      checkCard(card.card);
      return;
    }
    assert(constructors[card.type], 'Unexpected card type');
    const instance = new constructors[card.type]();
    instance.setConfig(card);
    cardsChecked++;
    if (card.type === 'entities') {
      assert.equal(instance._showHeaderToggle, false);
      for (const row of card.entities) {
        if (row.type === 'attribute') { new Attribute().setConfig(row); attrsChecked++; }
      }
    }
    if (card.type === 'button') {
      assert.equal(instance._config.tap_action.action, 'toggle');
      const calls = [];
      const hass = {states: {[card.entity]: {state: 'unknown'}},
                    callService: (...args) => calls.push(args)};
      const domain = card.entity.split('.')[0];
      for (const state of domain === 'switch' ? ['off', 'on'] : ['unknown']) {
        calls.length = 0;
        hass.states[card.entity].state = state;
        // Actual upstream toggle -> turnOnOffEntity -> getToggleAction.
        toggleEntity(hass, card.entity);
        const service = domain === 'button' ? 'press' : state === 'on' ? 'turn_off' : 'turn_on';
        assert.deepEqual(calls, [[domain, service, {entity_id: card.entity}]]);
        tapsChecked++;
      }
    }
}
for (const view of dashboard.views) {
  assert.equal(view.type, 'masonry');
  view.cards.forEach(checkCard);
}
// Causal negative controls prove real upstream setters reject invalid shapes.
assert.throws(() => new constructors.entities().setConfig({type: 'entities'}), /Entities must/);
assert.throws(() => new constructors['history-graph']().setConfig({entities: []}), /at least one/);
assert.throws(() => new constructors.button().setConfig({entity: '{{bad}}'}), /Invalid entity/);
assert.throws(() => new Attribute().setConfig({entity: 'sensor.test'}), /Attribute not/);
assert.throws(() => new constructors.markdown().setConfig({content: ''}), /Content required/);
console.log(JSON.stringify({cardsChecked, attrsChecked, tapsChecked,
  frontend: '20260826.7', commit: '380e9b5a81ada29a1d187b4123c54fb3d6fbcc89',
  kind: 'upstream source-extracted structural/action composition; NOT visual E2E'}));
