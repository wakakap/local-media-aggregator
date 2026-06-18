import { appState, playerState, viewerState, textState } from './state.js';
import * as api from './api.js';
function safeEncodePath(path) { return path ? path.split('/').map(encodeURIComponent).join('/') : ''; } // パスのエンコード処理
let epubBook = null, epubRendition = null; // Epub関連変数
const mediaContainer = document.getElementById('media-container'), breadcrumbContainer = document.getElementById('breadcrumb-container');
const cardTemplate = document.getElementById('card-template'), fileRowTemplate = document.getElementById('file-row-template');
const loadingIndicator = document.getElementById('loading-indicator'), tagFilterContainer = document.getElementById('tag-filter-container');
const directoryHeader = document.getElementById('directory-header'), headerCoverImg = document.getElementById('header-cover-img');
const headerTitle = document.getElementById('header-title'), headerTags = document.getElementById('header-tags');
const imageViewer = document.getElementById('image-viewer'), currentImage = document.getElementById('current-image');
const progressBarContainer = document.getElementById('progress-bar-container'), videoPlayer = document.getElementById('video-player');
const currentVideo = document.getElementById('current-video'), epubViewer = document.getElementById('epub-viewer');
const musicPlayerBar = document.getElementById('music-player-bar'), audioEl = document.getElementById('current-audio-player');
const videoSubtitleContainer = document.getElementById('video-subtitle-container'), audioLyricsContainer = document.getElementById('audio-lyrics-container');
const audioLyricsText = document.getElementById('audio-lyrics-text');
let currentSubtitles = [], _cachedTagCounts = null, _lastTagsDataRef = null, _lastMode = null; // 状態キャッシュ用変数
let preloadObserver = null, readObserver = null, visibleRatios = {}, isJumping = false, jumpTimeout = null; // オブザーバーとスクロール制御
let audioContext = null, analyser = null, dataArray = null, source = null, animationId = null, isVisualizerInited = false, stars = []; // ビジュアライザー変数
const VISUALIZER_CONFIG = { barCount: 64, starCount: 50 }; // ビジュアライザー設定
export function showLoading(show) { loadingIndicator.style.display = show ? 'block' : 'none'; } // ローディング表示切替
export function renderContent(data) { // メインコンテンツの描画
    appState.currentDataSet = data;
    mediaContainer.innerHTML = '';
    if (appState.inSearchMode || data.is_root) { mediaContainer.className = 'grid-view'; directoryHeader.style.display = 'none'; tagFilterContainer.style.display = 'block'; }
    else { mediaContainer.className = 'list-view'; tagFilterContainer.style.display = 'none'; renderDirectoryHeader(data.metadata); }
    renderBreadcrumbs(appState.inSearchMode ? 'SEARCH' : data.current_path, data.breadcrumbs);
    if (!data.items || data.items.length === 0) {
        const msg = document.createElement('div'); msg.style.padding = '20px'; msg.style.color = '#888'; msg.textContent = 'このフォルダは空です'; mediaContainer.appendChild(msg);
        return;
    }
    viewerState.fileList = data.items.filter(i => !i.is_dir && ['image', 'video', 'audio', 'subtitle'].includes(i.media_type));
    const CHUNK_SIZE = 50; let currentIndex = 0; // チャンク分割描画による最適化
    function renderChunk() {
        const end = Math.min(currentIndex + CHUNK_SIZE, data.items.length), fragment = document.createDocumentFragment();
        for (let i = currentIndex; i < end; i++) { mediaContainer.className === 'grid-view' ? renderCard(data.items[i], fragment) : renderFileRow(data.items[i], fragment); }
        mediaContainer.appendChild(fragment);
        currentIndex = end;
        if (currentIndex < data.items.length) requestAnimationFrame(renderChunk);
    }
    renderChunk();
}
function getTagCounts() { // タグの出現回数集計
    if (_cachedTagCounts && _lastTagsDataRef === appState.tagsData && _lastMode === appState.mode) return _cachedTagCounts;
    const counts = {};
    Object.keys(appState.tagsData).forEach(k => { if (k.startsWith(appState.mode)) appState.tagsData[k].forEach(t => { if (!t.startsWith('*')) counts[t] = (counts[t] || 0) + 1; }); });
    _cachedTagCounts = counts; _lastTagsDataRef = appState.tagsData; _lastMode = appState.mode;
    return counts;
}
function renderDirectoryHeader(metadata) { // フォルダヘッダー描画
    if (metadata && (metadata.cover_filename || (metadata.tags && metadata.tags.length > 0) || appState.isTagEditMode)) {
        directoryHeader.style.display = 'flex'; headerTitle.textContent = metadata.name;
        if (metadata.cover_filename) { headerCoverImg.src = metadata.cover_source === 'local' ? `/api/media/${appState.mode}/pages/${safeEncodePath(metadata.cover_filename)}` : `/api/media/${appState.mode}/cover/${safeEncodePath(metadata.cover_filename)}`; headerCoverImg.parentElement.style.display = 'block'; }
        else { headerCoverImg.src = ''; headerCoverImg.parentElement.style.display = 'none'; }
        renderTags(headerTags, metadata);
    } else directoryHeader.style.display = 'none';
}
function renderBreadcrumbs(path, breadcrumbsData) { // パンくずリスト描画
    breadcrumbContainer.innerHTML = '';
    if (path === 'SEARCH') {
        const rSpan = document.createElement('span'); rSpan.className = 'breadcrumb-item'; rSpan.textContent = 'ROOT'; rSpan.onclick = () => window.dispatchEvent(new CustomEvent('browse-root'));
        const sep = document.createElement('span'); sep.className = 'breadcrumb-separator'; sep.textContent = '>';
        const cSpan = document.createElement('span'); cSpan.className = 'breadcrumb-current'; cSpan.textContent = `検索: "${appState.searchQuery}"`;
        breadcrumbContainer.append(rSpan, sep, cSpan); return;
    }
    if (breadcrumbsData && breadcrumbsData.length > 0) {
        breadcrumbsData.forEach((crumb, index) => {
            if (index > 0) { const sep = document.createElement('span'); sep.className = 'breadcrumb-separator'; sep.textContent = '>'; breadcrumbContainer.appendChild(sep); }
            const span = document.createElement('span');
            if (index === breadcrumbsData.length - 1) { span.className = 'breadcrumb-current'; span.textContent = crumb.name; }
            else { span.className = 'breadcrumb-item'; span.textContent = crumb.name; crumb.name === 'ROOT' ? span.onclick = () => window.dispatchEvent(new CustomEvent('browse-root')) : span.onclick = () => window.dispatchEvent(new CustomEvent('browse-path', { detail: crumb.path })); }
            breadcrumbContainer.appendChild(span);
        });
    } else { const span = document.createElement('span'); span.className = 'breadcrumb-item'; span.textContent = 'ROOT'; span.onclick = () => window.dispatchEvent(new CustomEvent('browse-root')); breadcrumbContainer.appendChild(span); }
}
function renderCard(item, targetElement = mediaContainer) { // グリッドカードの描画
    const clone = cardTemplate.content.cloneNode(true), card = clone.querySelector('.card'), img = clone.querySelector('img'), nameEl = clone.querySelector('.card-filename'), tagsEl = clone.querySelector('.card-tags');
    card.dataset.path = item.full_path; card.dataset.isDir = item.is_dir;
    nameEl.textContent = `${item.is_dir ? '📁' : (item.media_type === 'video' ? '🎬' : '📄')} ${item.name}`;
    if (item.thumbnail_filename) { img.src = `/api/media/${appState.mode}/thumbnail/${safeEncodePath(item.thumbnail_filename)}`; img.style.display = 'block'; }
    else if (item.cover_filename) { img.src = item.cover_source === 'local' ? `/api/media/${appState.mode}/pages/${safeEncodePath(item.thumbnail_filename)}` : `/api/media/${appState.mode}/cover/${safeEncodePath(item.thumbnail_filename)}`; img.style.display = 'block'; }
    else { img.style.display = 'none'; clone.querySelector('.placeholder-text').textContent = item.name_no_ext; }
    renderTags(tagsEl, item);
    clone.querySelector('.card-image-wrapper').onclick = (e) => { e.stopPropagation(); handleItemClick(item); };
    card.ondblclick = () => { if (appState.isTagEditMode) enableRename(card, item, nameEl); };
    targetElement.appendChild(clone);
}
function renderFileRow(item, targetElement = mediaContainer) { // リスト行の描画
    const clone = fileRowTemplate.content.cloneNode(true), row = clone.querySelector('.file-row'), icon = clone.querySelector('.file-icon'), name = clone.querySelector('.file-name');
    icon.textContent = item.is_dir ? '📁' : (item.media_type === 'image' ? '🖼️' : (item.media_type === 'video' ? '🎬' : (item.media_type === 'audio' ? '🎵' : (item.media_type === 'epub' ? '📘' : '📄'))));
    name.textContent = item.name;
    if (item.media_type === 'audio' && playerState.currentTrack && playerState.currentTrack.full_path === item.full_path) row.classList.add('playing');
    row.onclick = () => handleItemClick(item);
    targetElement.appendChild(clone);
}
export function renderTags(container, item) { // タグの描画
    container.innerHTML = '';
    const compositeKey = `${appState.mode}:${item.is_dir ? item.media_path : item.media_path.replace(/\.[^/.]+$/, "")}`;
    const tagsSource = appState.isTagEditMode ? appState.tempTagsData : appState.tagsData, tags = tagsSource[compositeKey] || [];
    let visibleTags = tags.filter(t => !t.startsWith('*')); const counts = getTagCounts();
    visibleTags.sort((a, b) => { const cA = counts[a] || 0, cB = counts[b] || 0; return cB !== cA ? cB - cA : a.localeCompare(b); });
    if (appState.isTagEditMode) {
        visibleTags.forEach(tag => {
            const unit = document.createElement('div'); unit.className = 'tag-edit-unit'; unit.innerHTML = `<span style="padding:0 4px; color:white; font-size:11px;">${tag}</span><span class="del-tag-btn">×</span>`;
            unit.querySelector('.del-tag-btn').onclick = (e) => { e.stopPropagation(); appState.tempTagsData[compositeKey] = appState.tempTagsData[compositeKey].filter(t => t !== tag); renderTags(container, item); };
            container.appendChild(unit);
        });
        const addBtn = document.createElement('span'); addBtn.className = 'add-tag-btn'; addBtn.textContent = '+';
        addBtn.onclick = (e) => {
            e.stopPropagation(); const newTag = prompt(`"${item.name_no_ext}" に新しいタグを追加:`);
            if(newTag) { if(!appState.tempTagsData[compositeKey]) appState.tempTagsData[compositeKey] = []; if (!appState.tempTagsData[compositeKey].includes(newTag)) { appState.tempTagsData[compositeKey].push(newTag); renderTags(container, item); } }
        };
        container.appendChild(addBtn);
    } else {
        visibleTags.forEach(tag => {
            const span = document.createElement('span'); span.className = 'card-tag'; span.textContent = tag; span.style.backgroundColor = appState.selectedTags.includes(tag) ? '#D9534F' : '#555';
            span.onclick = (e) => { e.stopPropagation(); window.dispatchEvent(new CustomEvent('toggle-tag', { detail: tag })); }; container.appendChild(span);
        });
    }
}
async function handleItemClick(item) { // ファイル/フォルダのクリック制御
    if (item.is_dir) { api.recordView(item.media_path, appState.mode, null); window.dispatchEvent(new CustomEvent('browse-path', { detail: item.full_path })); return; }
    switch (item.media_type) { case 'image': openImageViewer(item); break; case 'video': openVideoPlayer(item); break; case 'audio': playAudio(item); break; case 'epub': openEpubViewer(item); break; case 'text': openTextViewer(item); break; }
}
export function openImageViewer(item) { // 画像ビューア起動
    const images = viewerState.fileList.filter(i => i.media_type === 'image'); let index = images.findIndex(i => i.full_path === item.full_path); if (index === -1) index = 0;
    viewerState.currentIndex = index; viewerState.images = images; imageViewer.classList.add('active'); buildProgressBar(images.length); ensureZoomControls();
    const container = document.querySelector('.image-container'); container.innerHTML = '';
    images.forEach((imgItem, i) => { // DOMを遅延ロードで作成
        const imgEl = document.createElement('img'); imgEl.className = 'viewer-img is-loading'; imgEl.dataset.index = i; imgEl.dataset.src = `/api/media/${appState.mode}/pages/${safeEncodePath(imgItem.media_path)}`;
        imgEl.onload = function() { this.classList.remove('is-loading'); this.style.setProperty('--nat-w', this.naturalWidth); };
        imgEl.onerror = function() {
            if (!this.dataset.retried) { this.dataset.retried = 'true'; setTimeout(() => { this.src = this.dataset.src + '?t=' + new Date().getTime(); }, 1000); }
            else { this.classList.remove('is-loading'); this.style.backgroundColor = '#2a2a2a'; this.alt = '❌ 画像リソースの損失またはネットワークタイムアウト'; }
        };
        container.appendChild(imgEl);
    });
    setupIntersectionObserver(); jumpToImage(index);
}
function ensureZoomControls() { // ズームUIの確保
    let controls = document.getElementById('image-zoom-controls');
    if (!controls) {
        controls = document.createElement('div'); controls.id = 'image-zoom-controls';
        controls.innerHTML = `<span class="zoom-btn" id="zoom-out-btn">－</span><input type="range" id="zoom-slider" min="0.1" max="4.0" step="0.05" value="1.0"><span class="zoom-btn" id="zoom-in-btn">＋</span><button id="zoom-fit-btn" class="button" style="padding: 2px 8px; margin-left: 10px; font-size: 12px;">画面に合わせる</button>`;
        document.getElementById('image-viewer').appendChild(controls);
        const slider = document.getElementById('zoom-slider'); slider.addEventListener('input', (e) => setGlobalZoom(parseFloat(e.target.value), 'center'));
        document.getElementById('zoom-out-btn').onclick = (e) => { e.stopPropagation(); slider.value = Math.max(parseFloat(slider.min), parseFloat(slider.value) - 0.1); setGlobalZoom(parseFloat(slider.value), 'center'); };
        document.getElementById('zoom-in-btn').onclick = (e) => { e.stopPropagation(); slider.value = Math.min(parseFloat(slider.max), parseFloat(slider.value) + 0.1); setGlobalZoom(parseFloat(slider.value), 'center'); };
        document.getElementById('zoom-fit-btn').onclick = (e) => { e.stopPropagation(); autoFitImage(viewerState.currentIndex); };
        controls.onclick = (e) => e.stopPropagation();
    }
}
function setGlobalZoom(scale, align = 'center') { // 全体ズーム処理
    const container = document.querySelector('.image-container'); if (!container) return;
    const currentImg = container.querySelector(`.viewer-img[data-index="${viewerState.currentIndex}"]`); let relativeCenterY = 0.5;
    if (currentImg && align === 'center') {
        const imgRect = currentImg.getBoundingClientRect(), containerRect = container.getBoundingClientRect(), viewCenterY = containerRect.top + (containerRect.height / 2);
        relativeCenterY = Math.max(0, Math.min(1, (viewCenterY - imgRect.top) / imgRect.height));
    }
    container.style.setProperty('--global-zoom', scale);
    if (currentImg) {
        if (align === 'start') container.scrollTop = currentImg.offsetTop;
        else { const newImgRect = currentImg.getBoundingClientRect(), viewCenterY = container.getBoundingClientRect().top + (container.getBoundingClientRect().height / 2); container.scrollTop += ((newImgRect.top + (newImgRect.height * relativeCenterY)) - viewCenterY); }
    }
    const slider = document.getElementById('zoom-slider'); if (slider && slider.value != scale) slider.value = scale;
}
function autoFitImage(index) { // 画像自動フィット処理
    const container = document.querySelector('.image-container'), targetImg = container.querySelector(`.viewer-img[data-index="${index}"]`); if (!targetImg) return;
    const calculateAndApply = () => { const limits = calculateZoomLimits(targetImg, container), slider = document.getElementById('zoom-slider'); if (slider) { slider.min = limits.min; slider.max = limits.max; } setGlobalZoom(limits.min, 'start'); };
    targetImg.complete && targetImg.naturalWidth ? calculateAndApply() : targetImg.addEventListener('load', calculateAndApply, { once: true });
}
function calculateZoomLimits(targetImg, container) { // 限界ズーム倍率計算
    const vw = container.clientWidth - 10, vh = container.clientHeight - 10, nw = targetImg.naturalWidth, nh = targetImg.naturalHeight;
    if (!nw || !nh) return { min: 1, max: 4 };
    const scaleX = vw / nw, scaleY = vh / nh; let minScale = Math.min(scaleX, scaleY), maxScale = scaleX;
    if (maxScale <= minScale) maxScale = Math.max(minScale * 2.0, 1.0);
    return { min: Math.floor(minScale * 100) / 100, max: Math.ceil(maxScale * 100) / 100 };
}
function setupIntersectionObserver() { // 画像ロード用交差監視
    if (preloadObserver) preloadObserver.disconnect(); if (readObserver) readObserver.disconnect();
    const container = document.querySelector('.image-container');
    preloadObserver = new IntersectionObserver((entries) => { entries.forEach(entry => { if (entry.isIntersecting && !entry.target.src) entry.target.src = entry.target.dataset.src; }); }, { root: container, rootMargin: '100% 0px 100% 0px', threshold: 0 });
    readObserver = new IntersectionObserver((entries) => {
        if (isJumping) return;
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target, bestIndex = parseInt(img.dataset.index);
                if (bestIndex !== viewerState.currentIndex) {
                    viewerState.currentIndex = bestIndex; updateProgressBar(bestIndex); recordImageView(bestIndex);
                    if (img.complete) { const limits = calculateZoomLimits(img, container), slider = document.getElementById('zoom-slider'); if (slider) { slider.min = limits.min; slider.max = limits.max; const currentZoom = parseFloat(slider.value); if (currentZoom > limits.max) setGlobalZoom(limits.max, 'center'); if (currentZoom < limits.min) setGlobalZoom(limits.min, 'center'); } }
                }
            }
        });
    }, { root: container, rootMargin: '-40% 0px -40% 0px', threshold: 0 });
    container.querySelectorAll('.viewer-img').forEach(img => { preloadObserver.observe(img); readObserver.observe(img); });
}
function recordImageView(index) { // 画像表示履歴記録
    const item = viewerState.images[index]; if (!item) return;
    const parts = item.media_path.split(/[/\\]/); parts.pop(); const galleryKey = parts.join('/');
    if (galleryKey) api.recordView(galleryKey, appState.mode, index);
}
function jumpToImage(index) { // 指定画像へジャンプ
    const container = document.querySelector('.image-container'), targetImg = container.querySelector(`.viewer-img[data-index="${index}"]`); if (!targetImg) return;
    const doScroll = () => { isJumping = true; clearTimeout(jumpTimeout); autoFitImage(index); targetImg.scrollIntoView({ block: 'start' }); jumpTimeout = setTimeout(() => { isJumping = false; }, 150); };
    [index - 1, index, index + 1].forEach(i => { const img = container.querySelector(`.viewer-img[data-index="${i}"]`); if (img && !img.getAttribute('src')) img.src = img.dataset.src; });
    !targetImg.complete || !targetImg.naturalWidth ? targetImg.addEventListener('load', doScroll, { once: true }) : doScroll();
    updateProgressBar(index); recordImageView(index);
}
function buildProgressBar(count) { // プログレスバー生成
    progressBarContainer.innerHTML = '';
    for (let i = 0; i < count; i++) {
        const wrapper = document.createElement('div'); wrapper.className = 'progress-segment-wrapper'; wrapper.dataset.index = i;
        const label = document.createElement('span'); label.className = 'progress-label';
        if ((i + 1) % 10 === 0 && count > 10) { wrapper.style.flexGrow = 2.5; label.textContent = i + 1; }
        const segment = document.createElement('div'); segment.className = 'progress-segment';
        wrapper.append(label, segment); progressBarContainer.appendChild(wrapper);
        wrapper.addEventListener('click', (e) => { e.stopPropagation(); viewerState.currentIndex = i; updateImageDisplay(); });
    }
}
function updateProgressBar(index) { // プログレスバー更新
    const wrappers = progressBarContainer.children;
    for (let i = 0; i < wrappers.length; i++) { const segment = wrappers[i].querySelector('.progress-segment'); if (segment) i === index ? segment.classList.add('active') : segment.classList.remove('active'); }
    if(wrappers[index]) wrappers[index].scrollIntoView({block: "center", behavior: "smooth"});
}
export function nextImage() { if (viewerState.currentIndex < viewerState.images.length - 1) { viewerState.currentIndex++; jumpToImage(viewerState.currentIndex); } }
export function prevImage() { if (viewerState.currentIndex > 0) { viewerState.currentIndex--; jumpToImage(viewerState.currentIndex); } }
export function updateImageDisplay() { jumpToImage(viewerState.currentIndex); }
function openVideoPlayer(item) { // ビデオプレイヤー起動
    videoPlayer.classList.add('active'); recordFileAccess(item); currentVideo.src = `/api/media/${appState.mode}/pages/${safeEncodePath(item.media_path)}`;
    videoSubtitleContainer.innerHTML = ''; currentSubtitles = []; currentVideo.ontimeupdate = null;
    loadSubtitles(item, videoSubtitleContainer).then(() => startSubtitleSync(currentVideo, videoSubtitleContainer));
}
export function toggleVideoSubtitle(videoEl, btnEl) { // 字幕トグル
    if (!videoEl.textTracks || videoEl.textTracks.length === 0) return; const track = videoEl.textTracks[0];
    if (track.mode === 'showing') { track.mode = 'hidden'; btnEl.textContent = '字幕: オフ'; btnEl.style.opacity = '0.5'; } else { track.mode = 'showing'; btnEl.textContent = '字幕: オン'; btnEl.style.opacity = '1'; }
}
function playAudio(item) { // 音声プレイ開始
    const audioFiles = viewerState.fileList.filter(i => i.media_type === 'audio'); playerState.playlist = audioFiles;
    playerState.currentIndex = audioFiles.findIndex(i => i.full_path === item.full_path); playerState.currentTrack = item; loadAndPlayAudio();
}
function loadAndPlayAudio() { // 音声ソース読み込みと再生
    const item = playerState.playlist[playerState.currentIndex]; if (!item) return; resetRatingPanel();
    playerState.currentTrack = item; recordFileAccess(item); audioEl.src = `/api/media/${appState.mode}/audio/${safeEncodePath(item.media_path)}`;
    loadSubtitles(item, audioLyricsText).then(() => startSubtitleSync(audioEl, audioLyricsText));
    audioEl.play().then(() => { if (audioContext && audioContext.state === 'suspended') audioContext.resume(); }).catch(e => console.error("再生エラー:", e));
    musicPlayerBar.classList.add('active'); updatePlayerUI(); document.querySelectorAll('.file-row').forEach(r => r.classList.remove('playing'));
}
export function updatePlayerUI() { // プレイヤーUI更新
    const info = document.getElementById('player-track-info'); if (playerState.currentTrack) info.textContent = `${playerState.currentIndex + 1}/${playerState.playlist.length} - ${playerState.currentTrack.name}`;
    const loopBtn = document.getElementById('player-loop-btn'); loopBtn.textContent = playerState.repeatMode === 'ONE' ? '🔂' : (playerState.repeatMode === 'ALL' ? '🔁' : '➡️');
    playerState.repeatMode !== 'NONE' ? loopBtn.classList.add('active') : loopBtn.classList.remove('active');
    const shuffleBtn = document.getElementById('player-shuffle-btn'); if (shuffleBtn) playerState.isShuffled ? shuffleBtn.classList.add('active') : shuffleBtn.classList.remove('active');
}
export function nextTrack() { // 次のトラックへ
    if (playerState.playlist.length === 0) return; let nextIdx;
    if (playerState.isShuffled) { do { nextIdx = Math.floor(Math.random() * playerState.playlist.length); } while (playerState.playlist.length > 1 && nextIdx === playerState.currentIndex); }
    else { nextIdx = playerState.currentIndex + 1; if (nextIdx >= playerState.playlist.length) nextIdx = 0; }
    playerState.currentIndex = nextIdx; loadAndPlayAudio();
}
export function prevTrack() { // 前のトラックへ
    if (playerState.playlist.length === 0) return; let prevIdx = playerState.currentIndex - 1; if (prevIdx < 0) prevIdx = playerState.playlist.length - 1;
    playerState.currentIndex = prevIdx; loadAndPlayAudio();
}
function openEpubViewer(item) { // Epubビューア起動
    epubViewer.classList.add('active'); recordFileAccess(item); document.getElementById('epub-area').innerHTML = '';
    if (epubBook) { epubBook.destroy(); epubBook = null; }
    epubBook = ePub(`/api/media/${appState.mode}/pages/${safeEncodePath(item.media_path)}`);
    epubRendition = epubBook.renderTo("epub-area", { width: "100%", height: "100%", flow: "scrolled-doc" }); epubRendition.display();
}
export function epubNext() { if(epubRendition) epubRendition.next(); } // Epub次ページ
export function epubPrev() { if(epubRendition) epubRendition.prev(); } // Epub前ページ
function enableRename(card, item, nameEl) { // インラインリネームUI (完全フロントエンド一時処理)
    const input = document.createElement('textarea'); 
    input.value = item.name_no_ext; 
    input.style.cssText = 'width: 100%; font-family: inherit; font-size: inherit; resize: none;';
    nameEl.innerHTML = ''; nameEl.appendChild(input); input.focus(); input.select();
    const save = () => { // API呼び出しを削除し、フロントエンドの状態のみを更新
        const newBase = input.value.trim(), icon = item.is_dir ? '📁' : (item.media_type === 'video' ? '🎬' : '📄');
        if (newBase && newBase !== item.name_no_ext) {
            let newName = newBase; 
            if (!item.is_dir && item.name.lastIndexOf('.') > 0) newName = newBase + item.name.substring(item.name.lastIndexOf('.'));
            const oldCompositeKey = `${appState.mode}:${item.is_dir ? item.media_path : item.media_path.replace(/\.[^/.]+$/, "")}`;
            // 1. オリジナルパスの記録（連続リネームに対応するため）
            if (!item.original_full_path) item.original_full_path = item.full_path;
            // 2. 変更キューに登録
            appState.pendingRenames[item.original_full_path] = newName;
            // 3. 新しいパス文字列の計算
            const parentPathFull = item.full_path.substring(0, Math.max(item.full_path.lastIndexOf('\\'), item.full_path.lastIndexOf('/')));
            const sepFull = item.full_path.includes('\\') ? '\\' : '/';
            const newFullPath = parentPathFull ? (parentPathFull + sepFull + newName) : newName;
            let parentPathMedia = '';
            const mediaLastSlash = Math.max(item.media_path.lastIndexOf('/'), item.media_path.lastIndexOf('\\'));
            if (mediaLastSlash > -1) parentPathMedia = item.media_path.substring(0, mediaLastSlash);
            const sepMedia = item.media_path.includes('\\') ? '\\' : '/';
            const newMediaPath = parentPathMedia ? (parentPathMedia + sepMedia + newName) : newName;
            // 4. タグデータの一時移行 (古いキーから新しいキーへ)
            const newCompositeKey = `${appState.mode}:${item.is_dir ? newMediaPath : newMediaPath.replace(/\.[^/.]+$/, "")}`;
            appState.tempTagsData[newCompositeKey] = [...(appState.tempTagsData[oldCompositeKey] || item.tags || [])]; 
            if (oldCompositeKey !== newCompositeKey) delete appState.tempTagsData[oldCompositeKey];
            // 5. アイテムオブジェクトの更新
            item.name = newName; item.name_no_ext = newBase; 
            item.full_path = newFullPath; item.media_path = newMediaPath;
            // 6. UIの即時反映
            card.dataset.path = item.full_path; nameEl.textContent = `${icon} ${item.name}`;
            renderTags(card.querySelector('.card-tags'), item);
        } else nameEl.textContent = `${icon} ${item.name}`;
    };
    input.onblur = save; 
    input.onkeydown = (e) => { if(e.key === 'Enter') { e.preventDefault(); input.blur(); } };
}
export function renderTagFilter() { // タグフィルターUI構築
    const container = document.getElementById('tag-filter-scroll'); container.innerHTML = ''; const counts = {};
    Object.keys(appState.tagsData).forEach(k => { if(k.startsWith(appState.mode)) appState.tagsData[k].forEach(t => { if(!t.startsWith('*')) counts[t] = (counts[t]||0)+1; }); });
    Object.entries(counts).sort((a,b) => b[1]-a[1]).forEach(([tag, count]) => {
        const span = document.createElement('span'); span.className = 'filter-tag'; if(appState.selectedTags.includes(tag)) span.classList.add('highlighted');
        span.textContent = `${tag} (${count})`; span.onclick = () => window.dispatchEvent(new CustomEvent('toggle-tag', { detail: tag })); container.appendChild(span);
    });
}
function parseVTT(vttText) { // VTT字幕パース
    const items = [], lines = vttText.split(/\r?\n/); let i = 0;
    while (i < lines.length) { if (lines[i].includes('-->')) break; i++; }
    while (i < lines.length) {
        const timeMatch = lines[i].trim().match(/((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})\s+-->\s+((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})/);
        if (timeMatch) {
            const start = parseTime(timeMatch[1]), end = parseTime(timeMatch[2]); let text = []; i++;
            while (i < lines.length && lines[i].trim() !== '') { if (!lines[i].trim().startsWith('NOTE')) text.push(lines[i]); i++; }
            items.push({ start, end, text: text.join('<br>') });
        } else i++;
    }
    return items;
}
function parseTime(timeStr) { // 時間文字列変換
    const parts = timeStr.replace(',', '.').split(':'); let seconds = 0;
    if (parts.length === 3) seconds += parseInt(parts[0], 10) * 3600 + parseInt(parts[1], 10) * 60 + parseFloat(parts[2]);
    else if (parts.length === 2) seconds += parseInt(parts[0], 10) * 60 + parseFloat(parts[1]);
    return seconds;
}
async function loadSubtitles(mediaItem, displayTarget) { // 字幕ファイル取得
    currentSubtitles = []; displayTarget.innerHTML = ''; if(displayTarget === audioLyricsText) audioLyricsContainer.classList.remove('active');
    const exactMatchName = mediaItem.name + '.vtt', stemMatchName = mediaItem.name_no_ext;
    let subtitleFile = viewerState.fileList.find(f => f.media_type === 'subtitle' && f.name === exactMatchName) || viewerState.fileList.find(f => f.media_type === 'subtitle' && f.name_no_ext === stemMatchName) || viewerState.fileList.find(f => f.media_type === 'subtitle' && f.name.startsWith(stemMatchName + '.')) || viewerState.fileList.find(f => f.media_type === 'subtitle' && f.name.includes(mediaItem.name_no_ext));
    if (subtitleFile) {
        try {
            const res = await fetch(`/api/media/${appState.mode}/pages/${safeEncodePath(subtitleFile.media_path)}`);
            if (res.ok) { currentSubtitles = parseVTT(await res.text()); if (displayTarget === audioLyricsText && currentSubtitles.length > 0) audioLyricsContainer.classList.add('active'); }
        } catch (err) { console.error("字幕読み込みエラー:", err); }
    }
}
function startSubtitleSync(mediaElement, displayElement) { // 字幕同期
    mediaElement.ontimeupdate = () => {
        const t = mediaElement.currentTime, cue = currentSubtitles.find(item => t >= item.start && t <= item.end);
        if (cue) { if (displayElement.innerHTML !== cue.text) { displayElement.innerHTML = cue.text; displayElement.style.opacity = 1; } }
        else { if (displayElement.innerHTML !== '') displayElement.innerHTML = ''; }
        updatePlayerUI();
    };
}
export async function renderStatsView() { // 統計画面描画
    const container = document.getElementById('stats-container'); container.innerHTML = '<div id="loading-indicator" style="display:block; position:static; margin:50px auto;">統計データを分析中...</div>';
    try {
        const allData = await api.getStats(); container.innerHTML = '';
        if (!allData || allData.length === 0) { container.innerHTML = '<p style="text-align:center; padding:20px; color:#888;">条件を満たすデータがありません（5回以上のアクセス）</p>'; return; }
        const groupedData = {}, modeOrder = appState.modesConfig.map(m => m.id);
        allData.forEach(item => { if (!groupedData[item.mode]) groupedData[item.mode] = []; groupedData[item.mode].push(item); });
        Object.keys(groupedData).forEach(m => { if (!modeOrder.includes(m)) modeOrder.push(m); });
        const gridContainer = document.createElement('div'); gridContainer.className = 'stats-grid-container'; container.appendChild(gridContainer);
        modeOrder.forEach(mode => {
            let items = groupedData[mode]; if (!items || items.length === 0) return;
            items.sort((a, b) => b.total - a.total); items = items.slice(0, 20);
            const maxViews = Math.max(...items.map(d => d.total || 0)) || 1, COLORS = ['#4e79a7', '#f28e2c', '#e15759', '#76b7b2', '#59a14f', '#edc949', '#af7aa1', '#ff9da7', '#9c755f', '#bab0ab'], section = document.createElement('div');
            section.className = 'stats-mode-section'; section.innerHTML = `<div class="stats-mode-title"><span>${mode}</span><span class="stats-mode-count">TOP ${items.length}</span></div>`;
            items.forEach((item, idx) => section.appendChild(createStatRow(item, maxViews, COLORS[idx % COLORS.length]))); gridContainer.appendChild(section);
        });
    } catch (e) { console.error(e); container.innerHTML = `<p style="color:red; text-align:center;">読み込み失敗: ${e.message}</p>`; }
}
function truncateMiddle(str, maxLength = 18) { // 文字列省略処理
    if (!str || str.length <= maxLength) return str;
    const charsToShow = maxLength - 3, frontChars = Math.max(2, Math.ceil(charsToShow * 0.6)), backChars = Math.max(2, Math.floor(charsToShow * 0.4));
    return str.substring(0, frontChars) + '...' + str.substring(str.length - backChars);
}
function createStatRow(node, maxViews, color) { // 統計行要素生成
    const container = document.createElement('div'); container.className = 'stat-row-container';
    const header = document.createElement('div'); header.className = 'stat-row-header';
    const label = document.createElement('div'); label.className = 'stat-row-label'; label.textContent = truncateMiddle(node.name, 18); label.title = node.name;
    const barWrapper = document.createElement('div'); barWrapper.className = 'stat-bar-wrapper';
    const bar = document.createElement('div'); bar.style.width = `${Math.max(maxViews > 0 ? (node.total / maxViews) * 100 : 0, 1)}%`; bar.style.backgroundColor = color; bar.className = 'stat-bar-fill';
    const textOverlay = document.createElement('span'); textOverlay.textContent = node.total; textOverlay.className = 'stat-bar-text';
    barWrapper.append(bar, textOverlay); header.append(label, barWrapper); container.appendChild(header);
    const histogramContainer = document.createElement('div'); histogramContainer.className = 'histogram-area compact-histogram';
    barWrapper.onclick = (e) => { e.stopPropagation(); if (histogramContainer.style.display === 'flex') { histogramContainer.style.display = 'none'; return; } histogramContainer.innerHTML = ''; histogramContainer.style.display = 'flex'; renderHistogram(node, histogramContainer, color); };
    container.appendChild(histogramContainer); return container;
}
function renderHistogram(folderNode, container, mainColor) { // ヒストグラム描画
    if (!folderNode.nodes || folderNode.nodes.length === 0) { container.innerHTML = '<span style="color:#666; margin:auto; font-size:11px;">詳細データなし</span>'; return; }
    const sortedNodes = [...folderNode.nodes].sort((a, b) => (a.type === 'page' && b.type === 'page') ? (a.page_index || 0) - (b.page_index || 0) : a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: 'base'}));
    const maxItemViews = Math.max(...sortedNodes.map(p => p.views || 0)) || 1;
    sortedNodes.forEach(node => {
        const bar = document.createElement('div'); bar.style.flexGrow = 1; bar.style.height = `${Math.max((node.views / maxItemViews) * 100, 5)}%`; bar.style.backgroundColor = mainColor; bar.className = 'histogram-bar'; bar.title = `${node.name} : ${node.views} 回アクセス`;
        bar.onmouseenter = () => { bar.style.opacity = 1; bar.style.backgroundColor = '#fff'; }; bar.onmouseleave = () => { bar.style.opacity = 0.8; bar.style.backgroundColor = mainColor; };
        bar.onclick = (e) => { e.stopPropagation(); jumpAndOpen(node, folderNode.full_path, folderNode.mode); }; container.appendChild(bar);
    });
}
async function jumpAndOpen(targetNode, parentPath, targetMode) { // アイテムへの直接移動
    const statsView = document.getElementById('stats-view'), browseView = document.getElementById('browse-view');
    if (statsView.style.display !== 'none') { statsView.style.display = 'none'; browseView.style.display = 'block'; (!appState.inSearchMode && !appState.isRoot) ? document.getElementById('directory-header').style.display = 'flex' : document.getElementById('tag-filter-container').style.display = 'block'; }
    if (targetMode && targetMode !== appState.mode) { appState.mode = targetMode; const selector = document.getElementById('mode-selector'); if (selector) selector.value = targetMode; appState.selectedTags = []; appState.pathStack = []; }
    const targetPath = parentPath || targetNode.full_path || targetNode.path;
    if (!targetPath) { alert("エラー：対象パスを取得できず、移動できません。"); return; }
    if (targetPath) {
        const validParts = targetPath.split(/[/\\]/).filter(p => p && !p.includes(':')); let currentRelPath = "";
        for (let i = Math.max(0, validParts.length - 3); i < validParts.length; i++) { if (validParts[i]) { if (validParts[i].toUpperCase().endsWith('_PAGES')) continue; currentRelPath = currentRelPath ? currentRelPath + '/' + validParts[i] : validParts[i]; try { api.recordView(currentRelPath.lastIndexOf('.') > 0 ? currentRelPath.substring(0, currentRelPath.lastIndexOf('.')) : currentRelPath, appState.mode, null); } catch (e) {} } }
    }
    try { await window.browsePathFunction(targetPath); } catch (e) { console.error("ロード失敗:", e); return; }
    if (targetNode.type === 'page') { const imagesList = viewerState.fileList.filter(f => f.media_type === 'image'); if (imagesList.length === 0) return; openImageViewer(imagesList[Math.min(Math.max(targetNode.page_index, 0), imagesList.length - 1)]); }
}
export async function openTextViewer(item) { // テキストリーダー起動
    const viewer = document.getElementById('text-viewer'), headerDiv = document.getElementById('text-reader-header'); showLoading(true); recordFileAccess(item);
    try {
        const res = await fetch(`/api/media/${appState.mode}/pages/${safeEncodePath(item.media_path)}`); if (!res.ok) throw new Error('Load failed');
        const text = await res.text(); textState.isOpen = true; textState.filename = item.name; textState.content = text; textState.currentPage = 0; textState.pages = [];
        for (let i = 0; i < text.length; i += textState.charsPerPage) textState.pages.push(text.slice(i, i + textState.charsPerPage));
        if (textState.pages.length === 0) textState.pages.push("コンテンツが空です");
        headerDiv.textContent = item.name; viewer.classList.add('active'); renderTextPage();
    } catch (e) { alert("テキストファイルを読み込めません: " + e.message); } finally { showLoading(false); }
}
function renderTextPage() { const contentDiv = document.getElementById('text-reader-content'); contentDiv.textContent = textState.pages[textState.currentPage]; contentDiv.scrollTop = 0; document.getElementById('text-page-info').textContent = `${textState.currentPage + 1} / ${textState.pages.length}`; } // テキストページ描画
export function nextTextPage() { if (textState.currentPage < textState.pages.length - 1) { textState.currentPage++; renderTextPage(); } } // テキスト次ページ
export function prevTextPage() { if (textState.currentPage > 0) { textState.currentPage--; renderTextPage(); } } // テキスト前ページ
export function closeTextViewer() { textState.isOpen = false; document.getElementById('text-viewer').classList.remove('active'); } // テキストビューア終了
function recordFileAccess(item) { if (appState.isRoot) return; const parts = item.media_path.split(/[/\\]/); parts.pop(); if (parts.length > 0) api.recordView(parts.join('/'), item.mode || appState.mode, item.name); } // メディアアクセス記録
export function resetRatingPanel() { // 評価パネル初期化
    const freshness = document.getElementById('rate-freshness'); if (!freshness) return; freshness.value = 0; document.getElementById('rate-melody').value = 0; document.getElementById('rate-emotion').value = 0;
    toggleRatingInputs(true); const btn = document.getElementById('submit-rating-btn'); if(btn) btn.textContent = "感想を記録"; playerState.hasRated = false;
}
export function toggleRatingInputs(enable) { ['rate-freshness', 'rate-melody', 'rate-emotion', 'submit-rating-btn'].forEach(id => { const el = document.getElementById(id); if (el) el.disabled = !enable; }); } // 評価入力切替
export function initVisualizer() { // 波形ビジュアライザ初期化
    if (isVisualizerInited) return; const canvas = document.getElementById('visualizer-canvas'); if (!canvas) return;
    const resizeCanvas = () => { if(canvas.parentElement) { canvas.width = canvas.parentElement.offsetWidth; canvas.height = canvas.parentElement.offsetHeight; initStars(canvas.width, canvas.height); } };
    window.addEventListener('resize', resizeCanvas); resizeCanvas();
    if (!audioContext) {
        const AudioContext = window.AudioContext || window.webkitAudioContext; if (!AudioContext) return; audioContext = new AudioContext(); analyser = audioContext.createAnalyser(); analyser.fftSize = 256; analyser.smoothingTimeConstant = 0.9;
        const audio = document.getElementById('current-audio-player');
        if (!source && audio) { try { source = audioContext.createMediaElementSource(audio); source.connect(analyser); analyser.connect(audioContext.destination); } catch(e) { console.error("AudioContext connect failed:", e); } }
        dataArray = new Uint8Array(analyser.frequencyBinCount);
    }
    isVisualizerInited = true;
}
export function toggleVisualizer(shouldPlay) { if (shouldPlay) { if (!animationId) renderVisualizer(); } else { if (animationId) { cancelAnimationFrame(animationId); animationId = null; } } } // ビジュアライザのオンオフ
function initStars(w, h) { stars = []; const finalCount = Math.min(Math.max(Math.floor((w * h) / 6400), 30), 100); for(let i=0; i<finalCount; i++) stars.push({ x: Math.random() * w, y: Math.random() * h, size: Math.random() * 2 + 0.5, opacity: Math.random(), speed: Math.random() * 0.1 + 0.02 }); } // スター生成
function renderVisualizer() { // ビジュアライザ描画ループ
    const canvas = document.getElementById('visualizer-canvas'); if (!canvas) { animationId = requestAnimationFrame(renderVisualizer); return; }
    const container = canvas.parentElement; if (canvas.width !== container.offsetWidth || canvas.height !== container.offsetHeight) { canvas.width = container.offsetWidth; canvas.height = container.offsetHeight; initStars(canvas.width, canvas.height); }
    if (canvas.height === 0) { animationId = requestAnimationFrame(renderVisualizer); return; }
    const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height, CONFIG = { starSpeedSens: 4.0, starGlowSens: 0.7, barHeightScale: 1.1, barExponent: 1.1, useBassForStars: true };
    if (analyser) analyser.getByteFrequencyData(dataArray); ctx.clearRect(0, 0, w, h); let boost = 0;
    if (dataArray) {
        if (CONFIG.useBassForStars) { const bassLimit = Math.floor(dataArray.length * 0.1) || 1; let bassSum = 0; for(let k=0; k < bassLimit; k++) bassSum += dataArray[k]; boost = (bassSum / bassLimit) / 255; }
        else { let sum = 0; for(let i=0; i<dataArray.length; i++) sum += dataArray[i]; boost = (sum / dataArray.length) / 255; }
    }
    ctx.fillStyle = "#FFF";
    stars.forEach(star => {
        ctx.globalAlpha = Math.min(0.3 + (Math.sin(Date.now() * 0.005 * star.speed) * 0.3) + (boost * CONFIG.starGlowSens), 1); ctx.beginPath(); ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2); ctx.fill();
        star.y -= star.speed + (boost * CONFIG.starSpeedSens); if (star.y < 0) { star.y = h; star.x = Math.random() * w; }
    });
    if (dataArray) {
        const barCount = VISUALIZER_CONFIG.barCount, barWidth = w / barCount, step = Math.floor((Math.floor(dataArray.length * 0.7)) / barCount) || 1; let x = 0;
        ctx.lineWidth = 2; ctx.globalCompositeOperation = 'lighter';
        for (let i = 0; i < barCount; i++) {
            const percent = Math.pow((dataArray[i * step] || 0) / 255, CONFIG.barExponent) * CONFIG.barHeightScale, barHeight = Math.min(percent * (h * 0.7), h * 0.9), color = `hsla(${(i / barCount) * 220 + (Date.now() * 0.05)}, 90%, 60%, ${0.6 + percent * 0.4})`;
            ctx.strokeStyle = color; ctx.shadowBlur = 20; ctx.shadowColor = color; ctx.globalAlpha = 0.9; const y = h - barHeight - 20;
            ctx.beginPath(); ctx.strokeRect(x + 2, y, barWidth - 4, 8); ctx.moveTo(x + barWidth / 2, y + 8); ctx.lineTo(x + barWidth / 2, h); ctx.stroke(); x += barWidth;
        }
        ctx.globalCompositeOperation = 'source-over'; ctx.shadowBlur = 0;
    }
    animationId = requestAnimationFrame(renderVisualizer);
}
export function refreshAllTagsUI() { // 全カードとヘッダーのタグを更新
    if (appState.currentDataSet && appState.currentDataSet.metadata) { const headerTags = document.getElementById('header-tags'); if (headerTags.offsetParent !== null) renderTags(headerTags, appState.currentDataSet.metadata); }
    document.querySelectorAll('.card').forEach(card => { const tagsContainer = card.querySelector('.card-tags'), item = appState.currentDataSet.items.find(i => i.full_path === card.dataset.path); if (tagsContainer && item) renderTags(tagsContainer, item); });
}
// ドキドキモード制御用変数 (オーディオ同期、非同期デコード＆メモリキューによる究極のパフォーマンス最適化)
const dkdViewer = document.getElementById('dokidoki-viewer'), dkdContainer = document.getElementById('dokidoki-container'), dkdSpotlight = document.getElementById('dokidoki-spotlight');
const dkdStartBtn = document.getElementById('dkd-start-btn'), dkdBps = document.getElementById('dkd-bps'), dkdBeatsSwitch = document.getElementById('dkd-beats-switch');
let dkdMasterTimer = null, dkdMediaTimeout = null, dkdMediaList = [], dkdIsPlaying = false, dkdBeatCount = 0;
let dkdAngle = Math.random() * Math.PI, dkdDirection = 1, dkdHue = Math.random() * 360, dkdActiveLayer = 0;
const dokiAudio = new Audio('/static/doki.wav');

