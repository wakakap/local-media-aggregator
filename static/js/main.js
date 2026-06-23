import { appState, playerState } from './state.js';
import * as api from './api.js';
import * as ui from './ui.js';
window.browsePathFunction = browsePath; // グローバル関数バインディング
document.addEventListener('DOMContentLoaded', async () => { // 初期化処理
    const settings = await api.getSettings();
    if (settings && settings.modes && settings.modes.length > 0) {
        appState.modesConfig = settings.modes; appState.mode = new URLSearchParams(window.location.search).get('mode') || settings.modes[0].id;
        const modeSelector = document.getElementById('mode-selector'); modeSelector.innerHTML = '';
        settings.modes.forEach(m => { const opt = document.createElement('option'); opt.value = m.id; opt.textContent = m.name; if (m.id === appState.mode) opt.selected = true; modeSelector.appendChild(opt); });
    } else { alert("システム設定の読み込みに失敗しました。バックエンドを確認してください！"); return; }
    appState.tagsData = await api.getTags(); ui.renderTagFilter();
    browsePath(new URLSearchParams(window.location.search).get('path') || ''); setupEventListeners();
});
async function browsePath(path, pushState = true) { // フォルダ閲覧処理
    const currentRenderId = ++appState.renderingId; ui.showLoading(true); appState.inSearchMode = false; appState.selectedTags = []; appState.searchQuery = ''; ui.renderTagFilter();
    try {
        const data = await api.browse(appState.mode, path || ''); if (currentRenderId !== appState.renderingId) { console.log("期限切れの browse リクエストを無視します"); return; }
        if (!data.error) { appState.currentPath = data.current_path; appState.isRoot = data.is_root; appState.currentSource = data.source; appState.isRebuilding = data.is_rebuilding; ui.renderTelemetry();if (pushState) { const url = new URL(window.location); url.searchParams.set('mode', appState.mode); url.searchParams.set('path', path || ''); window.history.pushState({ path: path || '', mode: appState.mode }, '', url); } ui.renderContent(data); }
        else alert("読み込み失敗: " + data.error);
    } catch (e) { console.error(e); } finally { if (currentRenderId === appState.renderingId) ui.showLoading(false); }
}
function updateEditButtonUI() { // タグ編集UI更新
    const btn = document.getElementById('edit-tags-btn'), menu = document.getElementById('maintenance-menu');
    if (appState.isTagEditMode) { btn.textContent = "タグを保存"; btn.style.backgroundColor = "red"; } else { btn.textContent = "タグを編集"; btn.style.backgroundColor = ""; }
    menu.style.display = 'none';
}
async function performSearch(query, type) { // 検索処理
    const currentRenderId = ++appState.renderingId; ui.showLoading(true); appState.inSearchMode = true; appState.searchQuery = Array.isArray(query) ? query.join('+') : query; appState.searchType = type;
    try { const data = await api.search(appState.mode, query, type); if (currentRenderId !== appState.renderingId) { console.log("期限切れの search リクエストを無視します"); return; } appState.currentSource = data.source; appState.isRebuilding = data.is_rebuilding; ui.renderTelemetry(); ui.renderContent({ items: data.items, current_path: 'SEARCH', is_root: false }); }
    catch (e) { console.error(e); } finally { if (currentRenderId === appState.renderingId) ui.showLoading(false); }
}
function setupEventListeners() { // 全イベントリスナーの登録
    window.addEventListener('popstate', (e) => { if (e.state) { if (e.state.mode && e.state.mode !== appState.mode) { appState.mode = e.state.mode; document.getElementById('mode-selector').value = appState.mode; } browsePath(e.state.path, false); } else browsePath('', false); });
    window.addEventListener('browse-path', (e) => browsePath(e.detail, true));
    window.addEventListener('browse-root', () => { console.log("⚠️ ルートディレクトリへの戻りリクエストを受信。検索モードを終了し状態をリセットします..."); browsePath(''); });
    window.addEventListener('go-back', () => browsePath(appState.currentPath.split(/[\\/]/).slice(0, -1).join('/'), true));
    window.addEventListener('refresh-view', () => browsePath(appState.currentPath));
    window.addEventListener('toggle-tag', (e) => { const tag = e.detail; appState.selectedTags.includes(tag) ? appState.selectedTags = appState.selectedTags.filter(t => t !== tag) : appState.selectedTags.push(tag); ui.renderTagFilter(); appState.selectedTags.length > 0 ? performSearch(appState.selectedTags, 'tag') : browsePath(''); });
    document.getElementById('open-local-folder-btn').onclick = async () => { if (!appState.currentPath || appState.currentPath === 'SEARCH') { alert("現在、ローカルフォルダを特定できません"); return; } const res = await api.openFolder(appState.currentPath); if (res.status !== 'success') alert("フォルダを開けません: " + (res.message || "不明なエラー")); };
    document.getElementById('mode-selector').addEventListener('change', (e) => { appState.mode = e.target.value; appState.selectedTags = []; appState.pathStack = []; ui.renderTagFilter(); browsePath(''); });
    document.getElementById('search-btn').onclick = () => { const q = document.getElementById('search-input').value.trim(); if (q) performSearch(q, 'keyword'); };
    document.getElementById('search-input').onkeydown = (e) => { if (e.key === 'Enter') document.getElementById('search-btn').click(); };
    document.getElementById('stats-btn').onclick = () => { const browseView = document.getElementById('browse-view'), statsView = document.getElementById('stats-view'); if (statsView.style.display === 'none') { browseView.style.display = 'none'; statsView.style.display = 'block'; document.getElementById('tag-filter-container').style.display = 'none'; document.getElementById('directory-header').style.display = 'none'; ui.renderStatsView(); } else { statsView.style.display = 'none'; browseView.style.display = 'block'; (!appState.inSearchMode && !appState.isRoot) ? document.getElementById('directory-header').style.display = 'flex' : document.getElementById('tag-filter-container').style.display = 'block'; } };
    document.getElementById('maintenance-btn').onclick = () => { const menu = document.getElementById('maintenance-menu'); menu.style.display = menu.style.display === 'block' ? 'none' : 'block'; };
    document.getElementById('edit-tags-btn').onclick = async () => { 
        if (!appState.isTagEditMode) { 
            appState.isTagEditMode = true; 
            appState.tempTagsData = JSON.parse(JSON.stringify(appState.tagsData)); 
            appState.pendingRenames = appState.pendingRenames || {}; 
            updateEditButtonUI(); 
            ui.renderTelemetry();
            if (appState.currentDataSet) ui.refreshAllTagsUI(); 
        } else { 
            ui.showLoading(true); 
            try {
                const res = await api.saveBatchEdits(appState.mode, appState.tempTagsData, appState.pendingRenames || {});
                if (res.status === 'success') {
                    appState.tagsData = appState.tempTagsData; 
                    appState.isTagEditMode = false; 
                    appState.pendingRenames = {}; 
                    updateEditButtonUI(); 
                    appState.isRebuilding = true;
                    appState.currentSource = 'disk';
                    ui.renderTelemetry();
                    try {
                        ui.refreshAllTagsUI(); 
                        ui.renderTagFilter(); 
                    } catch (renderErr) {
                        console.error("UI更新エラー:", renderErr);
                    }
                    ui.showLoading(false);
                    setTimeout(() => {
                        alert("タグと名前の変更を保存しました！"); 
                    }, 50);
                } else {
                    ui.showLoading(false);
                    alert("保存失敗: " + (res.message || "バックエンドでエラーが発生しました"));
                }
            } catch (err) {
                ui.showLoading(false);
                alert("通信エラー: " + err.message);
                console.error("Save Batch Error:", err);
            }
        } 
    };
    document.getElementById('update-cache-btn').onclick = async () => { if (confirm(`${appState.mode} モードのキャッシュを更新しますか？変更をフルスキャンするため数秒かかる場合があります。`)) { ui.showLoading(true); const res = await api.updateCache(appState.mode); ui.showLoading(false); if (res.status === 'success') { alert("キャッシュを更新しました！🚀"); window.dispatchEvent(new CustomEvent('refresh-view')); } else alert("キャッシュ更新失敗: " + res.message); document.getElementById('maintenance-menu').style.display = 'none'; } };
    document.getElementById('export-data-btn').onclick = async () => { if (confirm("すべての作品ディレクトリ構造をTXTファイルにエクスポートしますか？")) { ui.showLoading(true); const res = await api.exportData(); ui.showLoading(false); if (res.status === 'success') alert(`エクスポート成功！\n保存先: ${res.file}`); else alert("エクスポート失敗: " + (res.message || "不明なエラー")); document.getElementById('maintenance-menu').style.display = 'none'; } };
    document.getElementById('clean-data-btn').onclick = async () => { if (confirm("無効なタグと統計データをクリーンアップしますか？\n全ドライブをスキャンし、削除された作品の残留データを削除します。")) { ui.showLoading(true); const res = await api.cleanData(); ui.showLoading(false); if (res.status === 'success') { alert(`クリーンアップ完了！🧹\n無効なタグデータ ${res.tags_removed} 件、統計データ ${res.stats_removed} 件を削除しました`); appState.tagsData = await api.getTags(); ui.renderTagFilter(); window.dispatchEvent(new CustomEvent('refresh-view')); } else alert("クリーンアップ失敗: " + (res.message || "不明なエラー")); document.getElementById('maintenance-menu').style.display = 'none'; } };
    document.getElementById('tag-filter-expand-btn').onclick = () => document.getElementById('tag-filter-container').classList.toggle('expanded');
    const imageViewer = document.getElementById('image-viewer'); let touchStartX = 0, touchStartY = 0, isSwiping = false; // 画像ビューア操作用変数
    imageViewer.addEventListener('touchstart', (e) => { if (e.touches.length === 1) { touchStartX = e.touches[0].clientX; touchStartY = e.touches[0].clientY; isSwiping = false; } }, { passive: true });
    imageViewer.addEventListener('touchend', (e) => { if (e.changedTouches.length === 1) { const diffX = e.changedTouches[0].clientX - touchStartX, diffY = e.changedTouches[0].clientY - touchStartY; if (Math.abs(diffX) > 50 && Math.abs(diffY) < 100) { isSwiping = true; diffX < 0 ? ui.nextImage() : ui.prevImage(); } } });
    imageViewer.onclick = (e) => { if (isSwiping) { isSwiping = false; return; } if (e.target.id === 'image-viewer' || e.target.classList.contains('close-btn')) { imageViewer.classList.remove('active'); document.querySelector('.image-container').innerHTML = ''; return; } if (e.target.tagName === 'IMG') (e.clientX - e.target.getBoundingClientRect().left) > e.target.getBoundingClientRect().width / 2 ? ui.nextImage() : ui.prevImage(); };
    document.addEventListener('keydown', (e) => { // キーボード操作
        if (document.getElementById('image-viewer').classList.contains('active')) { if (e.key === 'ArrowRight') ui.nextImage(); if (e.key === 'ArrowLeft') ui.prevImage(); if (e.key === 'Escape') { document.getElementById('image-viewer').classList.remove('active'); document.querySelector('.image-container').innerHTML = ''; } }
        if (document.getElementById('text-viewer').classList.contains('active')) { if (e.key === 'ArrowRight' || e.key === ' ') ui.nextTextPage(); if (e.key === 'ArrowLeft') ui.prevTextPage(); if (e.key === 'Escape') ui.closeTextViewer(); }
    });
    document.querySelector('#video-player .close-btn').onclick = () => { document.getElementById('video-player').classList.remove('active'); const cv = document.getElementById('current-video'); cv.pause(); cv.removeAttribute('src'); cv.load(); cv.ontimeupdate = null; const sc = document.getElementById('video-subtitle-container'); if (sc) sc.innerHTML = ''; }; // ビデオ閉じる処理
    document.querySelector('#epub-viewer .close-btn').onclick = () => document.getElementById('epub-viewer').classList.remove('active');
    document.getElementById('next-page-btn').onclick = ui.epubNext; document.getElementById('prev-page-btn').onclick = ui.epubPrev;
    document.getElementById('player-play-btn').onclick = () => { const a = document.getElementById('current-audio-player'); a.paused ? a.play() : a.pause(); }; // 音声再生・一時停止
    document.getElementById('player-next-btn').onclick = ui.nextTrack; document.getElementById('player-prev-btn').onclick = ui.prevTrack;
    document.getElementById('player-shuffle-btn').onclick = () => { playerState.isShuffled = !playerState.isShuffled; ui.updatePlayerUI(); };
    document.getElementById('player-loop-btn').onclick = () => { const m = ['ALL', 'ONE', 'NONE']; playerState.repeatMode = m[(m.indexOf(playerState.repeatMode) + 1) % 3]; ui.updatePlayerUI(); };
    document.getElementById('player-close-btn').onclick = () => { const a = document.getElementById('current-audio-player'); a.pause(); a.currentTime = 0; document.getElementById('music-player-bar').classList.remove('active'); const lc = document.getElementById('audio-lyrics-container'); if (lc) lc.classList.remove('active'); document.querySelectorAll('.file-row.playing').forEach(r => r.classList.remove('playing')); playerState.currentTrack = null; };
    const expandBtn = document.getElementById('player-expand-btn');
    expandBtn.onclick = () => { const pb = document.getElementById('music-player-bar'); pb.classList.toggle('expanded'); const isExpanded = pb.classList.contains('expanded'); expandBtn.textContent = isExpanded ? '🔽' : '🔼'; if (isExpanded) { ui.initVisualizer(); ui.toggleVisualizer(true); } else ui.toggleVisualizer(false); };
    document.getElementById('submit-rating-btn').onclick = async () => { // 音楽の評価送信
        const btn = document.getElementById('submit-rating-btn'), track = playerState.currentTrack; if (!track) { console.error("❌ [DEBUG] 再生中のトラックがありません！"); return; }
        ui.toggleRatingInputs(false); btn.textContent = "記録中...";
        const ratings = { freshness: parseInt(document.getElementById('rate-freshness').value), melody: parseInt(document.getElementById('rate-melody').value), emotion: parseInt(document.getElementById('rate-emotion').value) }, key = `MUSIC:${track.media_path.replace(/\\/g, '/')}`;
        console.log("📦 [DEBUG] 送信準備:", { key, ratings });
        try { const res = await api.rateMusic(key, ratings); console.log("✅ [DEBUG] API レスポンス:", res); if (res.status === 'success') { btn.textContent = "記録完了 ✔"; playerState.hasRated = true; } else { alert("記録失敗: " + res.message); ui.toggleRatingInputs(true); btn.textContent = "感想を記録"; } }
        catch (err) { console.error("🔥 [DEBUG] Fetch エラー:", err); ui.toggleRatingInputs(true); btn.textContent = "ネットワークエラー"; }
    };
    document.querySelector('#text-viewer .close-btn').onclick = ui.closeTextViewer; document.getElementById('text-next-btn').onclick = ui.nextTextPage; document.getElementById('text-prev-btn').onclick = ui.prevTextPage;
    const audio = document.getElementById('current-audio-player'); audio.onended = () => { if (playerState.repeatMode === 'ONE') { audio.currentTime = 0; audio.play(); } else ui.nextTrack(); };
    audio.onplay = () => document.getElementById('player-play-btn').textContent = '⏸'; audio.onpause = () => document.getElementById('player-play-btn').textContent = '▶';
}