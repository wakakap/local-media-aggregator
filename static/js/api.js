async function fetchJson(url, options = {}) { // 汎用JSONフェッチ関数
    try { const res = await fetch(url, options); if (!res.ok) throw new Error(`HTTP ${res.status}`); return await res.json(); }
    catch (err) { console.error("APIエラー:", err); return { error: err.message }; }
}
export const browse = (mode, path = '') => fetchJson(`/api/browse?mode=${mode}&path=${encodeURIComponent(path)}`); // フォルダ参照
export const search = (mode, q, type = 'keyword') => fetchJson(`/api/search?mode=${mode}&q=${encodeURIComponent(Array.isArray(q) ? q.join(',') : q)}&type=${type}`); // 検索リクエスト
export const getTags = () => fetchJson('/api/tags'); // タグ一覧取得
export const saveTags = (data) => fetchJson('/api/tags', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)}); // タグ保存
export const recordView = (itemKey, mode, identifier = null) => fetchJson('/api/record_view', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({item_key: itemKey, mode, identifier})}); // 閲覧履歴記録
export const renameItem = (mode, fullPath, newName) => fetchJson('/api/rename_item', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode, full_path: fullPath, new_name: newName})}); // リネーム処理
export const openFolder = (path) => fetchJson(`/api/open_folder?path=${encodeURIComponent(path)}`); // ローカルフォルダ展開
export const getStats = () => fetchJson('/api/structured_stats'); // 統計データ取得
export const exportData = () => fetchJson('/api/export_data', { method: 'POST' }); // データ構造エクスポート
export const rateMusic = (key, ratings) => fetchJson('/api/rate_music', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ key: key, value: { ...ratings, timestamp: new Date().toLocaleString('ja-JP', { hour12: false }) } }) }); // 音楽評価の送信
export const updateCache = (mode) => fetchJson('/api/update_cache', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode}) }); // キャッシュ更新
export const cleanData = () => fetchJson('/api/clean_data', { method: 'POST' }); // 不要データクリーンアップ
export const getSettings = () => fetchJson('/api/settings'); // システム設定取得
export const saveBatchEdits = (mode, tagsData, renames) => fetchJson('/api/batch_edit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode, tags: tagsData, renames})}); // 一括編集の送信