// 🌟 究極最適化：メモリキュー（事前デコード済みの要素をストックする）
let dkdMediaQueue = []; 
const MAX_PRELOAD = 4; // 常に4つのメディアをバックグラウンドで完全解析・待機させておく

document.getElementById('dokidoki-btn').onclick = async () => { // 画面表示とリスト取得
    dkdViewer.style.display = 'flex'; dkdContainer.innerHTML = ''; dkdSpotlight.style.display = 'none';
    try {
        dkdStartBtn.textContent = "読込中..."; dkdStartBtn.disabled = true;
        const res = await fetch('/api/dokidoki_media'); const data = await res.json(); dkdMediaList = data.items;
        dkdStartBtn.textContent = "START"; dkdStartBtn.disabled = false;
        if(dkdMediaList.length === 0) { alert("メディアが見つかりません。"); dkdStartBtn.disabled = true; }
    } catch(e) { console.error("ドキドキ取得エラー", e); dkdStartBtn.textContent = "エラー"; }
};

document.getElementById('dokidoki-close-btn').onclick = () => { dkdViewer.style.display = 'none'; stopDokidoki(); };
dkdBps.oninput = (e) => { document.getElementById('dkd-bps-val').textContent = e.target.value; if(dkdIsPlaying) restartTimer(); };
dkdBeatsSwitch.oninput = (e) => { document.getElementById('dkd-beats-switch-val').textContent = e.target.value; }; 

