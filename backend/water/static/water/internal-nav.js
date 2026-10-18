(() => {
  const menu = document.querySelector('.internal-menu');
  if (!menu) return;
  const mobile = window.matchMedia('(max-width: 760px)');
  const update = () => { menu.open = !mobile.matches; };
  update();
  mobile.addEventListener('change', update);
  // Desktop navigation stays expanded even if details is toggled programmatically.
  menu.addEventListener('toggle', () => {
    if (!mobile.matches && !menu.open) menu.open = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && mobile.matches && menu.open) {
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
  document.addEventListener('click', (event) => {
    if (mobile.matches && menu.open && !menu.contains(event.target)) menu.open = false;
  });
})();
