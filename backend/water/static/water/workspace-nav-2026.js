/* Group links on the mobile section index keep their current location. */
(() => {
  const update = () => {
    const links = document.querySelectorAll('.ws-bottom-nav a');
    const selected = [...links].find((link) => link.hash && link.pathname === location.pathname && link.hash === location.hash);
    if (!selected) return;
    links.forEach((link) => link.removeAttribute('aria-current'));
    selected.setAttribute('aria-current', 'page');
  };
  window.addEventListener('hashchange', update);
  update();
})();