dkdStartBtn.onclick = () => { // 再生トグルと初期化
    if(dkdIsPlaying) stopDokidoki(); 
    else { 
        dkdIsPlaying = true; dkdSpotlight.style.display = 'block'; dkdBeatCount = 0; dkdActiveLayer = 0; dkdMediaQueue = [];
        
        // 双缓冲レイヤー初期化
        dkdContainer.innerHTML = '<div id="dkd-layer-0" style="position:absolute;width:100%;height:100%;opacity:1;transition:opacity 0.1s ease;display:flex;justify-content:center;align-items:center;will-change:opacity;"></div>' +
                                 '<div id="dkd-layer-1" style="position:absolute;width:100%;height:100%;opacity:0;transition:opacity 0.1s ease;display:flex;justify-content:center;align-items:center;will-change:opacity;"></div>';
        
        maintainPreloadQueue(); // 直ちにバックグラウンドでキューの補充・事前デコードを開始
        
        // 最初のデコード猶予を少し与えてから再生開始 (300ms)
        setTimeout(() => {
            if(!dkdIsPlaying) return;
            loadFromQueueToLayer(0); loadFromQueueToLayer(1);
            
            const R = Math.max(window.innerWidth, window.innerHeight) * 1.5;
            dkdSpotlight.style.transition = 'none'; dkdSpotlight.style.transform = `translate3d(${R * Math.cos(dkdAngle) * -1}px, ${R * Math.sin(dkdAngle) * -1}px, 0)`; 
            
            requestAnimationFrame(() => requestAnimationFrame(() => { restartTimer(); document.getElementById('dkd-layer-0').querySelector('video')?.play().catch(()=>{}); }));
            dkdStartBtn.textContent = "STOP"; dkdStartBtn.style.background = "#f44336"; 
        }, 300);
    }
};

