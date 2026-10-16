/* Advisory display only. All authoritative validation remains on the server. */
(() => {
  const update = (output, raw, previous, previousDate, readingDate) => {
    output.classList.remove('is-warning');
    if (!raw) { output.textContent = 'Введите показание, чтобы проверить расход.'; return; }
    if (previous == null || previous === '') { output.textContent = 'Начальное показание — расход пока неизвестен.'; return; }
    if (!readingDate || !previousDate || readingDate <= previousDate) {
      output.textContent = 'Для этой даты расход проверьте по журналу.';
      return;
    }
    const value = Number(raw.replace(',', '.'));
    const before = Number(previous);
    if (!Number.isFinite(value) || !Number.isFinite(before)) { output.textContent = ''; return; }
    const delta = Math.round(value * 1000) - Math.round(before * 1000);
    output.classList.toggle('is-warning', delta < 0);
    output.textContent = delta < 0
      ? 'Показание меньше предыдущего. Проверьте значение и дату.'
      : `Расход ${(delta / 1000).toLocaleString('ru-RU', {maximumFractionDigits: 3})} м³ с ${previousDate.split('-').reverse().join('.')}.`;
  };
  window.TrudReadingPreview = {update};
  const initialize = () => document.querySelectorAll('[data-reading-preview]').forEach((input) => {
    const output = document.getElementById(input.getAttribute('aria-describedby'));
    if (!output) return;
    const render = () => update(output, input.value, input.dataset.previous, input.dataset.previousDate, input.dataset.readingDate);
    input.addEventListener('input', render);
    render();
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
