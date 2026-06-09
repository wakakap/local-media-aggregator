export const appState = {
    mode: '',            // 現在のモード
    modesConfig: [],     // 全モードの設定を保持
    currentPath: '',     // 現在閲覧中の絶対パス
    pathStack: [],       // パス履歴スタック（パンくずリスト用）
    isRoot: true,        // ルートディレクトリかどうか
    tagsData: {},        // グローバルタグキャッシュ
    tempTagsData: {},    // 編集モード中の一時タグデータ
    isTagEditMode: false,// タグ編集モード状態
    selectedTags: [],    // 選択されたフィルタータグ
    searchQuery: '',     // 現在の検索キーワード
    inSearchMode: false, // 検索結果ビュー状態
    renderingId: 0,      // 非同期レンダリングの競合防止ID
    activeImports: {}    // 実行中の自動インポートタスク
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