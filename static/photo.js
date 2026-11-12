'use strict';
const editor = document.querySelector('[data-photo-editor]');
if (editor) {
  const canvas = editor.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  const fallback = editor.querySelector('[data-photo-fallback]');
  const status = editor.querySelector('[data-photo-status]');
  const save = editor.querySelector('[type=submit]');
  const fields = editor.elements;
  let picture = null, blob = null, generation = 0;
  const draw = () => {
    if (!picture) return;
    const rotation = Number(fields.rotation.value);
    const sideways = rotation === 90 || rotation === 270;
    const width = sideways ? picture.naturalHeight : picture.naturalWidth;
    const height = sideways ? picture.naturalWidth : picture.naturalHeight;
    const scale = 800 / Math.max(width, height) * Number(fields.zoom.value);
    const x = (800 - width * scale) * Number(fields.horizontal.value) / 100;
    const y = (800 - height * scale) * Number(fields.vertical.value) / 100;
    ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 800, 800);
    ctx.save();
    ctx.translate(x + width * scale / 2, y + height * scale / 2);
    ctx.rotate(rotation * Math.PI / 180);
    ctx.drawImage(picture, -picture.naturalWidth * scale / 2, -picture.naturalHeight * scale / 2, picture.naturalWidth * scale, picture.naturalHeight * scale);
    ctx.restore();
    canvas.hidden = false; fallback.hidden = true;
    status.textContent = `Zoom ${Math.round(Number(fields.zoom.value) * 100)}%. Square preview; changes are saved only when you choose Save photo.`;
  };
  const load = async () => {
    const current = ++generation;
    picture = null; save.disabled = true;
    if (blob) { URL.revokeObjectURL(blob); blob = null; }
    const file = fields.photo.files[0];
    canvas.hidden = true; fallback.hidden = false;
    if (file && file.size > 10 * 1024 * 1024) {
      status.textContent = 'Choose a photo under 10 MB.'; return;
    }
    const source = file ? (blob = URL.createObjectURL(file)) : editor.dataset.photoSource;
    if (!source) { status.textContent = 'Choose a photo to see the preview.'; return; }
    status.textContent = 'Loading preview…';
    try {
      const next = new Image(); next.src = source;
      await next.decode();
      if (current !== generation) return;
      if (next.naturalWidth * next.naturalHeight > 12500000) throw new Error('too large');
      picture = next; draw(); save.disabled = false;
    } catch {
      if (current === generation) status.textContent = 'Choose a readable JPG, PNG or WebP under 10 MB and 12.5 megapixels.';
    }
  };
  for (const key of ['zoom', 'horizontal', 'vertical', 'rotation']) fields[key].addEventListener('input', draw);
  fields.photo.addEventListener('change', load);
  const reset = editor.querySelector('[data-photo-reset]');
  reset.hidden = false;
  reset.addEventListener('click', () => {
    fields.zoom.value = '1'; fields.horizontal.value = '50'; fields.vertical.value = '50'; fields.rotation.value = '0'; draw();
  });
  window.addEventListener('pagehide', () => { if (blob) URL.revokeObjectURL(blob); });
  load();
}
