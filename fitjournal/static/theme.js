(() => {
  const root = document.documentElement;
  try {
    root.dataset.theme = localStorage.getItem('fitjournal-theme') === 'light' ? 'light' : 'dark';
  } catch {
    root.dataset.theme = 'dark';
  }

  document.addEventListener('DOMContentLoaded', () => {
    const menu = document.querySelector('.menu-toggle');
    const nav = document.querySelector('#main-nav');
    if (menu && nav) {
      const mobile = window.matchMedia('(max-width: 700px)');
      const resetMenu = () => {
        menu.hidden = !mobile.matches;
        nav.hidden = mobile.matches;
        menu.setAttribute('aria-expanded', 'false');
      };
      resetMenu();
      mobile.addEventListener('change', resetMenu);
      menu.addEventListener('click', () => {
        nav.hidden = !nav.hidden;
        menu.setAttribute('aria-expanded', String(!nav.hidden));
      });
      nav.addEventListener('keydown', event => {
        if (event.key === 'Escape' && mobile.matches) {
          resetMenu();
          menu.focus();
        }
      });
    }
    const toggle = document.querySelector('[data-theme-toggle]');
    if (!toggle) return;
    const update = () => {
      const light = root.dataset.theme === 'light';
      toggle.setAttribute('aria-pressed', String(light));
      toggle.querySelector('[data-theme-label]').textContent = light ? 'Светлая' : 'Тёмная';
    };
    toggle.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
      try {
        localStorage.setItem('fitjournal-theme', root.dataset.theme);
      } catch {
        // The toggle still works when browser storage is unavailable.
      }
      update();
    });
    update();
  });
})();