function maintainPreloadQueue() { // バックグラウンド非同期キュー補充（メインスレッドをブロックしない）
    if (!dkdIsPlaying || dkdMediaList.length === 0) return;
    while (dkdMediaQueue.length < MAX_PRELOAD) {
        const item = dkdMediaList[Math.floor(Math.random() * dkdMediaList.length)];
        const css = 'width: 100%; height: 100%; object-fit: contain; display: block; transform: translateZ(0);'; 
        const src = `/api/dokidoki_file/${item.path.split('/').map(encodeURIComponent).join('/')}`;

        if (item.type === 'image') {
            const img = new Image(); img.src = src; img.style.cssText = css;
            // 🌟 究極の最適化: decode() メソッドにより、画像のデコードを別スレッドで強制する（UIのフリーズを完全防止）
            img.decode().catch(()=>{}); 
            dkdMediaQueue.push(img);
        } else {
            const vid = document.createElement('video'); vid.src = src; vid.style.cssText = css; 
            vid.muted = true; vid.loop = true; vid.playsInline = true; vid.preload = "auto"; // 動画も事前にバッファリング
            vid.onloadedmetadata = () => { vid.currentTime = Math.random() * Math.max(0, vid.duration - 3); };
            dkdMediaQueue.push(vid);
        }
    }
}

