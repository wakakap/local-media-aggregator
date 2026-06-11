import os, sys, subprocess, threading, json
from flask import Flask, jsonify, render_template, request, send_from_directory, abort
from flask_cors import CORS
import backend_logic as logic

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json") # 設定ファイルの読み込み
try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f: SYSTEM_CONFIG = json.load(f)
except Exception as e:
    print(f"Error loading config.json! System might fail to start. Error: {e}")
    SYSTEM_CONFIG = {"base_dirs": [], "modes": []}

BASE_DIRS = SYSTEM_CONFIG.get("base_dirs", [])
DATA_DIR = SYSTEM_CONFIG.get("system_data_dir", "./data")
TAGS_FILE_PATH, MAP_FILE_PATH = os.path.join(DATA_DIR, "tags.json"), os.path.join(DATA_DIR, "map.txt")
STATS_FILE_PATH, MUSIC_RATINGS_PATH = os.path.join(DATA_DIR, "stats.json"), os.path.join(DATA_DIR, "music_ratings.json")

MODES_CONFIG = {mode["id"].upper(): {'pages': mode["pages"], 'cover': mode["cover"]} for mode in SYSTEM_CONFIG.get("modes", [])}
for base in BASE_DIRS:
    if not os.path.exists(base): print(f"--- 警告：「{base}」が現在存在しないか、接続されていません。") # 未接続ドライブの警告

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False

all_tags = logic.load_tags(TAGS_FILE_PATH)
cover_map = logic.load_cover_map(MAP_FILE_PATH)
TAGS_LOCK = threading.Lock()
GLOBAL_JSON_CACHE = {} # リソースキャッシュ

def get_mode_cache(mode): # キャッシュの取得と更新確認
    cache_file = os.path.join(DATA_DIR, f"{mode}_cache.json")
    if not os.path.exists(cache_file): return None
    try:
        file_mtime = os.path.getmtime(cache_file)
        if mode in GLOBAL_JSON_CACHE and GLOBAL_JSON_CACHE[mode]['mtime'] == file_mtime: return GLOBAL_JSON_CACHE[mode]['data']
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            GLOBAL_JSON_CACHE[mode] = {'mtime': file_mtime, 'data': data}
            return data
    except: return None

def get_paths_for_mode(mode): # 対象モードのパスリスト取得
    config = MODES_CONFIG.get(mode.upper(), MODES_CONFIG['MUSIC'])
    return [os.path.join(base, config['pages']) for base in BASE_DIRS], [os.path.join(base, config['cover']) for base in BASE_DIRS]

def is_safe_path(base_roots, req): # リクエストパスの安全性確認
    req_real = os.path.realpath(req).lower()
    return any(os.path.exists(base) and req_real.startswith(os.path.realpath(base).lower()) for base in base_roots)

def get_active_index(root_paths, target_path): # 対象パスが属するドライブのインデックス取得
    target_real = os.path.realpath(target_path)
    for i, r in enumerate(root_paths):
        if os.path.exists(r) and target_real.startswith(os.path.realpath(r)): return i
    return -1

@app.route('/api/settings')
def api_settings(): return jsonify({"modes": SYSTEM_CONFIG.get("modes", [])}) # 設定情報をフロントエンドに送信

@app.route('/api/media/<mode>/<type>/<path:filename>')
def api_media(mode, type, filename): # 仮想パスを物理パスにマッピングしストリームを返す
    root_paths, cover_paths = get_paths_for_mode(mode)
    base_paths = [os.path.join(c, 'THUMBNAILS') for c in cover_paths] if type == 'thumbnail' else cover_paths if type == 'cover' else root_paths if type in ['pages', 'audio', 'video'] else abort(404)
    for base in base_paths:
        if os.path.exists(base) and os.path.exists(os.path.normpath(os.path.join(base, filename))): return send_from_directory(base, filename)
    abort(404)

@app.route('/api/browse')
def api_browse(): # フォルダ内容のブラウズ処理
    mode, req_path = request.args.get('mode', 'MUSIC'), request.args.get('path', '')
    root_paths, cover_paths = get_paths_for_mode(mode)
    is_root, items, metadata, breadcrumbs, cache = not bool(req_path), [], None, [{"name": "ROOT", "path": ""}], get_mode_cache(mode)
    if is_root:
        if cache and "ROOT" in cache: items = cache["ROOT"]
        else:
            for i, root_path in enumerate(root_paths):
                if os.path.exists(root_path): items.extend(logic.get_directory_items(root_path, root_path, cover_paths[i], all_tags, cover_map, mode) or [])
    else:
        if not is_safe_path(root_paths, req_path): return jsonify({"error": "Access Denied"}), 403
        active_idx = get_active_index(root_paths, req_path)
        if active_idx == -1: return jsonify({"error": "Path mapping failed"}), 404
        active_root, active_cover = root_paths[active_idx], cover_paths[active_idx]
        try:
            if os.path.relpath(req_path, active_root) != '.':
                current_acc_path = active_root
                for part in os.path.relpath(req_path, active_root).split(os.sep):
                    current_acc_path = os.path.join(current_acc_path, part)
                    breadcrumbs.append({"name": part, "path": current_acc_path})
        except ValueError: pass
        cached_data = next((v for k, v in cache.get("PATHS", {}).items() if os.path.normcase(os.path.normpath(k)) == os.path.normcase(os.path.normpath(req_path))), None) if cache else None
        if cached_data: items, metadata = cached_data.get("items", []), cached_data.get("metadata", None)
        else:
            items = logic.get_directory_items(req_path, active_root, active_cover, all_tags, cover_map, mode)
            metadata = logic.get_directory_metadata(req_path, active_root, active_cover, all_tags, cover_map, mode)
    return jsonify({"current_path": req_path, "is_root": is_root, "items": items, "metadata": metadata, "breadcrumbs": breadcrumbs})

