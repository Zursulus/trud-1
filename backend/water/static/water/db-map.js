(() => {
  'use strict';
  const root = document.getElementById('db-map');
  if (!root) return;
  const data = JSON.parse(document.getElementById('db-map-data').textContent);
  const svg = root.querySelector('svg');
  const details = root.querySelector('aside');
  const ns = 'http://www.w3.org/2000/svg';
  const storageKey = 'trud-db-map-layout-v1';
  const width = 240, height = 76;
  const positions = {}, defaults = {}, visible = new Set(data.sections.map(s => s.id));
  visible.delete('service');
  let scale = 0.85, pan = {x: 12, y: 12}, selected = null, gesture = null;
  let serviceOrigin = {x: 24, y: 24};
  const byId = new Map(data.nodes.map(n => [n.id, n]));
  const colors = new Map(data.sections.map(s => [s.id, s.color]));
  const element = (tag, attrs = {}, text) => {
    const item = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, value));
    if (text !== undefined) item.textContent = text;
    return item;
  };
  let top = 24;
  data.sections.forEach(section => {
    const nodes = data.nodes.filter(n => n.section === section.id);
    if (section.id === 'service') serviceOrigin = {x: 24, y: top};
    nodes.forEach((node, i) => {
      defaults[node.id] = {x: 24 + (i % 3) * 280, y: top + Math.floor(i / 3) * 116};
      positions[node.id] = {...defaults[node.id]};
    });
    top += Math.max(1, Math.ceil(nodes.length / 3)) * 116 + 70;
  });
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved && typeof saved === 'object') Object.keys(positions).forEach(id => {
      const point = saved[id];
      if (point && Number.isFinite(point.x) && Number.isFinite(point.y) &&
          Math.abs(point.x) < 100000 && Math.abs(point.y) < 100000) positions[id] = {x: point.x, y: point.y};
    });
  } catch (_) { /* Storage is optional. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(positions)); } catch (_) { /* optional */ } };
  const defs = element('defs');
  const marker = element('marker', {id: 'db-map-arrow', viewBox: '0 0 10 10', refX: 10, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'});
  marker.append(element('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#687b89'}));
  defs.append(marker);
  svg.append(defs);
  const world = element('g');
  svg.append(world);
  function transform() {
    world.setAttribute('transform', `translate(${pan.x} ${pan.y}) scale(${scale})`);
    root.querySelector('output').textContent = `${Math.round(scale * 100)}%`;
  }
  function showDetails(node) {
    selected = node.id;
    details.replaceChildren();
    const heading = document.createElement('h2');
    heading.textContent = `${node.verbose_name} · ${node.id}`;
    const list = document.createElement('ul');
    node.fields.forEach(field => {
      const li = document.createElement('li');
      li.textContent = `${field.verbose_name} (${field.name}) — ${field.type}; ${field.required ? 'обязательное' : 'необязательное'}${field.primary_key ? '; первичный ключ' : ''}`;
      list.append(li);
    });
    const links = document.createElement('ul');
    data.relations.filter(r => r.source === node.id || r.target === node.id).forEach(r => {
      const li = document.createElement('li');
      li.textContent = `${r.source}.${r.field} → ${r.target}: ${r.label}`;
      links.append(li);
    });
    details.append(heading, list, document.createTextNode('Связи (поле → таблица):'), links);
    draw();
  }
  function draw() {
    world.replaceChildren();
    const nodes = data.nodes.filter(n => visible.has(n.section));
    const ids = new Set(nodes.map(n => n.id));
    data.relations.forEach(r => {
      if (!ids.has(r.source) || !ids.has(r.target)) return;
      const a = positions[r.source], b = positions[r.target];
      const x1 = a.x + width, y1 = a.y + height / 2, x2 = b.x, y2 = b.y + height / 2;
      const bend = r.source === r.target ? 100 : Math.max(50, Math.abs(x2 - x1) / 2);
      const path = element('path', {class: 'db-map-link', d: `M${x1},${y1} C${x1 + bend},${y1 - (r.source === r.target ? 110 : 0)} ${x2 - bend},${y2} ${x2},${y2}`, 'marker-end': 'url(#db-map-arrow)'});
      path.append(element('title', {}, `${r.source}.${r.field} → ${r.target}: ${r.label}`));
      world.append(path);
      if (selected === r.source || selected === r.target) {
        world.append(element('text', {x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 7, class: 'db-map-link-label', 'text-anchor': 'middle'}, r.label));
      }
    });
    data.sections.forEach(section => {
      const first = nodes.find(n => n.section === section.id);
      if (first) world.append(element('text', {x: defaults[first.id].x, y: defaults[first.id].y - 12, fill: section.color, 'font-size': 18}, section.label));
    });
    nodes.forEach(node => {
      const point = positions[node.id];
      const group = element('g', {class: 'db-map-node', transform: `translate(${point.x} ${point.y})`, tabindex: 0, role: 'button', 'aria-label': `${node.verbose_name}: показать поля`, 'data-node': node.id});
      group.append(element('rect', {width, height, rx: 8, fill: '#fff', stroke: colors.get(node.section), 'stroke-width': selected === node.id ? 4 : 2}));
      group.append(element('rect', {width: 7, height: height - 16, x: 6, y: 8, fill: colors.get(node.section)}));
      const label = node.verbose_name.length > 27 ? `${node.verbose_name.slice(0, 26)}…` : node.verbose_name;
      group.append(element('text', {x: 20, y: 28}, label));
      group.append(element('text', {x: 20, y: 53, class: 'db-map-model'}, node.id.length > 30 ? `${node.id.slice(0, 29)}…` : node.id));
      group.append(element('title', {}, `${node.verbose_name} (${node.id})`));
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); showDetails(node); }
      });
      world.append(group);
    });
    if (!visible.has('service')) {
      const group = element('g', {class: 'db-map-node', transform: `translate(${serviceOrigin.x} ${serviceOrigin.y})`, role: 'button', tabindex: 0, 'aria-label': 'Развернуть служебные таблицы', 'data-service': 'true'});
      group.append(element('rect', {width, height, fill: '#e2e5e8', stroke: '#707780', rx: 8}));
      group.append(element('text', {x: 12, y: 28}, 'Служебное · развернуть'));
      group.append(element('text', {x: 12, y: 53}, `${data.nodes.filter(n => n.section === 'service').length} таблиц · только структура`));
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); expandService(); }
      });
      world.append(group);
    }
    transform();
  }
  const checks = new Map();
  data.sections.forEach(section => {
    const label = document.createElement('label');
    label.style.setProperty('--section-color', section.color);
    const input = document.createElement('input');
    input.type = 'checkbox'; input.checked = visible.has(section.id);
    input.addEventListener('change', () => {
      if (input.checked) visible.add(section.id); else visible.delete(section.id);
      focusSection(input.checked ? section.id : data.nodes.find(n => visible.has(n.section))?.section);
      draw();
    });
    checks.set(section.id, input);
    label.append(input, document.createTextNode(section.label));
    root.querySelector('.db-map-filters').append(label);
  });
  function focusSection(id) {
    const nodes = data.nodes.filter(n => n.section === id);
    if (!nodes.length) return;
    pan = {x: 20 - Math.min(...nodes.map(n => positions[n.id].x)) * scale,
      y: 40 - Math.min(...nodes.map(n => positions[n.id].y)) * scale};
  }
  function expandService() {
    visible.add('service'); checks.get('service').checked = true; focusSection('service'); draw();
  }
  function zoom(factor, x = svg.clientWidth / 2, y = svg.clientHeight / 2) {
    const next = Math.min(2.5, Math.max(0.12, scale * factor));
    pan = {x: x - (x - pan.x) * next / scale, y: y - (y - pan.y) * next / scale};
    scale = next; transform();
  }
  svg.addEventListener('wheel', event => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    zoom(Math.exp(-event.deltaY * 0.001), event.clientX - rect.left, event.clientY - rect.top);
  }, {passive: false});
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0 || gesture) return;
    const target = event.target.closest('[data-node], [data-service]');
    gesture = {pointer: event.pointerId, x: event.clientX, y: event.clientY, id: target?.dataset.node,
      service: Boolean(target?.dataset.service), moved: false};
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener('pointermove', event => {
    if (!gesture || gesture.pointer !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.moved && Math.hypot(dx, dy) < 4) return;
    gesture.moved = true;
    if (gesture.id) {
      positions[gesture.id].x += dx / scale; positions[gesture.id].y += dy / scale; draw();
    } else { pan.x += dx; pan.y += dy; transform(); }
    gesture.x = event.clientX; gesture.y = event.clientY;
  });
  function finish(event, cancelled = false) {
    if (!gesture || gesture.pointer !== event.pointerId) return;
    const done = gesture; gesture = null;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    if (done.moved) save();
    else if (!cancelled && done.id) showDetails(byId.get(done.id));
    else if (!cancelled && done.service) expandService();
  }
  svg.addEventListener('pointerup', event => finish(event));
  svg.addEventListener('pointercancel', event => finish(event, true));
  svg.addEventListener('lostpointercapture', event => finish(event, true));
  root.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.action === 'reset') {
      Object.keys(defaults).forEach(id => { positions[id] = {...defaults[id]}; });
      scale = 0.85; pan = {x: 12, y: 12}; save(); draw();
    } else zoom(button.dataset.action === 'in' ? 1.25 : 0.8);
  }));
  svg.addEventListener('keydown', event => {
    if (event.target !== svg) return;
    const steps = {ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40]};
    if (steps[event.key]) { event.preventDefault(); pan.x += steps[event.key][0]; pan.y += steps[event.key][1]; transform(); }
  });
  draw();
})();
