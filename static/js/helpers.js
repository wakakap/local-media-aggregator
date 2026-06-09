export function formatBytes(bytes, decimals = 2) { // バイト数を人間が読みやすい形式に変換
    if (!+bytes) return '0 B'; const k = 1024, dm = decimals < 0 ? 0 : decimals, sizes = ['B', 'KB', 'MB', 'GB', 'TB'], i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}
export function truncate(str, n) { return (str.length > n) ? str.substr(0, n - 1) + '...' : str; } // 長いファイル名の切り詰め
export function getParentPath(fullPath) { // 完全パスから親ディレクトリパスを抽出
    const separator = fullPath.includes('\\') ? '\\' : '/', parts = fullPath.split(separator); parts.pop(); return parts.join(separator);
}
export function formatTime(seconds) { // 秒数を MM:SS 形式にフォーマット
    if (!seconds || isNaN(seconds)) return "00:00"; const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}