@app.route('/api/search')
def api_search(): # 検索API
    mode, query, search_type = request.args.get('mode', 'MUSIC'), request.args.get('q', ''), request.args.get('type', 'keyword')
    if not query: return jsonify({"items": []})
    cache, items = get_mode_cache(mode), []
    if cache and "FLAT_ITEMS" in cache: # キャッシュを利用した高速検索
        if search_type == 'tag':
            tags_list = [t.strip().lower() for t in query.split(',') if t.strip()]
            items = [item for item in cache["FLAT_ITEMS"] if all(tag in [t.lower() for t in item.get('tags', [])] for tag in tags_list)]
        else:
            keyword_lower = query.lower()
            items = [item for item in cache["FLAT_ITEMS"] if keyword_lower in item['name'].lower() or any(keyword_lower in t.lower() for t in item.get('tags', []))]
        return jsonify({"items": sorted(items, key=lambda x: logic.natural_sort_key(x['name']))})
    root_paths, cover_paths = get_paths_for_mode(mode) # リアルタイム検索へのフォールバック
    for i, root_path in enumerate(root_paths):
        if not os.path.exists(root_path): continue
        cover_path = cover_paths[i]
        drive_items = logic.search_by_tag(root_path, cover_path, all_tags, cover_map, [t.strip() for t in query.split(',') if t.strip()], mode) if search_type == 'tag' else logic.search_all(root_path, cover_path, all_tags, cover_map, query, mode)
        if isinstance(drive_items, list): items.extend(drive_items)
    return jsonify({"items": sorted(items, key=lambda x: logic.natural_sort_key(x['name']))})

@app.route('/api/update_cache', methods=['POST'])
def api_update_cache(): # キャッシュの更新
    mode = request.get_json().get('mode', 'MUSIC')
    root_paths, cover_paths = get_paths_for_mode(mode)
    return jsonify(logic.build_and_save_cache(mode, root_paths, cover_paths, logic.load_tags(TAGS_FILE_PATH), logic.load_cover_map(MAP_FILE_PATH), DATA_DIR))

@app.route('/api/record_view', methods=['POST'])
def api_record_view(): # 閲覧回数の記録
    data = request.get_json()
    return jsonify(logic.update_view_count(STATS_FILE_PATH, data['mode'], data['item_key'], data.get('identifier')))

@app.route('/api/tags', methods=['GET', 'POST'])
def api_tags(): # タグの取得・保存
    global all_tags
    if request.method == 'GET': return jsonify(all_tags)
    with TAGS_LOCK:
        logic.save_tags(TAGS_FILE_PATH, request.get_json())
        all_tags = logic.load_tags(TAGS_FILE_PATH)
    return jsonify({"status": "success"})

@app.route('/api/rename_item', methods=['POST'])
def api_rename(): # アイテム名の変更処理
    data = request.get_json()
    root_paths, cover_paths = get_paths_for_mode(data['mode'])
    active_idx = get_active_index(root_paths, data['full_path'])
    if active_idx == -1: return jsonify({"status": "error", "message": "Path not found"}), 404
    global all_tags
    with TAGS_LOCK:
        res, code = logic.rename_item_and_update_tags(data['mode'], data['full_path'], data['new_name'], TAGS_FILE_PATH, root_paths[active_idx], cover_paths[active_idx], all_tags, cover_map)
        if res['status'] == 'success': all_tags = logic.load_tags(TAGS_FILE_PATH)
    return jsonify(res), code

@app.route('/api/open_folder')
def api_open_folder(): # ローカルフォルダを開く
    path = request.args.get('path', '')
    if not os.path.exists(path): return jsonify({"status": "error"}), 404
    try:
        os.startfile(path) if sys.platform == 'win32' else subprocess.call(['xdg-open', path])
        return jsonify({"status": "success"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/rate_music', methods=['POST'])
def api_rate_music(): # 音楽の評価を保存
    data = request.get_json()
    if not data or 'key' not in data or 'value' not in data: return jsonify({"status": "error", "message": "Invalid data"}), 400
    return jsonify(logic.save_music_rating(MUSIC_RATINGS_PATH, data))

@app.route('/api/structured_stats')
def api_stats(): return jsonify(logic.get_structured_stats_from_cache(STATS_FILE_PATH, DATA_DIR, limit=int(request.args.get('limit', 25)))) # 統計データを取得

@app.route('/api/export_data', methods=['POST'])
def api_export_data(): # データの構造エクスポート
    res = logic.export_tree_structure_from_cache(MODES_CONFIG, DATA_DIR)
    return jsonify(res) if res['status'] == 'success' else (jsonify(res), 500)

@app.route('/api/clean_data', methods=['POST'])
def api_clean_data(): # 無効なデータのクリーンアップ
    global all_tags
    with TAGS_LOCK:
        res = logic.clean_orphaned_data(BASE_DIRS, MODES_CONFIG, TAGS_FILE_PATH, STATS_FILE_PATH)
        if res.get('status') == 'success': all_tags = logic.load_tags(TAGS_FILE_PATH)
    return jsonify(res)

if __name__ == '__main__': app.run(host='0.0.0.0', port=5000, debug=True)