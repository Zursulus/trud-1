/* Text-only preview; publication still uses the validated server form. */
(() => {
  document.querySelectorAll('[data-preview]').forEach((preview) => {
    const input = document.getElementById(`id_${preview.dataset.preview}`);
    if (!input) return;
    const update = () => { preview.textContent = input.value; preview.hidden = !input.value; };
    input.addEventListener('input', update);
    update();
  });
})();