function loadFromQueueToLayer(layerIndex) { // デコード済みの要素をレイヤーに配置する（コストゼロ）
    if (!dkdIsPlaying) return;
    const layer = document.getElementById(`dkd-layer-${layerIndex}`); if (!layer) return;
    layer.innerHTML = ''; 
    if (dkdMediaQueue.length > 0) layer.appendChild(dkdMediaQueue.shift()); // キューの先頭から取得
    maintainPreloadQueue(); // 消費したら即座に次を補充
}

function restartTimer() { clearInterval(dkdMasterTimer); dkdMasterTimer = setInterval(beatProcess, 1000 / parseInt(dkdBps.value)); }

function stopDokidoki() { 
    clearInterval(dkdMasterTimer); clearTimeout(dkdMediaTimeout); dkdIsPlaying = false; 
    dkdContainer.innerHTML = ''; dkdSpotlight.style.display = 'none'; dkdMediaQueue = [];
    dkdStartBtn.textContent = "START"; dkdStartBtn.style.background = "#2e7d32"; 
} 

function playTickSound() { const a = dokiAudio.cloneNode(); a.volume = 0.5; a.play().catch(()=>{}); }

function beatProcess() { 
    playTickSound(); dkdBeatCount++; 
    if(dkdBeatCount >= parseInt(dkdBeatsSwitch.value)) { dkdBeatCount = 0; sweepSpotlightAndSwitch(); }
}

