'use strict';
const query = document.querySelector('#catalogue-query');
if (query) {
  let timer, controller;
  const list = document.querySelector('#catalogue-results');
  const status = document.querySelector('#catalogue-status');
  query.closest('form').addEventListener('submit', event => event.preventDefault());
  const searchLocal = () => {
    clearTimeout(timer);
    controller?.abort();
    list.replaceChildren();
    status.textContent = '';
    if (!query.value.trim()) return;
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch('/catalogue/search/?q=' + encodeURIComponent(query.value), {signal: controller.signal});
        if (!response.ok || response.redirected) throw new Error('Search unavailable');
        const data = await response.json();
        list.replaceChildren();
        for (const product of data.products) {
          const li = document.createElement('li');
          const a = document.createElement('a');
          a.href = '/add/?product=' + product.id;
          a.textContent = `${product.name} · ${product.size} ${product.packaging} · ${product.quantity} in stock`;
          li.append(a); list.append(li);
        }
        status.textContent = data.products.length ? 'Choose a saved product below.' : 'No saved match. Continue manually below.';
      } catch (error) {
        if (error.name !== 'AbortError') status.textContent = 'Search unavailable. Continue manually below.';
      }
    }, 250);
  };
  query.addEventListener('input', searchLocal);
  // A user or browser autofill may populate the field before this deferred file loads.
  if (query.value.trim()) searchLocal();
}
const entry = document.querySelector('[data-stock-entry]');
if (entry) {
  const update = () => {
    const count = Number(entry.elements.quantity.value) * Number(entry.elements.items_per_pack.value);
    document.querySelector('#pack-summary').textContent = `This adds ${Number.isFinite(count) ? count : 0} individual items.`;
  };
  entry.addEventListener('input', update);
  update();
}

// Month-only best-before dates remain month-only; day choices match the month.
for (const picker of document.querySelectorAll('[data-date-picker]')) {
  const [day, month, year] = picker.querySelectorAll('select');
  const status = picker.nextElementSibling;
  const updateDays = () => {
    day.setCustomValidity(''); status.textContent = '';
    const days = month.value && year.value ? new Date(Number(year.value), Number(month.value), 0).getDate() : 31;
    for (const option of day.options) option.disabled = Boolean(option.value && Number(option.value) > days);
    if (Number(day.value) > days) {
      day.setCustomValidity('Choose a valid day for this month, or explicitly choose Month only.');
      status.textContent = 'This day is not in the selected month. Choose a valid day, or choose Month only for best before.';
    }
  };
  month.addEventListener('change', updateDays); year.addEventListener('change', updateDays);
  day.addEventListener('change', () => { day.setCustomValidity(''); status.textContent = ''; });
  updateDays();
}

// A nested disclosure list keeps ordinary buttons/links and works without a CDN.
for (const categoryExplorer of document.querySelectorAll('[data-category-explorer]')) {
  const storageKey = categoryExplorer.dataset.treeStorage || 'prepperdb-category-collapsed';
  const transient = categoryExplorer.hasAttribute('data-tree-transient');
  const toggles = [...categoryExplorer.querySelectorAll('[data-category-toggle]')];
  const controls = new Map(toggles.map(button => [button.getAttribute('aria-controls'), button]));
  let closed = new Set();
  try {
    const saved = JSON.parse((transient ? null : localStorage.getItem(storageKey)) || '[]');
    if (Array.isArray(saved)) closed = new Set(saved.filter(id => controls.has(id)));
  } catch { /* The complete tree remains usable when storage is unavailable. */ }
  const persist = () => {
    if (!transient) { try { localStorage.setItem(storageKey, JSON.stringify([...closed])); } catch {} }
  };
  const setOpen = (button, open) => {
    const id = button.getAttribute('aria-controls');
    document.getElementById(id).hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    if (open) closed.delete(id); else closed.add(id);
  };
  for (const button of toggles) {
    setOpen(button, !closed.has(button.getAttribute('aria-controls')));
    button.hidden = false;
    button.parentElement.querySelector('[data-tree-fallback]').hidden = true;
    button.addEventListener('click', () => {
      setOpen(button, button.getAttribute('aria-expanded') !== 'true');
      persist();
    });
    button.addEventListener('keydown', event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        setOpen(button, event.key === 'ArrowRight');
        persist();
      }
    });
  }
  for (const [selector, open] of [['[data-tree-expand]', true], ['[data-tree-collapse]', false]]) {
    const button = categoryExplorer.querySelector(selector);
    button.hidden = toggles.length === 0;
    button.addEventListener('click', () => {
      toggles.forEach(toggle => setOpen(toggle, open));
      persist();
    });
  }
  persist();
}

