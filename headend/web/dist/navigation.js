'use strict';
(() => {
  const pages = [
    ['/', 'Home'], ['/map', 'Map'], ['/home-assistant', 'Device inventory'],
    ['/queries', 'Information & sources'], ['/voices', 'Voices'], ['/cameras', 'Cameras'],
    ['/models', 'Model services'], ['/training', 'Training archive'], ['/bench', 'Audio bench'],
  ];
  const host = document.querySelector('[data-adam-navigation]');
  if (!host) return;
  const current = pages.find(([path]) => path === location.pathname)?.[1] || 'ADAM';
  const brand = document.createElement('a'); brand.href = '/'; brand.textContent = 'ADAM'; brand.className = 'adam-nav-brand';
  const label = document.createElement('span'); label.textContent = current; label.className = 'adam-nav-current';
  const menu = document.createElement('details'); menu.className = 'adam-nav-menu';
  const button = document.createElement('summary'); button.textContent = 'Menu'; button.setAttribute('aria-label', 'Menu · '+current);
  const links = document.createElement('div'); links.className = 'adam-nav-links';
  for (const [path, name] of pages) {
    const link = document.createElement('a'); link.href = path; link.textContent = name;
    if (path === location.pathname) link.setAttribute('aria-current', 'page');
    links.append(link);
  }
  menu.append(button, links); host.append(brand, label, menu);
  document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.open) { menu.open = false; button.focus(); } });
})();
