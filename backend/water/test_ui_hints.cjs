/* DOM-level interaction checks; run with node, without a browser or packages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag, attrs = {}, text = '') {
    this.tag = tag;
    this.attrs = {...attrs};
    this.children = [];
    this.events = {};
    this.nodeType = 1;
    this._text = text;
    this.hidden = false;
    this.style = {};
    this.dataset = new Proxy({}, {
      get: (_, key) => this.getAttribute(`data-${String(key).replace(/[A-Z]/g, s => `-${s.toLowerCase()}`)}`) ?? undefined,
      set: (_, key, value) => { this.setAttribute(`data-${String(key).replace(/[A-Z]/g, s => `-${s.toLowerCase()}`)}`, value); return true; },
    });
    this.classList = {
      add: name => { this.className = [...new Set([...this.className.split(' ').filter(Boolean), name])].join(' '); },
      remove: name => { this.className = this.className.split(' ').filter(item => item !== name).join(' '); },
      contains: name => this.className.split(' ').includes(name),
    };
  }
  get className() { return this.getAttribute('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get id() { return this.getAttribute('id'); }
  set id(value) { this.setAttribute('id', value); }
  get title() { return this.getAttribute('title'); }
  set title(value) { this.setAttribute('title', value); }
  get type() { return this.getAttribute('type'); }
  set type(value) { this.setAttribute('type', value); }
  get value() { return this.getAttribute('value') || ''; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = value; this.children = []; }
  get isConnected() { return this.tag === 'document' || Boolean(this.parent && this.parent.isConnected); }
  get form() { return this.closest('form'); }
  get method() { return this.getAttribute('method') || 'get'; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return Object.hasOwn(this.attrs, key) ? this.attrs[key] : null; }
  hasAttribute(key) { return Object.hasOwn(this.attrs, key); }
  removeAttribute(key) { delete this.attrs[key]; }
  detach() {
    if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  append(...nodes) {
    nodes.forEach(node => { node.detach(); node.parent = this; this.children.push(node); });
  }
  after(...nodes) {
    let current = this;
    nodes.forEach(node => {
      node.detach();
      const parent = current.parent;
      parent.children.splice(parent.children.indexOf(current) + 1, 0, node);
      node.parent = parent;
      current = node;
    });
  }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      const tag = part.match(/^[a-z]+/i);
      if (tag && tag[0] !== this.tag) return false;
      const classes = [...part.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
      if (!classes.every(name => this.classList.contains(name))) return false;
      const attrs = [...part.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)];
      return attrs.every(([, key, value]) => value === undefined ? this.hasAttribute(key) : this.getAttribute(key) === value);
    });
  }
  closest(selector) { return this.matches(selector) ? this : (this.parent && this.parent.closest(selector)); }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(event, callback) { (this.events[event] ||= []).push(callback); }
  fire(event, extra = {}) {
    const payload = {target: this, ...extra};
    (this.events[event] || []).forEach(callback => callback(payload));
    if (event === 'click' && this.parent) this.parent.fire(event, payload);
  }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  focus() { this.fire('focus'); }
  getBoundingClientRect() {
    return this.box || {left: 20, top: 200, right: 240, bottom: 240, width: 220, height: 40};
  }
}

function fixture({mobile = false, storageThrows = false, stored = {}} = {}) {
  const document = new Element('document');
  document.body = new Element('body');
  document.append(document.body);
  document.documentElement = {clientWidth: mobile ? 390 : 1200};
  document.createElement = tag => new Element(tag);
  const form = new Element('form', {method: 'post', action: '/synthetic-action/'});
  const csrf = new Element('input', {type: 'hidden', name: 'csrfmiddlewaretoken', value: 'synthetic-csrf'});
  const save = new Element('button', {type: 'submit', 'aria-describedby': 'existing-help'}, 'Сохранить ник');
  const ordinary = new Element('button', {type: 'submit'}, 'Найти');
  const danger = new Element('button', {type: 'submit', name: 'action', value: 'reverse'}, 'Отменить подтверждение');
  const confirm = new Element('button', {type: 'submit', name: 'decision', value: 'confirm'}, 'Подтвердить');
  const password = new Element('button', {type: 'button', 'aria-label': 'Показать пароль'});
  const link = new Element('a', {href: '/synthetic/', 'data-hint': 'Открыть выбранную карточку участка'}, 'Участок');
  const publishLink = new Element('a', {href: '/synthetic/edit/'}, 'Опубликовать');
  const summary = new Element('summary', {}, 'Передать показание');
  const main = new Element('main');
  const help = new Element('aside', {'data-help-key': 'test-home'});
  form.append(csrf, save, ordinary, danger, confirm, password);
  main.append(help, form, link, publishLink, summary);
  document.body.append(main);
  const media = {matches: mobile};
  const window = {innerHeight: 844, matchMedia: query => query.includes('760') ? media : {matches: true}, events: {},
    addEventListener(event, callback) { (this.events[event] ||= []).push(callback); }};
  let observer;
  const localStorage = {
    getItem(key) { if (storageThrows) throw Error('storage unavailable'); return stored[key]; },
    setItem(key, value) { if (storageThrows) throw Error('storage unavailable'); stored[key] = value; },
  };
  class MutationObserver { constructor(callback) { observer = callback; } observe() {} }
  vm.runInNewContext(fs.readFileSync('backend/water/static/water/ui-hints.js', 'utf8'),
    {document, window, MutationObserver, localStorage});
  const close = help.querySelector('.page-help-close');
  return {document, form, csrf, save, ordinary, danger, confirm, password, link, publishLink, summary, help, close, stored,
    observer, bubble: document.querySelector('.hint-bubble')};
}

const desktop = fixture();
assert.match(desktop.save.dataset.hint, /ник увидят/);
assert.match(desktop.danger.dataset.hint, /Внимание.*оплаты/);
assert.match(desktop.confirm.dataset.hint, /сверку показания/);
assert.match(desktop.publishLink.dataset.hint, /Открыть форму/);
assert.match(desktop.summary.dataset.hint, /Раскрыть или свернуть/);
assert.equal(desktop.link.dataset.hint, 'Открыть выбранную карточку участка');
assert.match(desktop.save.getAttribute('aria-describedby'), /^existing-help ui-hint-/);
assert.equal(desktop.form.getAttribute('action'), '/synthetic-action/');
assert.equal(desktop.form.method, 'post');
assert.equal(desktop.csrf.value, 'synthetic-csrf');
assert.equal(desktop.save.type, 'submit');
assert.ok(desktop.document.querySelectorAll('.hint-toggle').every(button => button.type === 'button'));
assert.equal(desktop.ordinary.parent.children[desktop.ordinary.parent.children.indexOf(desktop.ordinary) + 1], desktop.danger,
  'ordinary actions get no extra help button');
desktop.save.fire('focus');
assert.equal(desktop.bubble.hidden, false);
assert.equal(desktop.bubble.textContent, desktop.save.dataset.hint);
desktop.save.fire('blur');
assert.equal(desktop.bubble.hidden, true);
desktop.save.fire('mouseenter');
assert.equal(desktop.bubble.hidden, false);
desktop.save.fire('mouseleave');
assert.equal(desktop.bubble.hidden, true);
// Keep floating desktop tooltips inside the viewport near its right edge.
desktop.save.box = {left: 1180, top: 2, right: 1200, bottom: 30, width: 20, height: 28};
desktop.save.fire('focus');
assert.ok(parseFloat(desktop.bubble.style.left) + desktop.bubble.getBoundingClientRect().width <= 1192);
assert.ok(parseFloat(desktop.bubble.style.top) >= desktop.save.box.bottom);
desktop.password.setAttribute('aria-label', 'Скрыть пароль');
desktop.observer([{type: 'attributes', target: desktop.password}]);
assert.match(desktop.password.dataset.hint, /скрыты точками/);
const dynamic = new Element('button', {type: 'submit'}, 'Удалить');
desktop.form.append(dynamic);
desktop.observer([{type: 'childList', addedNodes: [dynamic]}]);
assert.match(dynamic.dataset.hint, /Внимание.*удалены/);
assert.ok(dynamic.getAttribute('aria-describedby'));
const count = desktop.document.querySelectorAll('.hint-toggle').length;
desktop.observer([{type: 'childList', addedNodes: [dynamic]}]);
assert.equal(desktop.document.querySelectorAll('.hint-toggle').length, count, 'rescanning does not duplicate controls');

const phone = fixture({mobile: true});
phone.save.fire('focus');
assert.equal(phone.bubble.hidden, true, 'phone uses inline help instead of covering actions');
const toggle = phone.form.children[phone.form.children.indexOf(phone.save) + 1];
const description = phone.form.children[phone.form.children.indexOf(toggle) + 1];
assert.equal(toggle.getAttribute('aria-label'), 'Подсказка');
assert.equal(toggle.getAttribute('aria-controls'), description.id);
assert.equal(description.hidden, true);
toggle.fire('click');
assert.equal(toggle.getAttribute('aria-expanded'), 'true');
assert.equal(description.hidden, false);
assert.equal(description.classList.contains('hint-inline-note'), true);
toggle.fire('click');
assert.equal(description.hidden, true);
toggle.fire('click');
phone.ordinary.fire('click');
assert.equal(description.hidden, true, 'outside tap closes inline help without blocking its action');
toggle.fire('click');
phone.document.fire('keydown', {key: 'Escape'});
assert.equal(description.hidden, true);
phone.close.fire('click');
assert.equal(phone.help.hidden, true);
assert.equal(phone.stored['trud:help:v1:test-home'], 'hidden');
assert.equal(fixture({stored: phone.stored}).help.hidden, true, 'dismissal survives another page visit');
const blocked = fixture({storageThrows: true});
assert.equal(blocked.help.hidden, false);
blocked.close.fire('click');
assert.equal(blocked.help.hidden, true, 'storage errors do not break help or forms');
console.log('OK: action consequences, ARIA, focus/hover, touch toggle/outside/Escape, persistence/storage failure, dynamic controls, password toggle, unchanged forms/CSRF.');