// Buttons change only the form value; direct entry and server validation remain.
for (const input of document.querySelectorAll('input[type="number"]')) {
  const group = document.querySelector('#quantity-stepper').content.firstElementChild.cloneNode(true);
  const label = input.labels?.[0]?.textContent.trim().replace(/:$/, '') || 'quantity';
  input.before(group);
  group.insertBefore(input, group.lastElementChild);
  for (const button of group.querySelectorAll('button')) {
    const up = button.dataset.step === '1';
    button.setAttribute('aria-label', `${up ? 'Increase' : 'Decrease'} ${label}`);
    button.title = button.getAttribute('aria-label');
    button.setAttribute('aria-controls', input.id);
    button.addEventListener('click', () => {
      up ? input.stepUp() : input.stepDown();
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
    });
  }
}

// Last-item consumption needs a deliberate confirmation, including on product details.
document.addEventListener('submit', event => {
  const form = event.target.closest('form[data-last-item]');
  if (!form) return;
  if (!window.confirm(`Use the last item of ${form.dataset.lastItem}? This will leave none in stock. You can undo this in History.`)) {
    event.preventDefault();
    return;
  }
  let confirmed = form.elements.confirm_last;
  if (!confirmed) {
    confirmed = document.createElement('input');
    confirmed.type = 'hidden'; confirmed.name = 'confirm_last'; form.append(confirmed);
  }
  confirmed.value = '1';
});

// Keep quick stock actions in place. Forms remain usable without JavaScript.
let stockPending = false;
document.addEventListener('submit', async event => {
  const form = event.target.closest('.stock-actions form');
  if (!form || event.defaultPrevented) return;
  event.preventDefault();
  if (stockPending) return;
  stockPending = true;
  const button = event.submitter;
  const row = form.closest('.stock-item');
  const productId = row.dataset.productId;
  const buttonName = button.getAttribute('aria-label');
  const scroll = window.scrollY;
  button.disabled = true;
  try {
    const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
    if (!response.ok || !response.redirected || new URL(response.url).pathname !== new URL(form.elements.return_to.value, location.origin).pathname) {
      throw new Error('Stock could not be updated. Reload to check the quantity before trying again.');
    }
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const fresh = doc.querySelector(`[data-product-id="${productId}"]`);
    if (fresh) {
      row.replaceWith(fresh);
      const focus = [...fresh.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === buttonName);
      (focus || fresh.querySelector('.stock-actions a'))?.focus({preventScroll: true});
    } else {
      const next = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      for (const branch of [...document.querySelectorAll('.stock-branch')].reverse()) {
        if (!branch.querySelector('.stock-item')) branch.remove();
      }
      const focus = next?.querySelector('button, a') || document.querySelector('#filter') || document.querySelector('h1');
      if (focus.tagName === 'H1') focus.tabIndex = -1;
      focus.focus({preventScroll: true});
      if (!document.querySelector('.stock-item')) {
        document.querySelector('.stock-browser').replaceWith(doc.querySelector('.empty'));
      }
    }
    window.scrollTo(0, scroll);
  } catch (error) {
    let status = row.querySelector('[data-stock-error]');
    if (!status) {
      status = document.createElement('p'); status.dataset.stockError = '';
      status.className = 'errorlist'; status.setAttribute('role', 'alert'); row.append(status);
    }
    status.textContent = error.message;
  } finally {
    button.disabled = false;
    stockPending = false;
  }
});

// Removal counts are bounded by this batch; stocktakes may correct the total upwards.
for (const amount of document.querySelectorAll('[data-available-stock]')) {
  const form = amount.closest('form');
  const action = form.elements.action;
  const available = Number(amount.dataset.availableStock);
  const adjustmentLimit = Number(amount.max);
  const help = document.getElementById(amount.id + '_helptext');
  const update = () => {
    const setting = action.value === 'set';
    const empty = !setting && available === 0;
    amount.min = setting || empty ? '0' : '1';
    amount.max = String(setting ? adjustmentLimit : Math.min(available, adjustmentLimit));
    const value = amount.valueAsNumber;
    const group = amount.closest('.quantity-stepper');
    group.querySelector('[data-step="-1"]').disabled = empty || (Number.isFinite(value) && value <= Number(amount.min));
    group.querySelector('[data-step="1"]').disabled = empty || (Number.isFinite(value) && value >= Number(amount.max));
    form.querySelector('[type=submit]').disabled = empty;
    if (help) help.textContent = setting
      ? 'Enter the corrected total, from 0 to 1,000,000. This may be higher than the current count.'
      : empty ? 'This batch is empty. Choose Set quantity to record a correction.'
      : `Enter 1 to ${amount.max} items from this batch.`;
  };
  action.addEventListener('change', update);
  amount.addEventListener('input', update);
  update();
}
