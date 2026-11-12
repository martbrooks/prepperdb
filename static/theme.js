/* Apply the saved preference before styles paint. Storage is optional. */
(() => {
  'use strict';
  let theme = 'dark';
  try { if (localStorage.getItem('prepperdb-theme') === 'light') theme = 'light'; } catch {}
  document.documentElement.dataset.theme = theme;
  document.addEventListener('DOMContentLoaded', () => {
    const button = document.querySelector('#theme-toggle');
    const render = () => {
      const label = document.documentElement.dataset.theme === 'dark' ? 'Use light theme' : 'Use dark theme';
      button.querySelector('[data-theme-icon="light"]').hidden = document.documentElement.dataset.theme !== 'dark';
      button.querySelector('[data-theme-icon="dark"]').hidden = document.documentElement.dataset.theme === 'dark';
      button.title = label;
      button.setAttribute('aria-label', label);
    };
    button.hidden = false;
    render();
    button.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('prepperdb-theme', next); } catch {}
      render();
    });
  });
})();
