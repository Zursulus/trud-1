(() => {
  const composer = document.querySelector('[data-appeal-composer]');
  if (!composer) return;

  composer.classList.add('is-enhanced');
  const textarea = composer.querySelector('textarea');
  const fileInput = composer.querySelector('input[type="file"]');
  const fileSummary = composer.querySelector('[data-file-summary]');
  const fileName = composer.querySelector('[data-file-name]');
  const fileMeta = composer.querySelector('[data-file-meta]');
  const submit = composer.querySelector('button[type="submit"]');

  const closePanels = (except = null) => {
    composer.querySelectorAll('[data-composer-panel]').forEach((panel) => {
      if (panel !== except) panel.hidden = true;
    });
  };

  composer.querySelectorAll('[data-panel-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const panel = composer.querySelector(`[data-composer-panel="${button.dataset.panelTarget}"]`);
      if (!panel) return;
      const opening = panel.hidden;
      closePanels(opening ? panel : null);
      panel.hidden = !opening;
      button.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
  });

  composer.querySelector('[data-file-trigger]')?.addEventListener('click', () => fileInput?.click());

  const humanSize = (bytes) => {
    if (!Number.isFinite(bytes)) return '';
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
  };

  const refreshFile = () => {
    const file = fileInput?.files?.[0];
    if (!file || !fileSummary) {
      if (fileSummary) fileSummary.hidden = true;
      return;
    }
    fileSummary.hidden = false;
    if (fileName) fileName.textContent = file.name;
    if (fileMeta) fileMeta.textContent = humanSize(file.size);
  };
  fileInput?.addEventListener('change', refreshFile);
  refreshFile();

  composer.querySelector('[data-file-remove]')?.addEventListener('click', () => {
    if (fileInput) fileInput.value = '';
    refreshFile();
  });

  const insertText = (text) => {
    if (!textarea || !text) return;
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(end);
    const spacer = before && !/\s$/.test(before) && !/^\s/.test(text) ? ' ' : '';
    textarea.value = `${before}${spacer}${text}${after}`;
    const cursor = before.length + spacer.length + text.length;
    textarea.focus();
    textarea.setSelectionRange(cursor, cursor);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  };

  composer.querySelectorAll('[data-insert-text]').forEach((button) => {
    button.addEventListener('click', () => {
      insertText(button.dataset.insertText || '');
      closePanels();
    });
  });

  if (textarea) {
    const resize = () => {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, 260)}px`;
    };
    textarea.addEventListener('input', resize);
    resize();
  }

  composer.addEventListener('submit', (event) => {
    if (!composer.checkValidity()) return;
    if (submit?.disabled) {
      event.preventDefault();
      return;
    }
    if (submit) {
      submit.disabled = true;
      submit.textContent = 'Отправляем…';
    }
  });
})();