function sweepSpotlightAndSwitch() { 
    const dur = 1.0 / parseInt(dkdBps.value); 
    dkdAngle += (Math.random() - 0.5) * (10 * Math.PI / 180); dkdHue = (dkdHue + (Math.random() - 0.5) * 30 + 360) % 360;
    const R = Math.max(window.innerWidth, window.innerHeight) * 1.5;
    
    dkdSpotlight.style.background = `radial-gradient(circle, hsla(${dkdHue}, 100%, 80%, 0.45) 0%, hsla(${dkdHue}, 100%, 65%, 0.15) 30%, transparent 65%)`;
    dkdSpotlight.style.transition = `transform ${dur}s cubic-bezier(0.8, 0, 0.2, 1)`;
    dkdSpotlight.style.transform = `translate3d(${R * Math.cos(dkdAngle) * dkdDirection}px, ${R * Math.sin(dkdAngle) * dkdDirection}px, 0)`; 
    dkdDirection *= -1; 
    
    clearTimeout(dkdMediaTimeout);
    dkdMediaTimeout = setTimeout(triggerMediaSwitch, dur * 500); 
}

function triggerMediaSwitch() { // オパシティ反転による極速切替と次弾装填
    if(dkdMediaList.length === 0 || !dkdIsPlaying) return;
    const currentLayer = document.getElementById(`dkd-layer-${dkdActiveLayer}`);
    dkdActiveLayer = 1 - dkdActiveLayer; 
    const nextLayer = document.getElementById(`dkd-layer-${dkdActiveLayer}`);
    
    if (currentLayer && nextLayer) {
        currentLayer.style.opacity = '0'; currentLayer.querySelector('video')?.pause(); 
        nextLayer.style.opacity = '1'; nextLayer.querySelector('video')?.play().catch(()=>{}); 
        
        // 完全に隠れた後に、事前解析済みのメモリキューから次の要素を引っ張ってくる
        setTimeout(() => loadFromQueueToLayer(1 - dkdActiveLayer), 150); 
    }
}
export function renderTelemetry() { // Telemetry Dashboard
    const badgeEdit = document.getElementById('badge-edit-mode');
    const badgeRebuild = document.getElementById('badge-rebuilding');
    const badgeSource = document.getElementById('badge-source');
    if (!badgeEdit || !badgeRebuild || !badgeSource) return;
    // 1. 編集モードの表示/非表示
    badgeEdit.style.display = appState.isTagEditMode ? 'block' : 'none';
    // 2. キャッシュ構築中の表示/非表示
    badgeRebuild.style.display = appState.isRebuilding ? 'block' : 'none';
    // 3. データソースの切り替え
    if (appState.currentSource) {
        badgeSource.style.display = 'block';
        if (appState.currentSource === 'cache') {
            badgeSource.textContent = '⚡ Cache';
            badgeSource.style.backgroundColor = '#4CAF50';
            badgeSource.title = '超高速キャッシュメモリから読み込みました';
        } else {
            badgeSource.textContent = '💿 Disk';
            badgeSource.style.backgroundColor = '#FF9800';
            badgeSource.title = '物理ディスクを直接スキャンしました';
        }
    } else {
        badgeSource.style.display = 'none';
    }
}