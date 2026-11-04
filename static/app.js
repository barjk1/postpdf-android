(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let mode = 'link';
  const linkTab = $('link-tab');
  const uploadTab = $('upload-tab');
  const loading = $('loading');
  const convertBtn = $('convert-btn');
  const error = $('error');
  const result = $('result');

  function setMode(next) {
    mode = next;
    for (const [tab, panel, isActive] of [
      [linkTab, $('link-panel'), next === 'link'],
      [uploadTab, $('upload-panel'), next === 'upload']
    ]) {
      tab.classList.toggle('active', isActive);
      tab.setAttribute('aria-selected', String(isActive));
      panel.classList.toggle('hidden', !isActive);
    }
    hideError();
  }
  function hideError() {error.textContent = ''; error.classList.add('hidden');}
  function showError(message) {
    error.textContent = message; error.classList.remove('hidden');
    error.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }
  function setBusy(busy) {
    convertBtn.disabled = busy;
    $('convert-btn-text').textContent = busy ? 'İşleniyor...' : 'PDF oluştur';
    loading.classList.toggle('hidden', !busy);
  }
  function selectedPaper() {return document.querySelector('input[name="paper"]:checked')?.value || 'A4';}

  linkTab.addEventListener('click', () => setMode('link'));
  uploadTab.addEventListener('click', () => setMode('upload'));
  $('paste-btn').addEventListener('click', async () => {
    try {const value = await navigator.clipboard.readText(); $('url').value = value; $('url').focus();}
    catch {showError('Yapıştırmak için bağlantı alanına uzun basıp "Yapıştır" seçeneğini kullan.');}
  });
  $('photos').addEventListener('change', () => {
    const n = $('photos').files.length;
    $('file-label').textContent = n ? `${n} fotoğraf seçildi` : 'En fazla 30 fotoğraf • Toplam 60 MB';
  });
  $('url').addEventListener('keydown', e => {if (e.key === 'Enter') {e.preventDefault(); convertBtn.click();}});

  convertBtn.addEventListener('click', async () => {
    hideError(); result.classList.add('hidden');
    let path, options;
    if (mode === 'link') {
      const value = $('url').value.trim();
      if (!value) {showError('Önce Instagram gönderisinin bağlantısını yapıştır.'); return;}
      path = '/api/convert';
      options = {headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
                 body: JSON.stringify({url: value, paper: selectedPaper()})};
    } else {
      const files = $('photos').files;
      if (!files.length) {showError('Önce en az bir fotoğraf seç.'); return;}
      if (files.length > 30) {showError('En fazla 30 fotoğraf yüklenebilir.'); return;}
      const total = [...files].reduce((n, file) => n + file.size, 0);
      if (total > 60 * 1024 * 1024) {showError('Toplam dosya boyutu 60 MB sınırını aşıyor.'); return;}
      const form = new FormData();
      for (const file of files) form.append('images', file);
      form.append('paper', selectedPaper());
      path = '/api/upload';
      options = {headers: {'X-CSRF-Token': csrf}, body: form};
    }
    setBusy(true);
    try {
      const response = await fetch(path, {method: 'POST', credentials: 'same-origin', ...options});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || 'İşlem başarısız oldu. Tekrar deneyin.');
      $('download-btn').href = payload.pdf_url;
      $('download-btn').setAttribute('download', payload.filename || 'belge.pdf');
      $('print-btn').href = payload.print_url;
      $('result-summary').textContent = `${payload.count} fotoğraf • ${payload.count} PDF sayfası`;
      result.classList.remove('hidden');
      result.scrollIntoView({behavior: 'smooth', block: 'center'});
    } catch (err) {
      showError(err?.message || 'Bağlantı hatası. İnternet bağlantını kontrol et.');
    } finally {setBusy(false);}
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(() => {}));
  }
})();
