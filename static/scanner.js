/* Product barcode scanning stays entirely in this browser; frames/photos are never uploaded. */
(() => {
  'use strict';
  const start = document.querySelector('#scan-barcode');
  if (!start) return;
  const form = document.querySelector('#barcode-lookup');
  const input = document.querySelector('#lookup-barcode');
  const panel = document.querySelector('#scanner-panel');
  const video = document.querySelector('#barcode-video');
  const status = document.querySelector('#scanner-status');
  const photo = document.querySelector('#barcode-photo');
  let controls, stream, frame, timer, generation = 0, photoGeneration = 0, decoderPromise;
  const stop = () => {
    generation++;
    clearTimeout(timer);
    cancelAnimationFrame(frame);
    controls?.stop(); controls = null;
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    if (video.srcObject) video.srcObject.getTracks().forEach(track => track.stop());
    video.srcObject = null; panel.hidden = true; start.disabled = false;
  };
  const found = value => {
    const text = String(value).trim();
    if (!text || text.length > 64 || !/^[A-Za-z0-9._-]+$/.test(text)) return;
    stop(); input.value = text; input.focus();
    status.textContent = 'Barcode read. Looking up the product…';
    form.requestSubmit();
  };
  const zxing = () => {
    if (!decoderPromise) decoderPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = start.dataset.decoder;
      script.onload = () => resolve(window.ZXingBrowser);
      script.onerror = () => { decoderPromise = null; reject(new Error('Decoder unavailable')); };
      document.head.append(script);
    });
    return decoderPromise;
  };
  const detector = async () => {
    if (!window.BarcodeDetector) return null;
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const formats = ['ean_13','ean_8','upc_a','upc_e','itf','code_128'].filter(value => supported.includes(value));
    return formats.length ? new window.BarcodeDetector({formats}) : null;
  };
  start.addEventListener('click', async () => {
    stop(); photoGeneration++; const active = generation;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      status.textContent = 'Camera access is unavailable here. Enter the barcode or use a barcode photo.';
      return;
    }
    start.disabled = true; panel.hidden = false;
    status.textContent = 'Point the rear camera at the barcode. Keep the whole barcode in view.';
    timer = setTimeout(() => { stop(); status.textContent = 'No barcode read yet. Try a photo, enter the digits, or scan again.'; }, 45000);
    const constraints = {audio:false, video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}}};
    try {
      // Request permission directly from the click, before loading a decoder or
      // awaiting feature detection. Mobile browsers may require that gesture.
      const acquired = await navigator.mediaDevices.getUserMedia(constraints);
      if (active !== generation) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired; video.srcObject = stream; await video.play();
      let native = null;
      try { native = await detector(); } catch { /* Locally bundled decoder below. */ }
      if (active !== generation) return;
      if (native) {
        let last = 0;
        const decode = async now => {
          if (active !== generation) return;
          if (video.readyState >= 2 && now - last > 200) {
            last = now;
            try {
              const results = await native.detect(video);
              if (active !== generation) return;
              if (results.length) { found(results[0].rawValue); return; }
            } catch { /* A frame may not be ready; keep scanning. */ }
          }
          frame = requestAnimationFrame(decode);
        };
        frame = requestAnimationFrame(decode);
      } else {
        const ZXing = await zxing();
        if (active !== generation) return;
        const reader = new ZXing.BrowserMultiFormatOneDReader();
        const acquired = await reader.decodeFromStream(stream, video, result => {
          if (active === generation && result) found(result.getText());
        });
        if (active !== generation) acquired.stop(); else controls = acquired;
      }
    } catch (error) {
      if (active === generation) {
        stop();
        status.textContent = error.name === 'NotAllowedError' || error.name === 'SecurityError'
          ? 'Camera permission is blocked. Check camera permissions below, then try again. You can also enter the barcode or use a photo.'
          : error.name === 'NotFoundError'
          ? 'No camera was found. Enter the barcode or use a barcode photo.'
          : error.message === 'Decoder unavailable'
          ? 'The barcode reader did not load. Reload and try again, or enter the barcode digits.'
          : 'The camera could not start. Close other camera apps and try again, or use a barcode photo.';
        document.querySelector('#camera-help').open = true;
      }
    }
  });
  document.querySelector('#stop-scanner').addEventListener('click', () => {
    stop(); status.textContent = 'Camera stopped. You can enter the barcode manually.';
  });
  const readPhoto = async () => {
    stop(); const active = ++photoGeneration; const file = photo.files?.[0];
    if (!file) return;
    if (!['image/jpeg','image/png','image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
      status.textContent = 'Choose a JPG, PNG or WebP barcode photo under 10 MB.'; return;
    }
    status.textContent = 'Reading the barcode photo on this device…';
    const url = URL.createObjectURL(file);
    try {
      const ZXing = await zxing();
      if (active !== photoGeneration) return;
      const result = await new ZXing.BrowserMultiFormatOneDReader().decodeFromImageUrl(url);
      if (active === photoGeneration) found(result.getText());
    } catch {
      if (active === photoGeneration) status.textContent = 'No readable barcode found. Try a clearer photo or enter the digits.';
    } finally { URL.revokeObjectURL(url); photo.value = ''; }
  };
  photo.addEventListener('change', readPhoto);
  form.addEventListener('submit', () => {
    stop(); photoGeneration++; status.textContent = 'Looking up this barcode…';
    form.querySelector('button[type=submit]').disabled = true;
  });
  window.addEventListener('pagehide', () => { stop(); photoGeneration++; });
  window.addEventListener('pageshow', event => {
    if (event.persisted) {
      stop(); photoGeneration++;
      status.textContent = 'Saved products are checked first. New food products can be looked up online.';
      form.querySelector('button[type=submit]').disabled = false;
    }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  start.disabled = false;
  if (photo.files?.length) readPhoto();
})();
