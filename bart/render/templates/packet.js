/* bart packet interactive layer — vanilla, no deps. */

(function () {
  'use strict';

  // ─── Theme toggle ──────────────────────────────────────────────
  const THEME_KEY = 'bart.theme';
  function getStoredTheme() {
    try { return localStorage.getItem(THEME_KEY) || 'auto'; } catch (e) { return 'auto'; }
  }
  function setStoredTheme(value) {
    try { localStorage.setItem(THEME_KEY, value); } catch (e) {}
  }
  function applyTheme(value) {
    document.documentElement.setAttribute('data-theme', value);
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.textContent = value === 'dark' ? 'dark' : value === 'light' ? 'light' : 'auto';
    }
  }
  applyTheme(getStoredTheme());

  function nextTheme(curr) {
    const seq = ['auto', 'light', 'dark'];
    return seq[(seq.indexOf(curr) + 1) % seq.length];
  }

  document.addEventListener('click', function (e) {
    if (e.target && e.target.id === 'theme-toggle') {
      const next = nextTheme(getStoredTheme());
      setStoredTheme(next);
      applyTheme(next);
    }
  });

  // ─── Copy buttons on code blocks ───────────────────────────────
  document.querySelectorAll('article pre').forEach(function (pre) {
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'copy';
    btn.addEventListener('click', function () {
      const code = pre.querySelector('code');
      const text = code ? code.innerText : pre.innerText;
      navigator.clipboard.writeText(text).then(
        function () { btn.textContent = 'copied'; setTimeout(function () { btn.textContent = 'copy'; }, 1400); },
        function () { btn.textContent = 'failed'; }
      );
    });
    pre.appendChild(btn);
  });

  // ─── TOC scrollspy ─────────────────────────────────────────────
  const tocLinks = Array.from(document.querySelectorAll('.sidebar nav a[href^="#"]'));
  if (tocLinks.length) {
    const idMap = new Map();
    tocLinks.forEach(function (link) {
      const id = decodeURIComponent(link.getAttribute('href').slice(1));
      const target = document.getElementById(id);
      if (target) idMap.set(target, link);
    });

    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        const link = idMap.get(entry.target);
        if (!link) return;
        if (entry.isIntersecting) {
          tocLinks.forEach(function (l) { l.classList.remove('active'); });
          link.classList.add('active');
        }
      });
    }, { rootMargin: '-10% 0% -70% 0%', threshold: 0 });

    idMap.forEach(function (_, el) { observer.observe(el); });
  }

  // ─── Search (Cmd-K / Ctrl-K) ───────────────────────────────────
  const searchOverlay = document.getElementById('search-overlay');
  const searchInput = document.getElementById('search-input');
  const searchResults = document.getElementById('search-results');
  let searchIndex = null;
  let activeResultIndex = 0;

  function openSearch() {
    if (!searchOverlay) return;
    searchOverlay.classList.add('open');
    if (searchInput) { searchInput.value = ''; searchInput.focus(); }
    renderSearchResults('');
  }

  function closeSearch() {
    if (!searchOverlay) return;
    searchOverlay.classList.remove('open');
  }

  function loadSearchIndex() {
    if (searchIndex) return Promise.resolve(searchIndex);
    const url = (document.documentElement.getAttribute('data-rel-root') || '.') + '/search-index.json';
    return fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      searchIndex = data;
      return data;
    }).catch(function () { searchIndex = []; return []; });
  }

  function renderSearchResults(query) {
    if (!searchResults) return;
    const q = query.trim().toLowerCase();
    loadSearchIndex().then(function (idx) {
      const items = idx || [];
      let matches;
      if (!q) {
        matches = items.slice(0, 12);
      } else {
        matches = items.filter(function (it) {
          return (it.title || '').toLowerCase().includes(q) ||
                 (it.source || '').toLowerCase().includes(q);
        }).slice(0, 24);
      }
      searchResults.innerHTML = '';
      activeResultIndex = 0;
      if (!matches.length) {
        searchResults.innerHTML = '<li class="search-empty">no matches</li>';
        return;
      }
      matches.forEach(function (m, i) {
        const li = document.createElement('li');
        if (i === 0) li.classList.add('active');
        const a = document.createElement('a');
        a.href = m.url + (m.anchor ? ('#' + m.anchor) : '');
        a.innerHTML = '<div class="result-title">' + escapeHtml(m.title) + '</div>' +
                      '<div class="result-source">' + escapeHtml(m.source) + '</div>';
        li.appendChild(a);
        searchResults.appendChild(li);
      });
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }

  if (searchInput) {
    searchInput.addEventListener('input', function (e) {
      renderSearchResults(e.target.value);
    });
    searchInput.addEventListener('keydown', function (e) {
      const items = Array.from(searchResults.querySelectorAll('li'));
      if (!items.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        items[activeResultIndex].classList.remove('active');
        activeResultIndex = (activeResultIndex + 1) % items.length;
        items[activeResultIndex].classList.add('active');
        items[activeResultIndex].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        items[activeResultIndex].classList.remove('active');
        activeResultIndex = (activeResultIndex - 1 + items.length) % items.length;
        items[activeResultIndex].classList.add('active');
        items[activeResultIndex].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const link = items[activeResultIndex].querySelector('a');
        if (link) link.click();
      } else if (e.key === 'Escape') {
        closeSearch();
      }
    });
  }

  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      openSearch();
    } else if (e.key === 'Escape' && searchOverlay && searchOverlay.classList.contains('open')) {
      closeSearch();
    }
  });

  if (searchOverlay) {
    searchOverlay.addEventListener('click', function (e) {
      if (e.target === searchOverlay) closeSearch();
    });
  }

  const searchBtn = document.getElementById('search-btn');
  if (searchBtn) searchBtn.addEventListener('click', openSearch);

  // ─── Sidebar toggle (mobile) ───────────────────────────────────
  const sidebar = document.querySelector('.sidebar');
  const sidebarBtn = document.getElementById('sidebar-toggle');
  if (sidebar && sidebarBtn) {
    sidebarBtn.addEventListener('click', function () { sidebar.classList.toggle('open'); });
  }
})();
