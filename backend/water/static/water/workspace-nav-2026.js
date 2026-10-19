/* Group links on the mobile section index keep their current location. */
(() => {
  const mobile = window.matchMedia('(max-width: 760px)');
  const groups = document.querySelectorAll('.hint-menu-group');
  const expandGroup = () => {
    groups.forEach(group => { group.open = !mobile.matches || !location.hash || `#${group.id}` === location.hash; });
  };
  mobile.addEventListener('change', expandGroup);
  window.addEventListener('hashchange', expandGroup);
  expandGroup();
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
