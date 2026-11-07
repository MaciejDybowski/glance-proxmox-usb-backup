(() => {
  const catalog = window.HOMELAB_LOCALE;
  const requested = new URLSearchParams(location.search).get('lang');
  let current = ['en', 'pl'].includes(requested) ? requested : catalog.language;
  if (!catalog.ui[current]) current = 'en';
  const interpolate = (text, values) => text.replace(/(?<!\{)\{([a-z_]+)\}(?!\})/g, (match, key) => String(values[key] ?? match));
  function t(key, values = {}) {
    return interpolate(catalog.ui[current][key] ?? catalog.ui.en[key] ?? key, values);
  }
  function error(text, depth = 0) {
    if (!text || depth > 3) return text;
    for (const entry of catalog.errors) {
      for (const source of ['pl', 'en']) {
        const keys = [];
        const template = entry[source];
        let pattern = '', position = 0;
        for (const token of template.matchAll(/(?<!\{)\{([a-z_]+)\}(?!\})/g)) {
          pattern += template.slice(position, token.index).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '([\\s\\S]+)';
          keys.push(token[1]);
          position = token.index + token[0].length;
        }
        pattern += template.slice(position).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const match = new RegExp(`^${pattern}$`).exec(text);
        if (match) return interpolate(entry[current], Object.fromEntries(keys.map((key, index) => [key, error(match[index + 1], depth + 1)])));
      }
    }
    return text;
  }
  function apply() {
    document.documentElement.lang = current;
    document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
    document.getElementById('language').value = current;
  }
  window.HOMELAB_I18N = Object.freeze({ t, error, apply,
    get language() { return current; },
    get locale() { return current === 'pl' ? 'pl-PL' : 'en-GB'; },
    setLanguage(value) {
      if (!catalog.ui[value]) return;
      current = value;
      apply();
      const url = new URL(location.href);
      url.searchParams.set('lang', value);
      history.replaceState(null, '', url);
      dispatchEvent(new Event('homelab-language-change'));
    }
  });
  apply();
  document.getElementById('language').addEventListener('change', event => window.HOMELAB_I18N.setLanguage(event.target.value));
})();
