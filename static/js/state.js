export const appState = {
    mode: '',            // 現在のモード
    modesConfig: [],     // 全モードの設定を保持
    currentPath: '',     // 現在閲覧中の絶対パス
    pathStack: [],       // パス履歴スタック（パンくずリスト用）
    isRoot: true,        // ルートディレクトリかどうか
    tagsData: {},        // グローバルタグキャッシュ
    tempTagsData: {},    // 編集モード中の一時タグデータ
    pendingRenames: {},  // 未保存のリネーム操作を一時保持する辞書
    pendingDeletes: [],  // 未保存の削除に伴うタグ破棄対象キーを一時保持する配列
    isTagEditMode: false,// タグ編集モード状態
    searchQuery: '',     // 現在の検索キーワード
    inSearchMode: false, // 検索結果ビュー状態
    currentSource: '',   // 現在のデータソース ('cache' または 'disk')
    isRebuilding: false, // バックグラウンドでキャッシュ構築中かどうか
    renderingId: 0,      // 非同期レンダリングの競合防止ID
    activeImports: {},   // 実行中の自動インポートタスク
    sortMode: 'default', // カードの並び順 ('default' | 'name' | 'size')
    selectedTags: [],    // 正選タグ（左クリック）：全て含むカードのみ表示（AND）
    excludedTags: [],    // 反選タグ（右クリック）：いずれか含むカードを除外（OR）
    keepFilterState: false, // フォルダ遷移をまたいで並び順・タグ絞り込みを維持するフラグ
    rootScrollTop: 0,       // ROOT ビューのスクロール位置（作品から復帰した際の復元用）
    restoreRootScroll: false, // 次回の ROOT 描画時にスクロール位置を復元するフラグ
    liveBackups: {}          // 実行中/待機中/直近終了のバックアップタスク（/api/status のポーリング結果）
};

export const playerState = {
    playlist: [],        // 現在のディレクトリ内の音声ファイル一覧
    currentIndex: -1,    // 現在の再生インデックス
    isPlaying: false,    // 再生状態
    isShuffled: false,   // シャッフル状態
    repeatMode: 'ALL',   // リピートモード ('NONE', 'ALL', 'ONE')
    volume: 1.0,         // 音量
    currentTrack: null   // 現在再生中のファイルオブジェクト
};

export const viewerState = {
    fileList: [],        // 現在のディレクトリ内の全メディアファイル（画像ページ送り用）
    currentIndex: -1,    // 現在のインデックス
    rotation: 0          // 画像の回転角度
};

export const textState = {
    isOpen: false,       // テキストビューアの開閉状態
    content: '',         // 全テキストコンテンツ
    pages: [],           // 分割後のページ配列
    currentPage: 0,      // 現在のページ番号 (0始まり)
    charsPerPage: 3000,  // 1ページあたりの表示文字数 (目安)
    filename: ''         // ファイル名
};