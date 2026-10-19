(() => {
  'use strict';

  const enhanced = new WeakSet();
  const icon = '<svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="password-visibility-slash" d="m3 3 18 18"/></svg>';

  function enhance(input) {
    // Django clones and renames empty formset templates before they become fields.
    if (enhanced.has(input) || input.name.includes('__prefix__')) return;
    enhanced.add(input);
    const wrapper = document.createElement('span');
    wrapper.className = 'password-visibility';
    input.before(wrapper);
    wrapper.append(input);
    input.classList.add('password-visibility-input');

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-visibility-toggle';
    button.setAttribute('aria-label', 'Показать пароль');
    button.setAttribute('aria-pressed', 'false');
    if (input.id) button.setAttribute('aria-controls', input.id);
    button.innerHTML = icon;
    button.addEventListener('click', () => {
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-label', visible ? 'Скрыть пароль' : 'Показать пароль');
      button.setAttribute('aria-pressed', String(visible));
    });
    wrapper.append(button);
  }

  function scan(root) {
    if (root.matches && root.matches('input[type="password"]')) enhance(root);
    root.querySelectorAll('input[type="password"]').forEach(enhance);
  }

  function init() {
    scan(document);
    // Include fields inserted by Django admin formsets and other project forms.
    new MutationObserver(records => {
      records.forEach(record => record.addedNodes.forEach(node => {
        if (node.nodeType === 1) scan(node);
      }));
    }).observe(document.body, {childList: true, subtree: true});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }
})();
