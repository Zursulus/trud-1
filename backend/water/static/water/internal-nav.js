(() => {
  const menu = document.querySelector('.internal-menu');
  if (!menu) return;
  const mobile = window.matchMedia('(max-width: 760px)');
  const update = () => { menu.open = !mobile.matches; };
  update();
  mobile.addEventListener('change', update);
})();
