/* Paper Trail — browser glue. Vanilla JS, zero build step, same-origin only. */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ setup */

  const $ = (id) => document.getElementById(id);

  const SAMPLE_CASES = [
    { id: 'benefits-denial', label: 'Insurance denial' },
    { id: 'snap-recertification', label: 'SNAP recertification' },
    { id: 'immigration-rfe', label: 'Immigration RFE' }
  ];

  const MODES = ['explain', 'deadline', 'ask', 'respond'];
  const MODE_LABELS = {
    explain: 'Explain',
    deadline: 'Deadline',
    ask: 'Ask',
    respond: 'Respond'
  };
  const LANG_NAMES = {
    en: 'English',
    es: 'Español',
    el: 'Ελληνικά',
    bn: 'বাংলা',
    zh: '中文',
    ar: 'العربية'
  };
  const SPEECH_LANG = {
    en: 'en-US',
    es: 'es-ES',
    el: 'el-GR',
    bn: 'bn-BD',
    zh: 'zh-CN',
    ar: 'ar-SA'
  };
  const CODE_LABELS = {
    network: 'Server unreachable',
    timeout: 'Request timed out',
    invalid_request: 'Request not valid',
    not_found: 'Document not found',
    demo_mode: 'Demo mode',
    demo_lang: 'Language not recorded',
    demo_uploads: 'Uploads unavailable',
    too_large: 'File too large',
    unsupported_type: 'Unsupported file',
    upstream_rate_limited: 'Rate limited upstream',
    model_format: 'Unreadable model output',
    upstream_error: 'Model call failed',
    no_api_key: 'No API key configured',
    internal: 'Server error',
    bad_response: 'Unexpected response',
    read_failed: 'File could not be read',
    upload_failed: 'Upload failed'
  };

  const MAX_FILES = 20;
  const MAX_IMAGE_EDGE = 1600;
  const BASE_TITLE = 'Paper Trail — a quiet desk for confusing mail';
  const UPLOAD_RETRY_STATUSES = [0, 429, 500, 502];
  const ACCEPTED_MIME = [
    'image/png', 'image/jpeg', 'image/webp',
    'text/plain', 'text/markdown', 'application/pdf'
  ];
  const EXT_MIME = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    webp: 'image/webp',
    txt: 'text/plain',
    md: 'text/markdown',
    pdf: 'application/pdf'
  };

  const state = {
    health: null,
    stats: null,
    docs: [],
    activeId: null,
    doc: null,
    pageIndex: 0,
    mode: 'explain',
    lang: 'en',
    busy: false,
    viewSeq: 0,
    analysisSeq: 0,
    result: null,
    citationRefs: [],
    speaking: false,
    uploadsBusy: false,
    uploadEntries: [],
    lastFailedFiles: [],
    uploadTimer: null,
    toastTimer: null,
    sampleBusy: null,
    lastSampleId: null,
    confirmAction: null,
    confirmLabel: 'Delete',
    confirmBusy: false,
    confirmReturn: null,
    booting: false,
    offline: false,
    started: false,
    dragDepth: 0
  };

  /* ---------------------------------------------------------------- helpers */

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[ch]));
  }

  function str(value) {
    if (typeof value === 'string') return value;
    if (value == null) return '';
    return String(value);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function asText(value) {
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (value && typeof value === 'object' && typeof value.text === 'string') return value.text;
    return '';
  }

  function truncate(value, length) {
    const text = str(value);
    return text.length > length ? text.slice(0, Math.max(0, length - 1)) + '…' : text;
  }

  function paragraphHtml(value) {
    const raw = str(value).trim();
    if (!raw) return '';
    return raw.split(/\n{2,}/).map((block) => (
      '<p>' + esc(block).replace(/\n/g, '<br>') + '</p>'
    )).join('');
  }

  function normalizeQuote(value) {
    return str(value).toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function formatBytes(bytes) {
    let number = Number(bytes);
    if (!Number.isFinite(number) || number <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let index = 0;
    while (number >= 1024 && index < units.length - 1) {
      number /= 1024;
      index += 1;
    }
    const rounded = number >= 10 || index === 0 ? Math.round(number) : number.toFixed(1);
    return rounded + ' ' + units[index];
  }

  function formatDate(value) {
    const text = str(value).trim();
    if (!text) return '';
    let date = null;
    const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
    if (iso) {
      date = new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
    } else {
      date = new Date(text);
    }
    if (!date || Number.isNaN(date.getTime())) return text;
    try {
      return date.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
      });
    } catch (err) {
      return text;
    }
  }

  function strictIsoDate(value) {
    if (typeof value !== 'string') return null;
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
    if (!match) return null;
    const year = Number(match[1]);
    const month = Number(match[2]) - 1;
    const day = Number(match[3]);
    const date = new Date(year, month, day);
    if (
      date.getFullYear() !== year ||
      date.getMonth() !== month ||
      date.getDate() !== day
    ) {
      return null;
    }
    return value.trim();
  }

  function fileExtension(name) {
    const match = /\.([a-z0-9]+)$/i.exec(str(name));
    return match ? match[1].toLowerCase() : '';
  }

  function stripDataUrl(dataUrl) {
    const text = str(dataUrl);
    const comma = text.indexOf(',');
    return comma >= 0 ? text.slice(comma + 1) : '';
  }

  function pageCountOf(doc) {
    if (!doc) return 0;
    if (Array.isArray(doc.pages) && doc.pages.length) return doc.pages.length;
    const count = Number(doc.page_count);
    return Number.isFinite(count) && count > 0 ? Math.round(count) : 0;
  }

  function pluralPages(count) {
    return count + ' page' + (count === 1 ? '' : 's');
  }

  function placeholderFor(mode) {
    if (mode === 'deadline') {
      return 'Press Analyze to find every date that matters and what happens if you miss it.';
    }
    if (mode === 'ask') {
      return 'Ask a question this letter should answer. Answers cite the page they came from.';
    }
    if (mode === 'respond') {
      return 'Press Analyze to draft a reply you can send, with a checklist before you send it.';
    }
    return 'Press Analyze for a plain-language explanation. Every answer points to the page it came from.';
  }

  function modeElementList() {
    return Array.prototype.slice.call(document.querySelectorAll('.mode-tab'));
  }

  function sampleElementList() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-sample]'));
  }

  function sameOriginImageUrl(value) {
    if (typeof value !== 'string' || !value) return null;
    try {
      const url = new URL(value, window.location.href);
      if (url.origin !== window.location.origin) return null;
      return url.pathname + url.search;
    } catch (err) {
      return null;
    }
  }

  function eventElement(event) {
    return event && event.target instanceof Element ? event.target : null;
  }

  /* -------------------------------------------------------------------- api */

  function makeError(status, code, message) {
    const error = new Error(message);
    error.status = Number(status) || 0;
    error.code = code || '';
    return error;
  }

  function errorFromResponse(status, data) {
    const raw = data && data.error;
    let code = '';
    let message = '';
    if (typeof raw === 'string') {
      message = raw;
    } else if (raw && typeof raw === 'object') {
      code = raw.code ? String(raw.code) : '';
      message = raw.message ? String(raw.message) : '';
    }
    if (!message && data && typeof data.message === 'string') message = data.message;
    if (!message) message = 'The server returned HTTP ' + status + '.';
    return makeError(status, code || ('http_' + status), message);
  }

  async function api(path, options) {
    const opts = options || {};
    const init = {
      method: opts.method || 'GET',
      headers: {}
    };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = controller
      ? window.setTimeout(() => controller.abort(), opts.timeoutMs || 120000)
      : null;
    if (controller) init.signal = controller.signal;

    let response;
    try {
      response = await fetch(path, init);
    } catch (err) {
      if (timer) window.clearTimeout(timer);
      if (err && err.name === 'AbortError') {
        throw makeError(0, 'timeout', 'The request timed out. The local server may still be busy — try again.');
      }
      throw makeError(0, 'network', 'Can’t reach the local Paper Trail server. Start python server.py, then retry.');
    }
    if (timer) window.clearTimeout(timer);

    let raw = '';
    try {
      raw = await response.text();
    } catch (err) {
      raw = '';
    }
    let data = null;
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch (err) {
        data = null;
      }
    }
    if (!response.ok || (data && data.ok === false)) {
      throw errorFromResponse(response.status, data);
    }
    if (!data) {
      throw makeError(response.status, 'bad_response', 'The server sent a response that wasn’t valid JSON.');
    }
    return data;
  }

  /* --------------------------------------------------------- status + boot */

  function updateCalls(calls, bytes) {
    const count = Number.isFinite(Number(calls)) ? Number(calls) : 0;
    const sent = Number.isFinite(Number(bytes)) ? Number(bytes) : 0;
    const label = count + ' external call' + (count === 1 ? '' : 's');
    const pill = $('callsPill');
    pill.textContent = label;
    pill.className = 'pill ' + (count === 0 ? 'pill-clean' : 'pill-neutral');
    pill.title = count === 0
      ? 'No outbound model requests. Nothing has left this machine.'
      : count + ' outbound model request' + (count === 1 ? '' : 's') + ' · ' + formatBytes(sent) + ' sent';
    $('privacyCalls').textContent = 'External calls: ' + count;
    $('privacyCalls').title = count === 0
      ? 'No external model calls have been made.'
      : formatBytes(sent) + ' sent in request bodies to the hosted model.';
  }

  function applyStats() {
    if (!state.stats) return;
    updateCalls(state.stats.externalCalls, state.stats.bytesOut);
  }

  function applyHealth() {
    const health = state.health;
    if (!health) return;
    const demo = !!health.demoMode;
    const pill = $('statusPill');
    let className = 'pill';
    let label = 'Ready';
    let title = '';

    if (demo) {
      className += ' pill-demo';
      label = 'Demo mode';
      title = 'Demo mode: recorded sample answers, no external calls.';
    } else if (health.backend === 'openai') {
      className += ' pill-local';
      label = 'Local model';
      title = 'Local model' + (health.model ? ' · ' + health.model : '') + ' · OpenAI-compatible backend';
    } else if (health.backend === 'gemini') {
      className += ' pill-live';
      label = 'Gemma 4 · hosted';
      title = 'Hosted Gemini API' + (health.model ? ' · ' + health.model : '');
      if (health.hasKey === false) title += ' · no API key configured';
    } else {
      className += ' pill-neutral';
      label = health.model ? String(health.model) : 'Model ready';
      title = 'Backend: ' + str(health.backend || 'unknown');
    }

    pill.className = className;
    pill.innerHTML = '<span class="pill-dot" aria-hidden="true"></span>' + esc(label);
    pill.title = title;

    $('demoBanner').hidden = !demo;
    $('demoLangHint').hidden = !demo;

    const store = health.dataDir ? String(health.dataDir) : './data';
    $('storePath').textContent = store;
    $('storePath').title = store;

    state.offline = false;
    $('offlineBanner').hidden = true;

    syncUploadAffordances();
    if (state.stats) applyStats();
    else updateCalls(Number(health.externalCalls) || 0, Number(health.bytesOut) || 0);
  }

  function setOffline(offline, message) {
    state.offline = !!offline;
    $('offlineBanner').hidden = !state.offline;
    if (!state.offline) return;
    const pill = $('statusPill');
    pill.className = 'pill pill-offline';
    pill.innerHTML = '<span class="pill-dot" aria-hidden="true"></span>Offline';
    pill.title = message || 'The local server is unreachable.';

    const callsPill = $('callsPill');
    callsPill.textContent = 'External calls —';
    callsPill.className = 'pill pill-neutral';
    callsPill.title = 'The local server is not reachable; the call count is unknown.';
    $('privacyCalls').textContent = 'External calls: —';
    $('privacyCalls').title = 'Unknown while the local server is unreachable.';
  }

  async function refreshStatsQuiet() {
    try {
      state.stats = await api('/api/stats', { timeoutMs: 15000 });
      applyStats();
      return true;
    } catch (err) {
      return false;
    }
  }

  async function refreshDocs() {
    const data = await api('/api/docs');
    state.docs = Array.isArray(data.docs) ? data.docs : [];
    if (state.activeId && !state.docs.some((doc) => doc.id === state.activeId)) {
      clearSelection();
    }
    renderDocList();
  }

  async function refreshDocsQuiet() {
    try {
      await refreshDocs();
    } catch (err) {
      if (err && err.code === 'network') setOffline(true, err.message);
      const empty = $('docListEmpty');
      empty.hidden = false;
      const detail = err && err.message ? ': ' + err.message : '';
      empty.textContent = state.docs.length
        ? 'Couldn’t refresh the document list' + detail
        : 'Couldn’t load documents' + detail;
    }
  }

  async function boot(options) {
    const opts = options || {};
    if (state.booting) return;
    state.booting = true;
    const retry = $('retryBoot');
    retry.disabled = true;
    retry.textContent = 'Retrying…';

    try {
      try {
        state.health = await api('/api/health', { timeoutMs: 15000 });
        applyHealth();
      } catch (err) {
        setOffline(true, err.message);
      }

      await Promise.all([refreshDocsQuiet(), refreshStatsQuiet()]);

      if (!state.offline && !state.started) {
        state.started = true;
        await maybeAutoSample();
      }
    } finally {
      state.booting = false;
      retry.disabled = false;
      retry.textContent = 'Retry';
    }
  }

  /* ---------------------------------------------------------------- samples */

  async function maybeAutoSample() {
    let query = null;
    try {
      query = new URLSearchParams(window.location.search).get('sample');
    } catch (err) {
      query = null;
    }
    if (!query) return;
    const id = String(query).trim().toLowerCase();
    if (!SAMPLE_CASES.some((item) => item.id === id)) {
      flash('Unknown sample case “' + query + '”. Choose one from the sidebar.', 'warn');
      return;
    }
    if (state.docs.some((doc) => doc.id === id)) {
      await selectDoc(id);
      return;
    }
    await loadSample(id, { quiet: true });
  }

  function setSampleBusy(id) {
    sampleElementList().forEach((button) => {
      const isThis = !!id && button.getAttribute('data-sample') === id;
      button.disabled = !!id;
      button.classList.toggle('is-loading', isThis);
      button.setAttribute('aria-busy', isThis ? 'true' : 'false');
    });
  }

  function hideSampleError() {
    const box = $('sampleError');
    box.hidden = true;
    box.innerHTML = '';
  }

  function showSampleError(err) {
    const box = $('sampleError');
    const code = err && err.code ? String(err.code) : '';
    const label = CODE_LABELS[code] || (err && err.status ? 'HTTP ' + err.status : 'Couldn’t load the sample');
    box.innerHTML =
      '<span class="error-code">' + esc(label) + '</span>' +
      '<span class="inline-error-text">' + esc(err && err.message ? err.message : 'The sample could not be loaded.') + '</span>' +
      '<button type="button" class="btn btn-small" data-sample-retry="' + esc(state.lastSampleId || '') + '">Retry</button>';
    box.hidden = false;
  }

  async function loadSample(id, options) {
    const opts = options || {};
    if (!id || state.sampleBusy) return;
    if (!SAMPLE_CASES.some((item) => item.id === id)) return;
    if (state.offline) {
      flash('The local server is unreachable. Retry above, then load the sample.', 'error');
      return;
    }

    state.sampleBusy = id;    state.lastSampleId = id;
    setSampleBusy(id);
    hideSampleError();

    try {
      const data = await api('/api/samples/load', {
        method: 'POST',
        body: { sample: id },
        timeoutMs: 60000
      });
      await refreshDocsQuiet();
      const doc = data && data.doc;
      if (doc && doc.id) await selectDoc(doc.id, { preloaded: doc });
      else await selectDoc(id);
      refreshStatsQuiet();
      if (data && data.replaced && !opts.quiet) {
        flash('Sample case refreshed in your local store.', 'info');
      }
    } catch (err) {
      showSampleError(err);
      if (err && err.code === 'network') setOffline(true, err.message);
    } finally {
      state.sampleBusy = null;
      setSampleBusy(null);
    }
  }

  /* -------------------------------------------------------------- documents */

  function renderDocList() {
    const docs = state.docs;
    const list = $('docList');
    const empty = $('docListEmpty');

    $('docCount').textContent = String(docs.length);
    empty.hidden = docs.length > 0;
    if (!docs.length) {
      empty.textContent = state.offline
        ? 'The local server is unreachable. Retry above.'
        : 'No documents yet. Load a sample case below.';
      list.innerHTML = '';
    } else {
      list.innerHTML = docs.map((doc) => {
        const active = doc.id === state.activeId;
        const count = pageCountOf(doc);
        const title = str(doc.title) || 'Untitled document';
        const type = str(doc.doc_type) || 'document';
        return '<li class="doc-item' + (active ? ' active' : '') + '">' +
          '<button type="button" class="doc-select" data-doc-id="' + esc(doc.id) + '"' +
            (active ? ' aria-current="true"' : '') +
            ' aria-label="Open ' + esc(title) + '">' +
            '<span class="doc-name">' + esc(title) + '</span>' +
            '<span class="doc-sub">' +
              '<span>' + esc(type) + '</span>' +
              (count
                ? '<span class="dot-sep" aria-hidden="true">·</span><span>' + pluralPages(count) + '</span>'
                : '') +
              (doc.source === 'sample' ? '<span class="badge badge-sample">SAMPLE</span>' : '') +
            '</span>' +
          '</button>' +
          '<button type="button" class="doc-delete" data-delete-id="' + esc(doc.id) + '"' +
            ' aria-label="Delete ' + esc(title) + '" title="Delete document">×</button>' +
        '</li>';
      }).join('');
    }

    $('deleteAllBtn').disabled = docs.length === 0;
  }

  function clearSelection() {
    state.viewSeq += 1;
    state.analysisSeq += 1;
    state.activeId = null;
    state.doc = null;
    state.result = null;
    state.citationRefs = [];
    state.busy = false;
    stopSpeaking();
    hideCiteBar();
    hideAskError();
    hideAnalysisError();
    state.pageIndex = 0;
    $('docView').hidden = true;
    $('emptyView').hidden = false;
    document.title = BASE_TITLE;
    renderDocList();
  }

  async function selectDoc(id, options) {
    const opts = options || {};
    if (!id) return;
    if (state.activeId === id && state.doc && !opts.preloaded) return;

    const seq = (state.viewSeq += 1);
    state.analysisSeq += 1;
    state.activeId = id;
    state.doc = opts.preloaded || null;
    state.pageIndex = 0;
    state.result = null;
    state.citationRefs = [];
    state.busy = false;
    stopSpeaking();
    hideCiteBar();
    hideAskError();
    hideAnalysisError();
    resetAnalysisUI(placeholderFor(state.mode));
    updateAnalyzeAvailability();

    $('emptyView').hidden = true;
    $('docView').hidden = false;
    renderDocList();

    const hadPreloaded = !!state.doc;
    if (hadPreloaded) renderDoc();
    else renderDocSkeleton();

    try {
      if (!state.doc) {
        const data = await api('/api/docs/' + encodeURIComponent(id), { timeoutMs: 60000 });
        if (seq !== state.viewSeq) return;
        state.doc = data.doc;
      }
      if (seq !== state.viewSeq) return;
      if (!hadPreloaded) renderDoc();
    } catch (err) {
      if (seq !== state.viewSeq) return;
      if (err && err.status === 404) {
        flash('That document is no longer in the local store.', 'warn');
        clearSelection();
        await refreshDocsQuiet();
        return;
      }
      showDocLoadError(err);
    }
  }

  function renderDocSkeleton() {
    $('docBadges').innerHTML = '<span class="badge">Opening</span>';
    $('docTitle').textContent = 'Opening document…';
    $('docMeta').textContent = '';
    $('sampleNotice').hidden = true;
    $('docWarnings').hidden = true;
    $('docWarnings').innerHTML = '';
    $('pageLabel').textContent = '1 of 1';
    $('prevPage').disabled = true;
    $('nextPage').disabled = true;
    $('pageImage').hidden = true;
    $('pageImage').removeAttribute('src');
    $('pageText').hidden = true;
    $('extractedWrap').hidden = true;
    $('pageEmpty').hidden = false;
    $('pageEmpty').textContent = 'Opening the local copy…';
    $('pageLoading').hidden = true;
    $('pageFrame').classList.remove('is-loading');
    $('analyzeBtn').disabled = true;
  }

  function renderDoc() {
    const doc = state.doc;
    if (!doc) return;

    const title = str(doc.title) || 'Untitled document';
    const type = str(doc.doc_type) || 'document';
    document.title = title + ' — Paper Trail';
    $('docTitle').textContent = title;

    let badges = '';
    if (doc.source === 'sample') {
      badges += '<span class="badge badge-sample">SAMPLE</span>';
    } else if (doc.source === 'upload') {
      badges += '<span class="badge badge-upload">UPLOADED</span>';
    }
    if (type) badges += '<span class="badge">' + esc(type) + '</span>';
    $('docBadges').innerHTML = badges;

    const bits = [];
    if (doc.agency) bits.push(str(doc.agency));
    bits.push(type);
    const count = pageCountOf(doc);
    if (count) bits.push(pluralPages(count));
    if (doc.doc_date) bits.push('dated ' + formatDate(doc.doc_date));
    if (doc.created_at) bits.push('added ' + formatDate(doc.created_at));
    $('docMeta').textContent = bits.join(' · ');

    $('sampleNotice').hidden = doc.source !== 'sample';

    const warnings = asArray(doc.warnings).filter((item) => typeof item === 'string' && item.trim());
    const warningBox = $('docWarnings');
    warningBox.hidden = warnings.length === 0;
    warningBox.innerHTML = warnings.length
      ? '<strong>Heads up</strong><ul>' + warnings.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul>'
      : '';

    state.pageIndex = 0;
    renderPage();
    resetAnalysisUI(placeholderFor(state.mode));
    updateAnalyzeAvailability();

    // Selecting a document re-renders the sidebar, which would otherwise drop
    // keyboard focus on the floor. Move it to the newly opened document instead.
    // During boot (for example ?sample=...), leave focus where the browser put it.
    if (!state.booting) {
      try {
        $('docTitle').focus();
      } catch (err) {
        /* ignore */
      }
    }
  }

  function showDocLoadError(err) {
    $('docBadges').innerHTML = '<span class="badge">ERROR</span>';
    $('docTitle').textContent = 'Could not open this document';
    $('docMeta').textContent = err && err.message ? err.message : 'The document could not be loaded.';
    $('sampleNotice').hidden = true;
    $('docWarnings').hidden = true;
    $('docWarnings').innerHTML = '';
    $('pageLabel').textContent = '1 of 1';
    $('prevPage').disabled = true;
    $('nextPage').disabled = true;
    $('pageImage').hidden = true;
    $('pageImage').removeAttribute('src');
    $('pageText').hidden = true;
    $('extractedWrap').hidden = true;
    $('pageLoading').hidden = true;
    $('pageFrame').classList.remove('is-loading');
    $('pageEmpty').hidden = false;
    $('pageEmpty').textContent = 'The local copy could not be opened. Select the document again to retry.';
    $('resultMeta').hidden = true;
    $('resultMeta').innerHTML = '';
    $('resultBody').innerHTML = '<p class="result-placeholder">Analysis is unavailable until this document opens.</p>';
    $('analyzeBtn').disabled = true;
    document.title = 'Document error — Paper Trail';
  }

  /* ----------------------------------------------------------------- viewer */

  function hideCiteBar() {
    $('citeBar').hidden = true;
    $('citeQuote').textContent = '';
    $('citePage').textContent = '';
  }

  function showCiteBar(page, quote) {
    $('citePage').textContent = Number.isFinite(Number(page)) ? String(Number(page)) : '?';
    $('citeQuote').textContent = str(quote) || 'No quote text was returned for this citation.';
    const bar = $('citeBar');
    bar.hidden = false;
    try {
      bar.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (err) {
      /* older browsers can still show the bar without scrolling */
    }
  }

  function renderPage() {
    const doc = state.doc;
    if (!doc) return;
    const pages = asArray(doc.pages);

    $('pageLoading').hidden = true;
    $('pageFrame').classList.remove('is-loading');
    $('pageImage').hidden = true;
    $('pageImage').removeAttribute('src');
    $('pageImage').alt = '';
    $('pageText').hidden = true;
    $('pageText').textContent = '';
    $('extractedWrap').hidden = true;
    $('extractedText').textContent = '';
    $('pageEmpty').hidden = true;
    $('pageEmpty').textContent = '';

    if (!pages.length) {
      $('pageLabel').textContent = '1 of 1';
      $('prevPage').disabled = true;
      $('nextPage').disabled = true;
      $('pageEmpty').hidden = false;
      $('pageEmpty').textContent = 'No page images or text were stored for this document.';
      return;
    }

    if (state.pageIndex < 0) state.pageIndex = 0;
    if (state.pageIndex > pages.length - 1) state.pageIndex = pages.length - 1;

    const page = pages[state.pageIndex] || {};
    const pageNumber = Math.round(Number(page.n)) || state.pageIndex + 1;
    const text = typeof page.text === 'string' ? page.text : '';
    const hasText = text.trim().length > 0;
    const imageUrl = sameOriginImageUrl(page.image_url);

    $('pageLabel').textContent = pageNumber + ' of ' + pages.length;
    $('prevPage').disabled = state.pageIndex <= 0;
    $('nextPage').disabled = state.pageIndex >= pages.length - 1;

    if (imageUrl) {
      $('pageImage').alt = 'Page ' + pageNumber + ' of ' + (str(doc.title) || 'the document');
      $('pageImage').hidden = false;
      $('pageFrame').classList.add('is-loading');
      $('pageLoading').hidden = false;
      $('pageImage').onload = function onPageImageLoad() {
        $('pageFrame').classList.remove('is-loading');
        $('pageLoading').hidden = true;
      };
      $('pageImage').onerror = function onPageImageError() {
        $('pageFrame').classList.remove('is-loading');
        $('pageLoading').hidden = true;
        $('pageImage').hidden = true;
        $('pageImage').removeAttribute('src');
        if (hasText) {
          $('pageText').textContent = text;
          $('pageText').hidden = false;
          $('pageEmpty').hidden = true;
        } else {
          $('pageText').hidden = true;
          $('pageEmpty').hidden = false;
          $('pageEmpty').textContent = 'This page image could not be loaded, and no page text was stored.';
        }
      };
      $('pageImage').src = imageUrl;

      $('extractedWrap').hidden = false;
      $('extractedText').textContent = hasText
        ? text
        : 'No text was extracted from this page. The image is what the model sees.';
    } else {
      $('pageText').textContent = text;
      $('pageText').hidden = !hasText;
      $('pageEmpty').hidden = hasText;
      $('pageEmpty').textContent = 'No text was extracted from this page. It may be a scanned image.';
    }
  }

  function turnPage(delta) {
    if (!state.doc) return;
    const pages = asArray(state.doc.pages);
    if (!pages.length) return;
    const next = state.pageIndex + delta;
    if (next < 0 || next >= pages.length) return;
    state.pageIndex = next;
    hideCiteBar();
    renderPage();
  }

  function handleCitation(ref) {
    if (!ref) return;
    const pageNumber = Number(ref.page);
    const pages = state.doc ? asArray(state.doc.pages) : [];
    const index = pages.findIndex((page) => Number(page.n) === pageNumber);
    if (index >= 0) {
      state.pageIndex = index;
      renderPage();
    } else if (pageNumber === state.pageIndex + 1 && pages.length) {
      renderPage();
    }
    showCiteBar(pageNumber, ref.quote);
    if (index < 0) {
      flash('Page ' + (Number.isFinite(pageNumber) ? pageNumber : '?') + ' isn’t in this document.', 'warn');
    }
  }

  /* --------------------------------------------------------------- analysis */

  function updateModeTabs() {
    modeElementList().forEach((button) => {
      const active = button.getAttribute('data-mode') === state.mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function setMode(mode) {
    if (MODES.indexOf(mode) === -1 || state.busy) return;
    state.mode = mode;
    updateModeTabs();
    $('askField').hidden = mode !== 'ask';
    $('respondField').hidden = mode !== 'respond';
    clearAskError();
    if (state.doc) resetAnalysisUI(placeholderFor(mode));
    updateAnalyzeAvailability();
  }

  function setLanguage(lang) {
    if (!LANG_NAMES[lang]) return;
    state.lang = lang;
    $('langSelect').value = lang;
  }

  function updateAnalyzeAvailability() {
    const enabled = !!state.doc && !state.busy;
    const button = $('analyzeBtn');
    button.disabled = !enabled;
    button.classList.toggle('is-loading', state.busy);
    button.textContent = state.busy ? 'Analyzing…' : 'Analyze';
    button.setAttribute('aria-busy', state.busy ? 'true' : 'false');

    modeElementList().forEach((tab) => { tab.disabled = state.busy; });
    $('langSelect').disabled = state.busy;
    $('question').disabled = state.busy;
    $('context').disabled = state.busy;
  }

  function hideAskError() {
    const box = $('askError');
    box.hidden = true;
    box.textContent = '';
    $('question').removeAttribute('aria-invalid');
    $('question').removeAttribute('aria-describedby');
  }

  function showAskError(message) {
    const box = $('askError');
    box.textContent = message;
    box.hidden = false;
    $('question').setAttribute('aria-invalid', 'true');
    $('question').setAttribute('aria-describedby', 'askError');
    $('question').focus();
  }

  function clearAskError() {
    hideAskError();
  }

  function buildAnalyzeBody() {
    const body = {
      doc_id: state.doc.id,
      mode: state.mode,
      lang: state.lang
    };
    if (state.mode === 'ask') {
      body.question = $('question').value.trim();
    }
    if (state.mode === 'respond') {
      const context = $('context').value.trim();
      if (context) body.context = context;
    }
    return body;
  }

  function showAnalysisLoading() {
    state.citationRefs = [];
    stopSpeaking();
    hideAnalysisError();
    $('resultMeta').hidden = true;
    $('resultMeta').innerHTML = '';
    const total = pageCountOf(state.doc) || 1;
    const pagesLabel = total > 1 ? 'pages 1–' + total : 'page 1';
    $('resultBody').setAttribute('aria-busy', 'true');
    $('resultBody').innerHTML =
      '<div class="skeleton" aria-hidden="true"><span></span><span></span><span></span></div>' +
      '<p class="loading-line"><span class="spinner" aria-hidden="true"></span>Reading ' + esc(pagesLabel) + '…</p>';
  }

  function hideAnalysisError() {
    const box = $('analysisError');
    box.hidden = true;
    box.innerHTML = '';
  }

  function isRetryableAnalysis(err) {
    if (!err) return false;
    if (err.status === 0) return true;
    if ([429, 500, 502].indexOf(err.status) !== -1) return true;
    return ['network', 'timeout', 'upstream_rate_limited', 'upstream_error', 'model_format', 'internal'].indexOf(err.code) !== -1;
  }

  function showAnalysisError(err) {
    const code = err && err.code ? String(err.code) : '';
    const status = err && err.status ? err.status : 0;
    const label = CODE_LABELS[code] || (status ? 'HTTP ' + status : 'Analysis failed');
    let actions = '';
    if (code === 'demo_lang') {
      actions += '<button type="button" class="btn btn-small" data-error-action="use-english">Use English</button>';
    }
    if (code === 'demo_uploads') {
      actions += '<button type="button" class="btn btn-small" data-error-action="load-sample">Load a sample</button>';
    }
    if (code === 'not_found') {
      actions += '<button type="button" class="btn btn-small" data-error-action="refresh-docs">Refresh documents</button>';
    }
    if (isRetryableAnalysis(err)) {
      actions += '<button type="button" class="btn btn-small" data-error-action="retry">Retry</button>';
    }

    const box = $('analysisError');
    box.innerHTML =
      '<div class="error-head">' +
        '<span class="error-mark" aria-hidden="true">!</span>' +
        '<div><strong>' + esc(label) + '</strong>' +
        '<span class="error-code">' + esc(code || ('HTTP ' + (status || '?'))) + '</span></div>' +
      '</div>' +
      '<p class="error-message">' + esc(err && err.message ? err.message : 'Something went wrong while analyzing this document.') + '</p>' +
      (actions ? '<div class="error-actions">' + actions + '</div>' : '');
    box.hidden = false;

    state.citationRefs = [];
    $('resultMeta').hidden = true;
    $('resultMeta').innerHTML = '';
    $('resultBody').innerHTML = '<p class="result-placeholder">Analysis didn’t complete. The error above explains why.</p>';
    $('resultBody').setAttribute('aria-busy', 'false');
    stopSpeaking();
  }

  function resetAnalysisUI(placeholder) {
    hideAnalysisError();
    $('resultMeta').hidden = true;
    $('resultMeta').innerHTML = '';
    state.citationRefs = [];
    $('resultBody').setAttribute('aria-busy', 'false');
    $('resultBody').innerHTML = '<p class="result-placeholder">' + esc(placeholder || placeholderFor(state.mode)) + '</p>';
    stopSpeaking();
    updateAnalyzeAvailability();
  }

  function verifyInfo(citation) {
    let value;
    if (citation && Object.prototype.hasOwnProperty.call(citation, 'verified')) {
      value = citation.verified;
    } else if (citation) {
      const topLevel = state.result && Array.isArray(state.result.citations) ? state.result.citations : [];
      const page = Number(citation.page);
      const quote = normalizeQuote(citation.quote);
      const match = topLevel.find((item) => (
        Number(item.page) === page && normalizeQuote(item.quote) === quote
      ));
      if (match) value = match.verified;
    }
    if (value === true || value === 'true') {
      return { cls: 'is-verified', mark: '✓', label: 'verified' };
    }
    if (value === false || value === 'false') {
      return { cls: 'is-unverified', mark: '!', label: 'not verified' };
    }
    return { cls: 'is-unchecked', mark: '·', label: 'not checked' };
  }

  function citationChip(citation) {
    if (!citation || typeof citation !== 'object') return '';
    const pageNumber = Number(citation.page);
    const hasPage = Number.isInteger(pageNumber) && pageNumber > 0;
    const pageLabel = hasPage ? String(pageNumber) : '?';
    const quote = typeof citation.quote === 'string' ? citation.quote : '';
    const info = verifyInfo(citation);
    const index = state.citationRefs.push(citation) - 1;
    const aria = 'Go to page ' + pageLabel + ', citation ' + info.label + ': ' + truncate(quote || 'no quote', 110);
    return '<button type="button" class="cite-chip ' + info.cls + '" data-cite="' + index + '" ' +
      'title="' + esc(quote || 'No quote text') + '" aria-label="' + esc(aria) + '">' +
      '<span class="cite-page">p.' + pageLabel + '</span>' +
      '<span class="cite-mark" aria-hidden="true">' + info.mark + '</span>' +
      '<span class="sr-only"> ' + esc(info.label) + '</span>' +
      '</button>';
  }

  function citationRow(citations, label) {
    const list = asArray(citations).filter((item) => item && typeof item === 'object');
    const before = state.citationRefs.length;
    const chips = list.map(citationChip).join('');
    if (!chips || state.citationRefs.length === before) return '';
    return '<div class="cite-row"><p class="res-label">' + esc(label || 'Sources') + '</p>' +
      '<div class="chips">' + chips + '</div></div>';
  }

  function callout(label, text, tone) {
    const content = paragraphHtml(text);
    if (!content) return '';
    return '<div class="callout callout-' + tone + '">' +
      '<p class="callout-label">' + esc(label) + '</p>' +
      '<div class="callout-text" dir="auto">' + content + '</div>' +
      '</div>';
  }

  function calloutList(label, items, tone) {
    const list = asArray(items).map(asText).filter((item) => item.trim().length > 0);
    if (!list.length) return '';
    return '<div class="callout callout-' + tone + '">' +
      '<p class="callout-label">' + esc(label) + '</p>' +
      '<ul>' + list.map((item) => '<li dir="auto">' + esc(item) + '</li>').join('') + '</ul>' +
      '</div>';
  }

  function compareDeadlines(a, b) {
    const aIso = strictIsoDate(a && a.date);
    const bIso = strictIsoDate(b && b.date);
    if (aIso && bIso) return aIso < bIso ? -1 : aIso > bIso ? 1 : 0;
    if (aIso) return -1;
    if (bIso) return 1;
    return 0;
  }

  function renderExplain(data) {
    let html = '';
    if (str(data.what_it_is)) {
      html += '<div class="answer-lead" dir="auto">' + paragraphHtml(data.what_it_is) + '</div>';
    } else {
      html += '<p class="quiet">The model didn’t return a summary for this document.</p>';
    }

    const points = asArray(data.key_points);
    html += '<p class="res-label">Key points</p>';
    if (points.length) {
      html += '<ul class="point-list">' + points.map((point) => {
        const text = typeof point === 'string' ? point : (point && point.text);
        const citation = point && typeof point === 'object' ? point.citation : null;
        return '<li><span class="point-text" dir="auto">' + esc(text || '—') + '</span>' + citationChip(citation) + '</li>';
      }).join('') + '</ul>';
    } else {
      html += '<p class="quiet">No key points were returned.</p>';
    }

    if (str(data.next_action)) {
      html += callout('Next step', data.next_action, 'green');
    }
    html += calloutList('Couldn’t read', data.unreadable, 'amber');
    return html;
  }

  function renderDeadline(data) {
    const deadlines = asArray(data.deadlines).filter((item) => item && typeof item === 'object').slice();
    deadlines.sort(compareDeadlines);

    let html = '<div class="res-headline"><p class="res-label">Deadlines, soonest first</p>' +
      '<span class="count-chip">' + deadlines.length + '</span></div>';

    if (!deadlines.length) {
      html += '<p class="quiet">No deadlines were found in this document.</p>';
    } else {
      html += deadlines.map((deadline, index) => {
        const iso = strictIsoDate(deadline.date);
        const rawDate = str(deadline.date).trim();
        const dateLabel = iso
          ? formatDate(iso)
          : (rawDate && rawDate.toLowerCase() !== 'null' ? rawDate : 'No date stated');
        const dateHtml = iso
          ? '<time class="date-pill" datetime="' + esc(iso) + '">' + esc(dateLabel) + '</time>'
          : '<span class="date-pill date-none">' + esc(dateLabel) + '</span>';
        const soonest = index === 0 && iso
          ? '<span class="next-tag">Soonest</span>'
          : '';
        return '<article class="deadline-card' + (index === 0 && iso ? ' is-next' : '') + '">' +
          '<div class="deadline-top">' +
            '<div class="deadline-date-row">' + dateHtml + soonest + '</div>' +
            citationChip(deadline.citation) +
          '</div>' +
          '<h4 dir="auto">' + esc(str(deadline.what) || 'Deadline') + '</h4>' +
          (str(deadline.consequence)
            ? '<p class="deadline-line" dir="auto"><span class="line-label">If missed</span>' + esc(str(deadline.consequence)) + '</p>'
            : '') +
          (str(deadline.action)
            ? '<p class="deadline-line" dir="auto"><span class="line-label">Do this</span>' + esc(str(deadline.action)) + '</p>'
            : '') +
        '</article>';
      }).join('');
    }

    if (str(data.note)) {
      html += callout('Note', data.note, 'neutral');
    }
    return html;
  }

  function renderAsk(data) {
    let html = '';
    if (str(data.answer)) {
      html += '<div class="answer-lead" dir="auto">' + paragraphHtml(data.answer) + '</div>';
    } else {
      html += '<p class="quiet">The model didn’t return an answer.</p>';
    }

    const citations = asArray(data.citations).length
      ? data.citations
      : (state.result ? state.result.citations : []);
    html += citationRow(citations, 'Sources');

    const missing = str(data.missing_info).trim();
    if (missing && missing.toLowerCase() !== 'null') {
      html += callout('What the document doesn’t say', missing, 'amber');
    }
    return html;
  }

  function renderRespond(data) {
    let html = '';
    const to = str(data.to);
    const subject = str(data.subject);

    if (to || subject) {
      html += '<div class="letter-head">';
      if (to) {
        html += '<div class="letter-row"><span class="letter-k">To</span>' +
          '<span class="letter-v" dir="auto">' + esc(to) + '</span></div>';
      }
      if (subject) {
        html += '<div class="letter-row"><span class="letter-k">Subject</span>' +
          '<span class="letter-v" dir="auto">' + esc(subject) + '</span></div>';
      }
      html += '</div>';
    }

    html += '<div class="letter-card">' +
      '<div class="letter-toolbar">' +
        '<p class="res-label">Ready-to-send letter</p>' +
        '<div class="letter-tools">' +
          '<button type="button" class="btn btn-small" data-action="copy-letter">Copy</button>' +
          '<button type="button" class="btn btn-small" data-action="download-letter">Download .txt</button>' +
        '</div>' +
      '</div>' +
      '<pre class="letter-pre" dir="auto" tabindex="0" aria-label="Draft reply letter">' + esc(str(data.body) || 'The model didn’t return a letter body.') + '</pre>' +
    '</div>';

    const checklist = asArray(data.checklist).map(asText).filter((item) => item.trim().length > 0);
    if (checklist.length) {
      html += '<p class="res-label">Before you send</p><ul class="checklist">' +
        checklist.map((item) => '<li dir="auto">' + esc(item) + '</li>').join('') + '</ul>';
    }

    if (str(data.send_via)) {
      html += '<p class="meta-row" dir="auto"><span class="meta-k">Send via</span>' + esc(str(data.send_via)) + '</p>';
    }

    if (str(data.caution)) {
      html += callout('Keep in mind', data.caution, 'amber');
    }

    const citations = asArray(data.citations).length
      ? data.citations
      : (state.result ? state.result.citations : []);
    html += citationRow(citations, 'Sources');
    return html;
  }

  function renderWarnings(warnings) {
    const list = asArray(warnings).filter((item) => typeof item === 'string' && item.trim());
    if (!list.length) return '';
    return '<div class="result-warnings"><p class="res-label">Verification notes</p>' +
      '<ul>' + list.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul></div>';
  }

  function renderResultMeta(result, mode) {
    const tags = [];
    if (result.demo) {
      tags.push('<span class="tag tag-amber" title="Recorded answer from the bundled demo responses">Recorded demo answer</span>');
    } else {
      if (result.model) tags.push('<span class="tag">' + esc(result.model) + '</span>');
      const latency = Number(result.latency_ms);
      if (Number.isFinite(latency) && latency > 0) {
        tags.push('<span class="tag">' + (latency >= 1000 ? (latency / 1000).toFixed(1) + ' s' : Math.round(latency) + ' ms') + '</span>');
      }
    }
    tags.push('<span class="tag">' + esc(MODE_LABELS[mode] || mode) + '</span>');
    const lang = result.lang || state.lang;
    tags.push('<span class="tag">' + esc(LANG_NAMES[lang] || lang) + '</span>');
    if (state.citationRefs.length) {
      tags.push('<span class="tag tag-muted">' + state.citationRefs.length + ' citation' + (state.citationRefs.length === 1 ? '' : 's') + '</span>');
    }

    const meta = $('resultMeta');
    meta.innerHTML = '<div class="meta-tags">' + tags.join('') + '</div>' +
      '<button type="button" class="btn btn-small btn-ghost" data-action="speak" aria-pressed="false">Read aloud</button>';
    meta.hidden = false;
  }

  function renderResult(result) {
    state.result = result;
    state.citationRefs = [];
    const data = result && result.data && typeof result.data === 'object' ? result.data : {};
    const mode = MODES.indexOf(result && result.mode) !== -1 ? result.mode : state.mode;

    let html = '';
    if (mode === 'deadline') html = renderDeadline(data);
    else if (mode === 'ask') html = renderAsk(data);
    else if (mode === 'respond') html = renderRespond(data);
    else html = renderExplain(data);

    html += renderWarnings(result && result.warnings);
    if (!state.citationRefs.length) {
      html += '<p class="cite-empty">No page citations were returned for this answer.</p>';
    }

    $('resultBody').innerHTML = html;
    $('resultBody').setAttribute('aria-busy', 'false');
    renderResultMeta(result, mode);
  }

  async function runAnalyze() {
    if (!state.doc) {
      flash('Open a document first.', 'warn');
      return;
    }
    if (state.busy) return;

    if (state.mode === 'ask') {
      const question = $('question').value.trim();
      if (!question) {
        showAskError('Add a question first — for example, “What happens if I miss the deadline?”');
        return;
      }
      if (question.length > 1000) {
        showAskError('Keep the question to 1,000 characters or fewer.');
        return;
      }
    }
    hideAskError();

    const seq = (state.analysisSeq += 1);
    const docId = state.doc.id;
    state.busy = true;
    hideAnalysisError();
    updateAnalyzeAvailability();
    showAnalysisLoading();

    try {
      const result = await api('/api/analyze', {
        method: 'POST',
        body: buildAnalyzeBody(),
        timeoutMs: 180000
      });
      if (seq !== state.analysisSeq) return;
      if (!state.doc || state.doc.id !== docId) return;
      state.result = result;
      state.busy = false;
      updateAnalyzeAvailability();
      renderResult(result);
      refreshStatsQuiet();
    } catch (err) {
      if (seq !== state.analysisSeq) return;
      if (!state.doc || state.doc.id !== docId) return;
      state.busy = false;
      state.result = null;
      updateAnalyzeAvailability();
      showAnalysisError(err);
      refreshStatsQuiet();
      if (err && err.code === 'network') setOffline(true, err.message);
    } finally {
      if (seq === state.analysisSeq) {
        state.busy = false;
        updateAnalyzeAvailability();
      }
    }
  }

  /* -------------------------------------------------------------- read aloud */

  function composeSpeech() {
    const result = state.result;
    if (!result) return '';
    const data = result.data && typeof result.data === 'object' ? result.data : {};
    const mode = MODES.indexOf(result.mode) !== -1 ? result.mode : state.mode;
    const parts = [];
    if (state.doc && state.doc.title) parts.push(str(state.doc.title));

    if (mode === 'explain') {
      if (str(data.what_it_is)) parts.push(str(data.what_it_is));
      asArray(data.key_points).forEach((point) => {
        const text = typeof point === 'string' ? point : (point && point.text);
        if (text) parts.push(str(text));
      });
      if (str(data.next_action)) parts.push('Next step. ' + str(data.next_action));
      asArray(data.unreadable).forEach((item) => {
        const text = asText(item);
        if (text) parts.push('Could not read. ' + text);
      });
    } else if (mode === 'deadline') {
      const deadlines = asArray(data.deadlines).slice().sort(compareDeadlines);
      deadlines.forEach((deadline) => {
        if (!deadline || typeof deadline !== 'object') return;
        const date = formatDate(deadline.date);
        let line = (date ? date + '. ' : 'No date stated. ') + (str(deadline.what) || 'Deadline') + '.';
        if (str(deadline.consequence)) line += ' If missed: ' + str(deadline.consequence) + '.';
        if (str(deadline.action)) line += ' Do this: ' + str(deadline.action) + '.';
        parts.push(line);
      });
      if (str(data.note)) parts.push(str(data.note));
    } else if (mode === 'ask') {
      if (str(data.answer)) parts.push(str(data.answer));
      const missing = str(data.missing_info).trim();
      if (missing && missing.toLowerCase() !== 'null') parts.push('The document does not say: ' + missing);
    } else if (mode === 'respond') {
      if (str(data.to)) parts.push('To ' + str(data.to));
      if (str(data.subject)) parts.push('Subject. ' + str(data.subject));
      if (str(data.body)) parts.push(str(data.body));
      const checklist = asArray(data.checklist).map(asText).filter((item) => item.trim().length > 0);
      if (checklist.length) parts.push('Before you send. ' + checklist.join('. ') + '.');
      if (str(data.caution)) parts.push(str(data.caution));
    }

    const warnings = asArray(result.warnings).filter((item) => typeof item === 'string' && item.trim());
    if (warnings.length) parts.push('Notes. ' + warnings.join('. ') + '.');

    return parts.join(' ').replace(/\s+/g, ' ').trim();
  }

  function updateSpeakButtons() {
    document.querySelectorAll('[data-action="speak"]').forEach((button) => {
      button.textContent = state.speaking ? 'Stop reading' : 'Read aloud';
      button.setAttribute('aria-pressed', state.speaking ? 'true' : 'false');
    });
  }

  function stopSpeaking() {
    if (state.speaking && 'speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel();
      } catch (err) {
        /* nothing to cancel */
      }
    }
    state.speaking = false;
    updateSpeakButtons();
  }

  function toggleSpeak() {
    if (!('speechSynthesis' in window) || typeof window.SpeechSynthesisUtterance !== 'function') {
      flash('This browser has no speech engine. The text is still on screen.', 'warn');
      return;
    }
    if (state.speaking) {
      stopSpeaking();
      return;
    }
    const text = composeSpeech();
    if (!text) {
      flash('There is nothing to read yet.', 'info');
      return;
    }

    try {
      window.speechSynthesis.cancel();
      const utterance = new window.SpeechSynthesisUtterance(text.slice(0, 24000));
      utterance.lang = SPEECH_LANG[state.lang] || 'en-US';
      utterance.rate = 0.98;
      utterance.pitch = 1;
      utterance.onend = () => {
        state.speaking = false;
        updateSpeakButtons();
      };
      utterance.onerror = (event) => {
        state.speaking = false;
        updateSpeakButtons();
        if (event && (event.error === 'interrupted' || event.error === 'canceled')) return;
        flash('The browser’s speech engine stopped early. The text is still on screen.', 'warn');
      };
      state.speaking = true;
      updateSpeakButtons();
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      state.speaking = false;
      updateSpeakButtons();
      flash('The browser’s speech engine is unavailable. The text is still on screen.', 'warn');
    }
  }

  /* -------------------------------------------------------------- copy/save */

  function letterText() {
    const data = state.result && state.result.data && typeof state.result.data === 'object'
      ? state.result.data
      : {};
    const lines = [];
    if (str(data.to)) lines.push('To: ' + str(data.to));
    if (str(data.subject)) lines.push('Subject: ' + str(data.subject));
    if (lines.length) lines.push('');
    lines.push(str(data.body) || '');
    return lines.join('\n').trim();
  }

  function safeFileSlug(value) {
    const slug = str(value)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60);
    return slug || 'paper-trail';
  }

  async function writeClipboard(text) {
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (err) {
      /* fall through to the older path */
    }
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand('copy');
      area.remove();
      return !!copied;
    } catch (err) {
      return false;
    }
  }

  async function copyLetter(button) {
    const text = letterText();
    if (!text) {
      flash('There’s no letter to copy yet.', 'info');
      return;
    }
    const copied = await writeClipboard(text);
    if (!copied) {
      flash('Copy was blocked by the browser. Select the letter text and copy it manually.', 'warn');
      return;
    }
    if (button && button.isConnected) {
      const original = button.textContent;
      button.textContent = 'Copied';
      button.disabled = true;
      window.setTimeout(() => {
        if (!button.isConnected) return;
        button.textContent = original;
        button.disabled = false;
      }, 1500);
    }
  }

  function downloadLetter() {
    const text = letterText();
    if (!text) {
      flash('There’s no letter to download yet.', 'info');
      return;
    }
    try {
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const title = state.doc && state.doc.title ? state.doc.title : 'paper-trail';
      link.href = url;
      link.download = safeFileSlug(title) + '-reply.txt';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (err) {
      flash('The browser blocked the download. Select the letter text and copy it manually.', 'warn');
    }
  }

  /* ------------------------------------------------------------------ upload */

  function uploadsBlocked() {
    return !!(state.health && state.health.demoMode);
  }

  function syncUploadAffordances() {
    const blocked = uploadsBlocked();
    const buttons = [$('uploadBtn'), $('chooseFilesBtn')];
    buttons.forEach((button) => {
      if (!button) return;
      button.setAttribute('aria-disabled', blocked ? 'true' : 'false');
      button.classList.toggle('is-disabled', blocked);
      button.title = blocked
        ? 'Uploads are disabled in demo mode. Load a sample case or set GEMINI_API_KEY.'
        : '';
    });
    $('fileInput').disabled = blocked;
    $('dropDemoNote').hidden = !blocked;
    $('dropzone').classList.toggle('is-disabled', blocked);
  }

  function showUploadsDisabled() {
    flash('Demo mode: uploads are disabled. Load a sample case, or set GEMINI_API_KEY for live analysis.', 'warn');
    const note = $('dropDemoNote');
    if (note && !note.hidden) {
      try {
        note.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } catch (err) {
        /* ignore */
      }
    }
  }

  function openFilePicker() {
    if (uploadsBlocked()) {
      showUploadsDisabled();
      return;
    }
    if (state.offline) {
      flash('The local server is unreachable. Start it, then retry.', 'error');
      return;
    }
    if (state.uploadsBusy) {
      flash('An upload is already in progress.', 'warn');
      return;
    }
    $('fileInput').click();
  }

  function classifyFile(file) {
    const type = String(file && file.type ? file.type : '').toLowerCase();
    const extension = fileExtension(file && file.name);
    let mime = '';
    if (ACCEPTED_MIME.indexOf(type) !== -1) mime = type;
    else if (EXT_MIME[extension]) mime = EXT_MIME[extension];
    if (!mime) {
      return {
        ok: false,
        message: 'Unsupported file type. Use PNG, JPG, WebP, PDF, TXT, or Markdown.'
      };
    }
    return { ok: true, mime: mime };
  }

  function readFileAsDataUrl(file, onProgress) {
    return new Promise((resolve, reject) => {
      let reader;
      try {
        reader = new FileReader();
      } catch (err) {
        reject(makeError(0, 'read_failed', 'This browser could not read the file.'));
        return;
      }
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(makeError(0, 'read_failed', '“' + file.name + '” could not be read from this device.'));
      reader.onabort = () => reject(makeError(0, 'read_failed', 'Reading “' + file.name + '” was cancelled.'));
      if (typeof onProgress === 'function') {
        reader.onprogress = (event) => {
          if (event.lengthComputable && event.total) {
            onProgress(Math.round((event.loaded / event.total) * 100));
          }
        };
      }
      reader.readAsDataURL(file);
    });
  }

  async function fileToPayload(file, mime, onProgress) {
    if (mime.indexOf('image/') === 0) {
      return imageFilePayload(file, mime, onProgress);
    }
    const dataUrl = await readFileAsDataUrl(file, onProgress);
    const data = stripDataUrl(dataUrl);
    if (!data) throw makeError(0, 'read_failed', '“' + file.name + '” could not be read.');
    return { name: file.name, mime: mime, data_b64: data };
  }

  async function imageFilePayload(file, mime, onProgress) {
    const original = await readFileAsDataUrl(file, onProgress);
    let bitmap = null;
    try {
      if (typeof window.createImageBitmap === 'function') {
        try {
          bitmap = await window.createImageBitmap(file);
        } catch (err) {
          bitmap = null;
        }
      }
      if (bitmap && (bitmap.width > MAX_IMAGE_EDGE || bitmap.height > MAX_IMAGE_EDGE)) {
        const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
        const width = Math.max(1, Math.round(bitmap.width * scale));
        const height = Math.max(1, Math.round(bitmap.height * scale));
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d');
        if (context) {
          context.fillStyle = '#ffffff';
          context.fillRect(0, 0, width, height);
          context.drawImage(bitmap, 0, 0, width, height);
          const scaled = stripDataUrl(canvas.toDataURL('image/jpeg', 0.92));
          if (scaled) return { name: file.name, mime: 'image/jpeg', data_b64: scaled };
        }
      }
    } catch (err) {
      /* fall back to the original bytes */
    } finally {
      if (bitmap && typeof bitmap.close === 'function') bitmap.close();
    }
    const data = stripDataUrl(original);
    if (!data) throw makeError(0, 'read_failed', '“' + file.name + '” could not be read.');
    return { name: file.name, mime: mime, data_b64: data };
  }

  function ingestFile(payload, onProgress) {
    return new Promise((resolve, reject) => {
      let xhr;
      try {
        xhr = new XMLHttpRequest();
      } catch (err) {
        reject(makeError(0, 'network', 'This browser blocked the upload request.'));
        return;
      }
      xhr.open('POST', '/api/ingest', true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.timeout = 180000;
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total && typeof onProgress === 'function') {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        let data = null;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch (err) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.ok !== false) {
          resolve(data);
        } else {
          reject(errorFromResponse(xhr.status, data));
        }
      };
      xhr.onerror = () => reject(makeError(0, 'network', 'The upload could not reach the local server.'));
      xhr.ontimeout = () => reject(makeError(0, 'timeout', 'The upload timed out. Try a smaller file or retry.'));
      xhr.onabort = () => reject(makeError(0, 'network', 'The upload was cancelled.'));
      try {
        xhr.send(JSON.stringify({ files: [payload] }));
      } catch (err) {
        reject(makeError(0, 'upload_failed', 'The upload could not be started.'));
      }
    });
  }

  let uploadRenderScheduled = false;

  function scheduleUploadRender() {
    if (uploadRenderScheduled) return;
    uploadRenderScheduled = true;
    window.requestAnimationFrame(() => {
      uploadRenderScheduled = false;
      renderUploadList();
    });
  }

  function renderUploadList() {
    const entries = state.uploadEntries || [];
    $('uploadList').innerHTML = entries.map((entry) => {
      let label = 'Waiting';
      if (entry.status === 'reading') label = 'Reading…';
      else if (entry.status === 'uploading') label = Math.round(entry.progress) + '%';
      else if (entry.status === 'done') label = 'Added';
      else if (entry.status === 'failed') label = 'Failed';

      const progress = Math.max(0, Math.min(100, Number(entry.progress) || 0));
      const showBar = entry.status === 'reading' || entry.status === 'uploading' || entry.status === 'done';
      const bar = showBar ? '<span class="upload-bar"><span style="width:' + progress + '%"></span></span>' : '';
      const error = entry.status === 'failed' && entry.message
        ? '<p class="upload-error">' + esc(entry.message) + '</p>'
        : '';

      return '<li class="upload-item is-' + entry.status + '">' +
        '<div class="upload-line">' +
          '<span class="upload-name" title="' + esc(entry.name) + '">' + esc(entry.name) + '</span>' +
          '<span class="upload-state">' + esc(label) + '</span>' +
        '</div>' + bar + error +
      '</li>';
    }).join('');
  }

  function closeUploadPanel() {
    window.clearTimeout(state.uploadTimer);
    state.uploadTimer = null;
    $('uploadPanel').hidden = true;
    if (!state.uploadsBusy) {
      state.uploadEntries = [];
      $('uploadList').innerHTML = '';
    }
  }

  async function handleFiles(fileList) {
    if (uploadsBlocked()) {
      showUploadsDisabled();
      return;
    }
    if (state.offline) {
      flash('The local server is unreachable. Start it, then retry.', 'error');
      return;
    }
    if (state.uploadsBusy) {
      flash('An upload is already in progress.', 'warn');
      return;
    }

    let files = Array.prototype.slice.call(fileList || []).filter((file) => file && typeof file.name === 'string');
    if (!files.length) return;
    if (files.length > MAX_FILES) {
      flash('Up to 20 files at once — the first 20 will be added.', 'warn');
      files = files.slice(0, MAX_FILES);
    }

    state.uploadsBusy = true;
    state.lastFailedFiles = [];
    const entries = files.map((file) => ({
      file: file,
      name: file.name,
      size: Number(file.size) || 0,
      status: 'queued',
      progress: 0,
      message: '',
      retryable: false
    }));
    state.uploadEntries = entries;

    $('uploadPanel').hidden = false;
    $('uploadPanel').setAttribute('aria-busy', 'true');
    $('uploadTitle').textContent = 'Adding ' + files.length + ' ' + (files.length === 1 ? 'document' : 'documents');
    $('uploadSummary').textContent = 'Preparing files…';
    $('uploadRetry').hidden = true;
    window.clearTimeout(state.uploadTimer);
    renderUploadList();

    const added = [];

    try {
      for (let index = 0; index < entries.length; index += 1) {
        const entry = entries[index];
        $('uploadSummary').textContent = 'File ' + (index + 1) + ' of ' + entries.length + ' · ' + entry.name;

        if (entry.size === 0) {
          entry.status = 'failed';
          entry.message = 'This file is empty.';
          renderUploadList();
          continue;
        }

        const info = classifyFile(entry.file);
        if (!info.ok) {
          entry.status = 'failed';
          entry.message = info.message;
          renderUploadList();
          continue;
        }

        entry.status = 'reading';
        entry.progress = 4;
        renderUploadList();

        try {
          const payload = await fileToPayload(entry.file, info.mime, (percent) => {
            entry.progress = Math.min(30, Math.max(4, Math.round(percent * 0.3)));
            scheduleUploadRender();
          });
          entry.status = 'uploading';
          entry.progress = 30;
          renderUploadList();

          const data = await ingestFile(payload, (percent) => {
            entry.progress = Math.min(100, 30 + Math.round(percent * 0.7));
            scheduleUploadRender();
          });
          entry.status = 'done';
          entry.progress = 100;
          const doc = data && Array.isArray(data.docs) ? data.docs[0] : null;
          if (doc) added.push(doc);
          renderUploadList();
        } catch (err) {
          entry.status = 'failed';
          entry.message = err && err.message ? err.message : 'Upload failed.';
          entry.retryable = !!(err && UPLOAD_RETRY_STATUSES.indexOf(err.status) !== -1);
          if (entry.retryable) state.lastFailedFiles.push(entry.file);
          if (err && err.code === 'network') setOffline(true, err.message);
          renderUploadList();
        }
      }
    } finally {
      state.uploadsBusy = false;
    }

    const failed = entries.filter((entry) => entry.status === 'failed').length;
    const succeeded = entries.length - failed;
    $('uploadPanel').setAttribute('aria-busy', 'false');
    $('uploadTitle').textContent = failed ? 'Upload finished with errors' : 'Upload complete';
    $('uploadSummary').textContent = failed
      ? 'Added ' + succeeded + ' of ' + entries.length + '. ' + failed + ' failed.'
      : 'Added ' + added.length + ' ' + (added.length === 1 ? 'document' : 'documents') + ' to your desk.';
    $('uploadRetry').hidden = state.lastFailedFiles.length === 0;
    renderUploadList();

    if (added.length) {
      await refreshDocsQuiet();
      refreshStatsQuiet();
      const first = added[0];
      if (first && first.id) await selectDoc(first.id, { preloaded: first });
    }

    if (!failed) {
      state.uploadTimer = window.setTimeout(() => {
        if (!state.uploadsBusy) closeUploadPanel();
      }, 8000);
    }
  }

  function startUploads(files) {
    handleFiles(files).catch((err) => {
      state.uploadsBusy = false;
      $('uploadPanel').setAttribute('aria-busy', 'false');
      $('uploadSummary').textContent = 'The upload stopped unexpectedly.';
      flash(err && err.message ? err.message : 'The upload stopped unexpectedly.', 'error');
    });
  }

  async function retryFailedUploads() {
    if (state.uploadsBusy) return;
    const files = state.lastFailedFiles.slice();
    state.lastFailedFiles = [];
    if (!files.length) return;
    startUploads(files);
  }

  function hasFilePayload(event) {
    const transfer = event && event.dataTransfer;
    if (!transfer) return false;
    if (transfer.types) {
      try {
        return Array.prototype.indexOf.call(transfer.types, 'Files') !== -1;
      } catch (err) {
        return true;
      }
    }
    return true;
  }

  function showDropOverlay() {
    if (state.dragDepth > 0 && !uploadsBlocked() && !state.offline) {
      $('dropOverlay').hidden = false;
    }
  }

  function hideDropOverlay() {
    state.dragDepth = 0;
    $('dropOverlay').hidden = true;
  }

  /* ------------------------------------------------------------------ delete */

  function askConfirm(options) {
    const opts = options || {};
    state.confirmAction = typeof opts.onConfirm === 'function' ? opts.onConfirm : null;
    state.confirmLabel = opts.label || 'Delete';
    state.confirmReturn = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    $('confirmText').textContent = opts.message || 'Delete this?';
    $('confirmYes').textContent = state.confirmLabel;
    $('confirmYes').disabled = false;
    $('confirmNo').disabled = false;
    $('confirmBar').hidden = false;
    $('confirmNo').focus();
  }

  function hideConfirm() {
    $('confirmBar').hidden = true;
    state.confirmAction = null;
    state.confirmBusy = false;
    const returnTo = state.confirmReturn;
    state.confirmReturn = null;
    if (returnTo && returnTo.isConnected) {
      try {
        returnTo.focus();
      } catch (err) {
        /* ignore */
      }
    }
  }

  function confirmDeleteDocument(id, title) {
    if (!id) return;
    askConfirm({
      message: 'Delete “' + (title || 'this document') + '” from your desk? The stored pages and files will be removed.',
      label: 'Delete document',
      onConfirm: async () => {
        try {
          await api('/api/docs/' + encodeURIComponent(id), { method: 'DELETE', timeoutMs: 30000 });
        } catch (err) {
          if (!err || err.status !== 404) throw err;
        }
        if (state.activeId === id) {
          state.docs = state.docs.filter((doc) => doc.id !== id);
          clearSelection();
        } else {
          state.docs = state.docs.filter((doc) => doc.id !== id);
          renderDocList();
        }
        await refreshDocsQuiet();
        refreshStatsQuiet();
        flash('Deleted “' + (title || 'document') + '” from this machine.', 'info');
      }
    });
  }

  function confirmDeleteAll() {
    const count = state.docs.length;
    if (!count) {
      flash('There’s nothing to delete.', 'info');
      return;
    }
    askConfirm({
      message: 'Delete all ' + count + ' document' + (count === 1 ? '' : 's') +
        '? Stored pages and files are removed from this machine. This can’t be undone.',
      label: 'Delete everything',
      onConfirm: async () => {
        await api('/api/docs', { method: 'DELETE', timeoutMs: 60000 });
        state.docs = [];
        clearSelection();
        await refreshDocsQuiet();
        refreshStatsQuiet();
        flash('All documents deleted from this machine.', 'info');
      }
    });
  }

  async function onConfirmYes() {
    if (!state.confirmAction || state.confirmBusy) return;
    state.confirmBusy = true;
    const action = state.confirmAction;
    const yes = $('confirmYes');
    const no = $('confirmNo');
    yes.disabled = true;
    no.disabled = true;
    yes.textContent = 'Deleting…';
    try {
      await action();
    } catch (err) {
      flash(err && err.message ? err.message : 'Delete failed.', 'error');
      if (err && err.code === 'network') setOffline(true, err.message);
    } finally {
      yes.disabled = false;
      no.disabled = false;
      yes.textContent = state.confirmLabel;
      hideConfirm();
    }
  }

  /* ------------------------------------------------------------------ flash */

  function hideFlash() {
    window.clearTimeout(state.toastTimer);
    state.toastTimer = null;
    $('toast').hidden = true;
  }

  function flash(message, tone) {
    const box = $('toast');
    $('toastText').textContent = message;
    box.className = 'toast toast-' + (tone || 'info');
    box.hidden = false;
    window.clearTimeout(state.toastTimer);
    const duration = tone === 'error' ? 10000 : tone === 'warn' ? 8000 : 5500;
    state.toastTimer = window.setTimeout(hideFlash, duration);
  }

  /* ----------------------------------------------------------------- dialog */

  function openHowDialog() {
    const dialog = $('howDialog');
    if (typeof dialog.showModal === 'function') {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
    const close = dialog.querySelector('.dialog-close');
    if (close) {
      try {
        close.focus();
      } catch (err) {
        /* ignore */
      }
    }
  }

  function closeHowDialog() {
    const dialog = $('howDialog');
    if (typeof dialog.close === 'function' && dialog.open) dialog.close();
    else dialog.removeAttribute('open');
    const trigger = $('howBtn');
    if (trigger) {
      try {
        trigger.focus();
      } catch (err) {
        /* ignore */
      }
    }
  }

  /* --------------------------------------------------------------- keyboard */

  function isTypingTarget(target) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'PRE' || tag === 'BLOCKQUOTE' || !!target.isContentEditable;
  }

  function onKeydown(event) {
    if (event.isComposing) return;
    const dialog = $('howDialog');
    if (event.key === 'Escape') {
      if (dialog.open) {
        event.preventDefault();
        closeHowDialog();
        return;
      }
      if (!$('confirmBar').hidden) {
        hideConfirm();
        return;
      }
      if (!$('citeBar').hidden) {
        hideCiteBar();
        return;
      }
      if (!$('dropOverlay').hidden) {
        hideDropOverlay();
        return;
      }
      return;
    }

    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      if (dialog.open) return;
      event.preventDefault();
      if (!$('confirmBar').hidden) return;
      runAnalyze();
      return;
    }

    if (dialog.open || !$('confirmBar').hidden) return;
    if (isTypingTarget(event.target)) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key >= '1' && event.key <= '4') {
      if (!state.doc) return;
      event.preventDefault();
      setMode(MODES[Number(event.key) - 1]);
      return;
    }

    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      if (!state.doc) return;
      event.preventDefault();
      turnPage(event.key === 'ArrowRight' ? 1 : -1);
    }
  }

  /* ------------------------------------------------------------------ wire */

  function wire() {
    $('uploadBtn').addEventListener('click', openFilePicker);
    $('chooseFilesBtn').addEventListener('click', openFilePicker);
    $('fileInput').addEventListener('change', (event) => {
      const files = event.target.files ? Array.prototype.slice.call(event.target.files) : [];
      event.target.value = '';
      if (files.length) startUploads(files);
    });

    $('deleteAllBtn').addEventListener('click', confirmDeleteAll);
    $('privacyDelete').addEventListener('click', confirmDeleteAll);
    $('retryBoot').addEventListener('click', () => { boot({ initial: false }); });
    $('howBtn').addEventListener('click', openHowDialog);

    $('uploadClose').addEventListener('click', closeUploadPanel);
    $('uploadRetry').addEventListener('click', retryFailedUploads);
    $('toastClose').addEventListener('click', hideFlash);

    $('confirmYes').addEventListener('click', onConfirmYes);
    $('confirmNo').addEventListener('click', hideConfirm);

    $('prevPage').addEventListener('click', () => turnPage(-1));
    $('nextPage').addEventListener('click', () => turnPage(1));
    $('citeClose').addEventListener('click', hideCiteBar);

    modeElementList().forEach((button) => {
      button.addEventListener('click', () => setMode(button.getAttribute('data-mode')));
    });

    $('langSelect').addEventListener('change', () => {
      const lang = $('langSelect').value;
      setLanguage(lang);
      if (state.doc) {
        resetAnalysisUI('Language set to ' + (LANG_NAMES[lang] || lang) + '. Press Analyze for an answer in that language.');
      }
    });

    $('question').addEventListener('input', () => {
      if (!$('askError').hidden) hideAskError();
    });
    $('question').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey && !event.isComposing) {
        event.preventDefault();
        runAnalyze();
      }
    });

    $('analyzeBtn').addEventListener('click', runAnalyze);

    $('analysisError').addEventListener('click', (event) => {
      const target = eventElement(event);
      const button = target ? target.closest('[data-error-action]') : null;
      if (!button) return;
      const action = button.getAttribute('data-error-action');
      if (action === 'retry') {
        runAnalyze();
      } else if (action === 'use-english') {
        setLanguage('en');
        runAnalyze();
      } else if (action === 'load-sample') {
        const sample = document.querySelector('.sidebar [data-sample]') || document.querySelector('[data-sample]');
        if (sample) {
          try {
            sample.scrollIntoView({ block: 'center', behavior: 'smooth' });
          } catch (err) {
            /* ignore */
          }
          sample.focus();
        }
      } else if (action === 'refresh-docs') {
        refreshDocsQuiet();
      }
    });

    $('resultMeta').addEventListener('click', (event) => {
      const target = eventElement(event);
      const button = target ? target.closest('[data-action="speak"]') : null;
      if (button) toggleSpeak();
    });

    $('resultBody').addEventListener('click', (event) => {
      const target = eventElement(event);
      if (!target) return;
      const chip = target.closest('[data-cite]');
      if (chip) {
        const index = Number(chip.getAttribute('data-cite'));
        if (Number.isInteger(index) && state.citationRefs[index]) {
          handleCitation(state.citationRefs[index]);
        }
        return;
      }
      const action = target.closest('[data-action]');
      if (!action) return;
      const name = action.getAttribute('data-action');
      if (name === 'copy-letter') copyLetter(action);
      else if (name === 'download-letter') downloadLetter();
    });

    $('docList').addEventListener('click', (event) => {
      const target = eventElement(event);
      if (!target) return;
      const deleteButton = target.closest('[data-delete-id]');
      if (deleteButton) {
        const id = deleteButton.getAttribute('data-delete-id');
        const doc = state.docs.find((item) => item.id === id);
        confirmDeleteDocument(id, doc ? doc.title : 'this document');
        return;
      }
      const selectButton = target.closest('[data-doc-id]');
      if (selectButton) {
        const id = selectButton.getAttribute('data-doc-id');
        if (id && (id !== state.activeId || !state.doc)) selectDoc(id);
      }
    });

    document.addEventListener('click', (event) => {
      const target = eventElement(event);
      if (!target) return;
      const sampleButton = target.closest('[data-sample]');
      if (sampleButton && !sampleButton.disabled) {
        loadSample(sampleButton.getAttribute('data-sample'));
      }
    });

    $('sampleError').addEventListener('click', (event) => {
      const target = eventElement(event);
      const button = target ? target.closest('[data-sample-retry]') : null;
      if (!button) return;
      const id = button.getAttribute('data-sample-retry');
      if (id) loadSample(id);
    });

    document.addEventListener('keydown', onKeydown);

    // Drag and drop anywhere over the page. The API takes base64, so we read
    // files in the browser and POST JSON - never multipart.
    window.addEventListener('dragenter', (event) => {
      if (!hasFilePayload(event)) return;
      event.preventDefault();
      state.dragDepth += 1;
      showDropOverlay();
    });
    window.addEventListener('dragover', (event) => {
      if (!hasFilePayload(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = uploadsBlocked() || state.offline ? 'none' : 'copy';
    });
    window.addEventListener('dragleave', (event) => {
      state.dragDepth = Math.max(0, state.dragDepth - 1);
      if (state.dragDepth === 0 || !event.relatedTarget) $('dropOverlay').hidden = true;
    });
    window.addEventListener('drop', (event) => {
      const transfer = event.dataTransfer;
      const looksLikeFiles = !!(transfer && (
        (transfer.files && transfer.files.length) || hasFilePayload(event)
      ));
      if (!looksLikeFiles) return;
      event.preventDefault();
      state.dragDepth = 0;
      $('dropOverlay').hidden = true;
      if (!transfer || !transfer.files || !transfer.files.length) return;
      if (uploadsBlocked()) {
        showUploadsDisabled();
        return;
      }
      if (state.offline) {
        flash('The local server is unreachable. Start it, then retry.', 'error');
        return;
      }
      if (state.uploadsBusy) {
        flash('An upload is already in progress.', 'warn');
        return;
      }
      startUploads(transfer.files);
    });
    window.addEventListener('dragend', hideDropOverlay);

    const howDialog = $('howDialog');
    howDialog.addEventListener('click', (event) => {
      if (event.target === howDialog) closeHowDialog();
    });

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && !state.booting) refreshStatsQuiet();
    });

    window.addEventListener('beforeunload', () => {
      if ('speechSynthesis' in window) {
        try {
          window.speechSynthesis.cancel();
        } catch (err) {
          /* ignore */
        }
      }
    });
  }

  function startHeartbeat() {
    window.setInterval(async () => {
      if (document.hidden || state.booting) return;
      const ok = await refreshStatsQuiet();
      if (ok) {
        if (state.offline) await boot({ initial: false });
      } else if (!state.offline) {
        try {
          await api('/api/health', { timeoutMs: 8000 });
        } catch (err) {
          setOffline(true, err.message);
        }
      }
    }, 30000);
  }

  /* ------------------------------------------------------------------- init */

  wire();
  updateCalls(0, 0);
  syncUploadAffordances();
  renderDocList();
  updateModeTabs();
  updateAnalyzeAvailability();
  boot({ initial: true });
  startHeartbeat();
})();
