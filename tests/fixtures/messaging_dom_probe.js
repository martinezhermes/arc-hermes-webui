const assert = require('node:assert/strict');
const roots = new Set();
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.events = {};
    this.attrs = {}; this.dataset = {}; this.className = ''; this.value = '';
    this.classList = {add: value => { this.className += ' ' + value; }};
  }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children.forEach(n => { n.parentNode = null; }); this.children = []; this._text = ''; this.append(...nodes); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get textContent() { return (this._text || '') + this.children.map(n => n.textContent).join(''); }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener(event, callback) { this.events[event] = callback; }
  get isConnected() { return roots.has(this) || !!(this.parentNode && this.parentNode.isConnected); }
  querySelectorAll(selector) {
    const direct = selector.startsWith(':scope > ');
    selector = selector.replace(':scope > ', '');
    const descendants = direct ? this.children : walk(this);
    return descendants.filter(node => selector.split(',').some(part => part.startsWith('.')
      ? node.className.split(' ').includes(part.slice(1)) : node.tagName.toLowerCase() === part));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function walk(node) { return node.children.flatMap(child => [child, ...walk(child)]); }
function button(root, label, last = false) {
  const matches = root.querySelectorAll('button').filter(node => node.textContent === label);
  assert(matches.length, 'Missing button: ' + label);
  return last ? matches.at(-1) : matches[0];
}
const containers = {connectionsContent: new Element('div'), arcConnectorsContent: new Element('div'), conversationsContent: new Element('div')};
Object.values(containers).forEach(node => roots.add(node));
global.document = {
  hidden: false, baseURI: 'http://synthetic.invalid/',
  getElementById: id => containers[id], createElement: tag => new Element(tag),
  createTextNode: text => { const node = new Element('text'); node.textContent = text; return node; },
};
const timers = new Map();
global.setTimeout = callback => { const id = timers.size + 1; timers.set(id, callback); return id; };
global.clearTimeout = id => timers.delete(id);
global.addEventListener = () => {};
global.confirm = () => true;
global.t = key => key;
global.S = {activeProfile: 'work'};
const connection = {configured:true, access:{principal:{
  id:'reader', enabled:true, administrator:false, operations:['groups.list','messages.recent'], rooms:['room'],
}}};
let heldDirectory, heldHistory, directoryResolve, historyResolve;
const directory = name => ({items:[{id:'room',name}],nextOffset:null});
const history = message => ({items:[{text:message,senderId:'synthetic',messageId:'message'}]});
const requests = [];
let gatewayEnabled = true;
let arcEnabled = true;
global.api = async (url, options) => {
  requests.push({url, options});
  if (url.includes('/api/messaging/connections?')) return {profile:'work', platforms:[
    {id:'whatsapp',name:'WhatsApp',enabled:gatewayEnabled,configured:true,state:gatewayEnabled?'connected':'disabled',env_vars:[]},
    {id:'slack',name:'Slack',enabled:false,configured:false,state:'disabled',env_vars:[]},
  ]};
  if (url.includes('/api/arc/connectors/whatsapp/configure?')) {
    arcEnabled = JSON.parse(options.body).enabled;
    return {ok:true};
  }
  if (url.includes('/api/arc/connectors?')) return {profile:'work', connectors:[{
    id:'arc-whatsapp', enabled:arcEnabled, port:9131, token_file_set:true,
    connection:arcEnabled?connection:{configured:false},
  }]};
  if (url.includes('/api/messaging/platforms/whatsapp/configure?')) {
    gatewayEnabled = JSON.parse(options.body).enabled;
    return {ok:true,platform:'whatsapp'};
  }
  if (url.includes('/connection?')) return connection;
  if (url.includes('/groups.list')) {
    if (heldDirectory) return new Promise(resolve => { directoryResolve = resolve; });
    return directory('Original group');
  }
  if (url.includes('/messages.recent')) {
    if (heldHistory) return new Promise(resolve => { historyResolve = resolve; });
    return history('Original history');
  }
  throw new Error('Unexpected synthetic request: ' + url);
};
require('../../static/messaging.js');
const turn = () => new Promise(resolve => setImmediate(resolve));
async function run() {
  await MessagingWorkspace.open('connections');
  let connections = containers.connectionsContent;
  assert(connections.textContent.includes('messaging_gateway_section'));
  assert(!connections.textContent.includes('ARC WhatsApp'));
  button(connections, 'messaging_disable_gateway').events.click();
  await turn();
  assert.equal(gatewayEnabled, false);
  assert(button(connections, 'messaging_enable_gateway'));
  assert(requests.some(item => item.url.includes('/api/messaging/platforms/whatsapp/configure?') &&
    JSON.parse(item.options.body).enabled === false));

  await MessagingWorkspace.open('arcConnectors');
  const arc = containers.arcConnectorsContent;
  assert(arc.textContent.includes('ARC WhatsApp'));
  assert(!arc.textContent.includes('WhatsApp Gateway'));
  button(arc, 'messaging_disable_arc').events.click();
  await turn();
  assert.equal(arcEnabled, false);
  assert(button(arc, 'messaging_enable_arc'));
  assert(requests.some(item => item.url.includes('/api/arc/connectors/whatsapp/configure?') &&
    JSON.parse(item.options.body).enabled === false));

  await MessagingWorkspace.open('conversations', {messagingSource:'groups'});
  await turn();
  const root = containers.conversationsContent;
  button(root, 'Original group').events.click();
  await turn();
  assert(root.textContent.includes('Original history'));

  // A directory refresh and a history refresh are independent observations.
  heldDirectory = true;
  button(root, 'messaging_refresh').events.click();
  heldHistory = true;
  button(root, 'messaging_refresh', true).events.click();
  assert(directoryResolve && historyResolve);
  historyResolve(history('New history'));
  await turn();
  directoryResolve(directory('Updated group'));
  await turn();
  assert(root.textContent.includes('New history'));
  assert(root.textContent.includes('Updated group'), 'History refresh must not strand directory loading');

  // A late response after leaving cannot restore private previous-profile DOM.
  button(root, 'messaging_refresh').events.click();
  S.activeProfile = 'personal';
  MessagingWorkspace.close();
  directoryResolve(directory('Previous profile data'));
  await turn();
  assert.equal(root.textContent, '');
  assert.equal(timers.size, 0);
  assert(requests.every(item => item.options.signal.aborted));
}
run().catch(error => { console.error(error); process.exitCode = 1; });
