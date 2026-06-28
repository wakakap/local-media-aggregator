import os, re, json, time, shutil, threading, tempfile
from PIL import Image
from icoextract import IconExtractor

CHROMEDRIVER_PATH = os.getenv("CHROMEDRIVER_PATH")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json") # 設定ファイルのパス
try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f: SYSTEM_CONFIG = json.load(f) # 設定をロード
except Exception as e:
    print(f"Warning: Cannot load config.json, using empty fallback. Error: {e}")
    SYSTEM_CONFIG = {"extensions": {}, "exe_blacklist": []}

ext_cfg = SYSTEM_CONFIG.get("extensions", {})
IMAGE_EXTENSIONS = set(ext_cfg.get("image", ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp', '.ico']))
TEXT_EXTENSIONS = set(ext_cfg.get("text", ['.txt']))
AUDIO_EXTENSIONS = set(ext_cfg.get("audio", ['.mp3', '.m4a', '.flac', '.wav', '.ogg', '.webm']))
VIDEO_EXTENSIONS = set(ext_cfg.get("video", ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv']))
SUBTITLE_EXTENSIONS = set(ext_cfg.get("subtitle", ['.srt', '.ass', '.vtt']))
EBOOK_EXTENSIONS = set(ext_cfg.get("ebook", ['.epub']))
EXECUTABLE_EXTENSIONS = set(ext_cfg.get("executable", ['.iso', '.mdf', '.mds', '.exe', '.lnk', '.url']))
EXE_BLACKLIST = SYSTEM_CONFIG.get("exe_blacklist", [])
VALID_EXTS = IMAGE_EXTENSIONS | TEXT_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS | EBOOK_EXTENSIONS | EXECUTABLE_EXTENSIONS # 有効な全拡張子

STATS_LOCK = threading.Lock()
MUSIC_RATINGS_LOCK = threading.Lock()

_stats_cache = {}       # メモリ上の統計キャッシュ
_stats_dirty = False    # 未保存の変更フラグ
_flush_timer = None     # 遅延書き込みタイマー

def load_stats(stats_file_path):
    if not os.path.exists(stats_file_path):
        print(f"[stats] ファイルが見つかりません: {stats_file_path}")
        return {}
    try:
        with open(stats_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"[stats] 読み込み成功: {len(data)} エントリ")
            return data
    except Exception as e:
        print(f"[stats] 読み込みエラー: {e}")  # ← BOMや文字化けの場合ここに出る
        return {}

def save_stats(stats_file_path, data): # 統計データの保存（アトミック書き込み）
    import tempfile
    try:
        dir_name = os.path.dirname(stats_file_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, stats_file_path)
        except:
            if os.path.exists(tmp_path): os.remove(tmp_path)
            raise
    except Exception as e: print(f"Save stats error: {e}")

def get_first_image_in_directory(directory_path): # フォルダ内の最初の画像を取得
    if not os.path.isdir(directory_path): return None
    try:
        files = os.listdir(directory_path)
        images = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]
        images = [f for f in images if os.path.isfile(os.path.join(directory_path, f))]
        if not images: return None
        images.sort(key=natural_sort_key)
        return images[0]
    except: return None

def natural_sort_key(s): # 自然順ソートのキー生成
    removal_pattern = r'\([^)]*\)|\[[^\]]*\]'
    cleaned_s = re.sub(removal_pattern, '', s)
    match = re.search(r'\d+', cleaned_s)
    if not match: return (cleaned_s.strip().lower(), 0)
    text_part = cleaned_s[:match.start()].strip().lower()
    num_part = int(match.group(0))
    return (text_part, num_part)

def is_gallery(directory_path): # ギャラリーフォルダか判定
    if not os.path.isdir(directory_path): return False
    try:
        count = 0
        for item_name in os.listdir(directory_path):
            if os.path.splitext(item_name)[1].lower() in IMAGE_EXTENSIONS: return True
            count += 1
            if count > 10: break 
    except OSError: return False
    return False

def get_file_media_type(filename): # メディアタイプを取得
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS: return 'image'
    if ext in VIDEO_EXTENSIONS: return 'video'
    if ext in AUDIO_EXTENSIONS: return 'audio'
    if ext in SUBTITLE_EXTENSIONS: return 'subtitle'
    if ext in EBOOK_EXTENSIONS: return 'epub'
    if ext in TEXT_EXTENSIONS: return 'text'
    return 'unknown'

def load_tags(tags_file_path): # タグデータの読み込み
    try:
        if os.path.exists(tags_file_path):
            with open(tags_file_path, 'r', encoding='utf-8') as f: return json.load(f)
        return {}
    except: return {}

def load_cover_map(map_file_path): # カバーマップの読み込み
    cover_map = []
    if not os.path.exists(map_file_path): return cover_map
    try:
        with open(map_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or ',' not in line: continue
                parts = line.split(',', 1)
                try:
                    regex_obj = re.compile(f"^{parts[0].strip()}$", re.IGNORECASE)
                    cover_map.append((regex_obj, parts[1].strip()))
                except: continue
    except: pass
    return cover_map

def find_cover_filename(cover_path, base_name, cover_map): # カバー画像ファイル名を検索
    if not os.path.isdir(cover_path): return None
    for ext in IMAGE_EXTENSIONS:
        if os.path.exists(os.path.join(cover_path, base_name + ext)): return base_name + ext
    for regex_obj, cover_template in cover_map:
        match = regex_obj.match(base_name)
        if match:
            try:
                escaped_vars = {k: re.escape(v) for k, v in match.groupdict().items()}
                final_regex = cover_template.format(**escaped_vars)
                final_regex_obj = re.compile(f"^{final_regex}$", re.IGNORECASE)
                for f in os.listdir(cover_path):
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS and final_regex_obj.match(os.path.splitext(f)[0]): return f
            except: continue
    return None

def get_or_create_thumbnail(source_path, cover_root_path, thumbnail_base_name, size=(300, 450)): # サムネイルの取得または生成
    if not source_path or not os.path.exists(source_path): return None
    thumbnail_dir = os.path.join(cover_root_path, "THUMBNAILS")
    thumbnail_filename = f"{thumbnail_base_name}_{size[0]}x{size[1]}.jpg"
    thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)
    if os.path.exists(thumbnail_path): return thumbnail_filename
    try:
        os.makedirs(thumbnail_dir, exist_ok=True)
        with Image.open(source_path) as img:
            img.thumbnail(size, Image.Resampling.LANCZOS)
            if img.mode != 'RGB': img = img.convert("RGB")
            img.save(thumbnail_path, "jpeg", quality=85)
        return thumbnail_filename
    except Exception as e:
        print(f"Thumbnail generation failed for {source_path}: {e}")
        return None

def get_game_cover_thumbnail(full_path, cover_path, base_name): # ゲーム用サムネイルの抽出
    if not os.path.isdir(full_path): return None
    thumbnail_dir = os.path.join(cover_path, "THUMBNAILS")
    try:
        priority_exts = ['.ico', '.exe', '.jpg', '.jpeg', '.png', '.bmp']
        found_files = {ext: [] for ext in priority_exts}
        for f in os.listdir(full_path):
            f_lower = f.lower()
            ext = os.path.splitext(f_lower)[1]
            if ext in priority_exts:
                if ext == '.exe' and any(black_word.lower() in f_lower for black_word in EXE_BLACKLIST): continue
                found_files[ext].append(f)
        best_ext, best_file = None, None
        for ext in priority_exts:
            if found_files[ext]:
                found_files[ext].sort(key=natural_sort_key)
                best_ext = ext
                best_file = os.path.join(full_path, found_files[ext][0])
                break
        if not best_file: return None
        if best_ext != '.exe': return get_or_create_thumbnail(best_file, cover_path, base_name)
        target_exe = best_file
        thumbnail_filename = f"{base_name}_exe.png"
        thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)
        if os.path.exists(thumbnail_path): return thumbnail_filename
        if os.path.getsize(target_exe) == 0: return None
        os.makedirs(thumbnail_dir, exist_ok=True)
        temp_ico = os.path.join(thumbnail_dir, f"{base_name}_temp.ico")
        try:
            extractor = IconExtractor(target_exe)
            extractor.export_icon(temp_ico)
        except Exception:
            if os.path.getsize(target_exe) > 100 * 1024 * 1024: return None
            fd, safe_temp_exe = tempfile.mkstemp(suffix=".exe")
            os.close(fd) 
            try:
                shutil.copy2(target_exe, safe_temp_exe)
                extractor = IconExtractor(safe_temp_exe)
                extractor.export_icon(temp_ico)
            except Exception: return None
            finally:
                if os.path.exists(safe_temp_exe): os.remove(safe_temp_exe)
        if os.path.exists(temp_ico):
            with Image.open(temp_ico) as img: img.save(thumbnail_path, "PNG")
            os.remove(temp_ico) 
            return thumbnail_filename
        else: return None
    except Exception as e: 
        print(f"Cover extraction failed for {full_path}: {e}")
        return None

def _create_item_data(full_path, root_path, cover_path, all_tags, cover_map, mode, allow_thumbnail_gen=True): # アイテムデータの生成
    item_name = os.path.basename(full_path)
    is_dir = os.path.isdir(full_path)
    name_no_ext = item_name if is_dir else os.path.splitext(item_name)[0]
    media_path = os.path.relpath(full_path, root_path).replace('\\', '/')
    media_path_no_ext = media_path if is_dir else os.path.splitext(media_path)[0]
    composite_key = f"{mode.upper()}:{media_path_no_ext}"
    is_savedata = (item_name == "00_SAVEDATA")

    data = {
        "name": item_name, "name_no_ext": name_no_ext, "full_path": full_path,
        "media_path": media_path, "is_dir": is_dir, "mode": mode
    }

    if is_dir:
        if is_savedata: data["is_gallery"] = False
        else:
            data["is_gallery"] = is_gallery(full_path)
            original_cover_name = find_cover_filename(cover_path, name_no_ext, cover_map)
            source_image_path = None
            if original_cover_name:
                source_image_path = os.path.join(cover_path, original_cover_name)
                data["cover_filename"] = original_cover_name
                data["cover_source"] = "global"
            elif mode in ['GAME']:
                game_cover_name = get_game_cover_thumbnail(full_path, cover_path, name_no_ext)
                if game_cover_name: data["thumbnail_filename"] = game_cover_name
            else:
                local_first_img = get_first_image_in_directory(full_path)
                if local_first_img:
                    source_image_path = os.path.join(full_path, local_first_img)
                    data["cover_filename"] = os.path.join(media_path, local_first_img).replace('\\', '/')
                    data["cover_source"] = "local"
            if source_image_path and allow_thumbnail_gen:
                data["thumbnail_filename"] = get_or_create_thumbnail(source_image_path, cover_path, name_no_ext)
    else: data["media_type"] = get_file_media_type(item_name)
    return data

def get_directory_items(current_path, root_path, cover_path, all_tags, cover_map, mode): # フォルダ内アイテムの取得
    try:
        rel_path = os.path.relpath(current_path, root_path)
        depth = 0 if rel_path == '.' else len(rel_path.split(os.sep))
        allow_gen = (depth == 0)
        items = []
        for item_name in os.listdir(current_path):
            if item_name.startswith('.'): continue
            full_path = os.path.join(current_path, item_name)
            is_dir = os.path.isdir(full_path)
            if not is_dir:
                ext = os.path.splitext(item_name)[1].lower()
                if ext not in VALID_EXTS: continue
            items.append(_create_item_data(full_path, root_path, cover_path, all_tags, cover_map, mode, allow_thumbnail_gen=allow_gen))
        dirs = sorted([x for x in items if x['is_dir']], key=lambda x: natural_sort_key(x['name']))
        files = sorted([x for x in items if not x['is_dir']], key=lambda x: natural_sort_key(x['name']))
        return dirs + files
    except Exception as e: return {"error": str(e)}

def get_directory_metadata(full_path, root_path, cover_path, all_tags, cover_map, mode): # フォルダメタデータの取得
    if not os.path.isdir(full_path): return None
    dir_name = os.path.basename(full_path)
    rel_dir = os.path.relpath(full_path, root_path).replace('\\', '/')
    if dir_name == "00_SAVEDATA": return {"name": dir_name, "name_no_ext": dir_name, "media_path": rel_dir, "cover_filename": None, "cover_source": None, "tags": []}
    composite_key = f"{mode.upper()}:{rel_dir}"
    tags = all_tags.get(composite_key, [])
    found_cover, cover_source = None, None
    
    if mode in ['GAME']:
        game_cover_name = get_game_cover_thumbnail(full_path, cover_path, dir_name)
        if game_cover_name: found_cover, cover_source = f"THUMBNAILS/{game_cover_name}", "global"
    else:
        local_first_img = get_first_image_in_directory(full_path)
        if local_first_img:
            rel_dir = os.path.relpath(full_path, root_path).replace('\\', '/')
            found_cover, cover_source = os.path.join(rel_dir, local_first_img).replace('\\', '/'), "local"
    if not found_cover:
        current_check_path = full_path
        while True:
            if not os.path.abspath(current_check_path).startswith(os.path.abspath(root_path)): break
            base_name = os.path.basename(current_check_path)
            cover = find_cover_filename(cover_path, base_name, cover_map)
            if cover:
                found_cover, cover_source = cover, "global"
                break 
            if os.path.abspath(current_check_path) == os.path.abspath(root_path): break
            parent_dir = os.path.dirname(current_check_path)
            if parent_dir == current_check_path: break 
            current_check_path = parent_dir
    return {"name": dir_name, "name_no_ext": dir_name, "media_path": rel_dir, "cover_filename": found_cover, "cover_source": cover_source}

def search_all(root_path, cover_path, all_tags, cover_map, keyword, mode): # 全体検索
    keyword_lower = keyword.lower()
    found_paths = set()
    try:
        for name in os.listdir(root_path):
            if name.startswith('.'): continue
            full_path = os.path.join(root_path, name)
            if keyword_lower in name.lower(): 
                found_paths.add(full_path)
                continue
            rel_path = name
            item_key = rel_path if os.path.isdir(full_path) else os.path.splitext(rel_path)[0]
            composite_key = f"{mode.upper()}:{item_key}"
            if any(keyword_lower in tag.lower() for tag in all_tags.get(composite_key, [])): found_paths.add(full_path)
    except Exception: pass
    results = [_create_item_data(p, root_path, cover_path, all_tags, cover_map, mode) for p in found_paths]
    return results

def search_by_tag(root_path, cover_path, all_tags, cover_map, tag_list, mode): # タグによる検索
    target_keys = {k.split(':', 1)[1] for k, v in all_tags.items() if k.startswith(f"{mode.upper()}:") and all(t in v for t in tag_list)}
    found_paths = set()
    for root, dirs, files in os.walk(root_path):
        for name in dirs + files:
            full_path = os.path.join(root, name)
            rel_path = os.path.relpath(full_path, root_path).replace('\\', '/')
            item_key = rel_path if os.path.isdir(full_path) else os.path.splitext(rel_path)[0]
            if item_key in target_keys: found_paths.add(full_path)
        dirs[:] = [d for d in dirs if not is_gallery(os.path.join(root, d))]
    results = [_create_item_data(p, root_path, cover_path, all_tags, cover_map, mode) for p in found_paths]
    return results

def get_stats_data(tags_file_path): # 統計データのパース
    stats = []
    all_tags = load_tags(tags_file_path)
    for key, tags in all_tags.items():
        total, pages, files = 0, {}, {}
        for t in tags:
            if not t.startswith('*'): continue
            content = t[1:]
            if content.isdigit(): total = int(content)
            elif content.startswith('p') and ':' in content:
                try: p, c = content.split(':', 1); pages[p[1:]] = int(c)
                except: pass
            elif ':' in content:
                try: name, c = content.rsplit(':', 1); files[name] = int(c) if c.isdigit() else 0
                except: pass
        if total > 0 or pages or files: stats.append({"item_key": key, "total_views": total, "page_views": pages, "file_views": files})
    return sorted(stats, key=lambda x: x['total_views'], reverse=True)

def get_structured_stats_data(stats_file_path, item_key_map, all_root_paths, limit=25): # 構造化統計データの取得
    stats = load_stats(stats_file_path)
    results = []
    for key, entry in stats.items():
        if ':' not in key: continue
        mode, item_name = key.split(':', 1)
        if mode != 'MANGA': continue
        if not entry.get('pages'): continue
        path_info = item_key_map.get(item_name)
        if not path_info: continue 
        full_path = path_info['path']
        folder_views = entry.get('total_views', 0)
        sub_nodes = []
        for p, v in entry.get('pages', {}).items():
            sub_nodes.append({"name": f"Page {p}", "views": v, "total": v, "isLeaf": True, "type": "page", "page_index": int(p) - 1})
        sub_nodes.sort(key=lambda x: x['total'], reverse=True)
        results.append({"name": item_name, "full_path": full_path, "mode": mode, "views": folder_views, "total": folder_views, "nodes": sub_nodes, "isLeaf": False})
    results.sort(key=lambda x: x['total'], reverse=True)
    if limit > 0: results = results[:limit]
    return results

def update_view_count(stats_file_path, mode, item_key, identifier=None): # 閲覧回数の更新（遅延書き込み）
    global _stats_dirty, _stats_cache, _flush_timer
    composite_key = f"{mode.upper()}:{item_key}"
    with STATS_LOCK:
        if not _stats_cache:
            _stats_cache.update(load_stats(stats_file_path))
        entry = _stats_cache.setdefault(composite_key, {"total_views": 0, "last_accessed": 0, "pages": {}, "files": {}})
        now = int(time.time())
        if (now - entry.get("last_accessed", 0)) > 60: entry["total_views"] += 1
        entry["last_accessed"] = now
        if isinstance(identifier, int):
            page_key = str(identifier + 1)
            entry["pages"][page_key] = entry["pages"].get(page_key, 0) + 1
        elif isinstance(identifier, str):
            entry["files"][identifier] = entry["files"].get(identifier, 0) + 1
        _stats_dirty = True
        if _flush_timer: _flush_timer.cancel()
        _flush_timer = threading.Timer(10, _flush_stats, args=[stats_file_path])
        _flush_timer.start()
    return {"status": "success"}

def _flush_stats(stats_file_path): # 遅延書き込みの実行（変更があった場合のみ）
    global _stats_dirty
    with STATS_LOCK:
        if not _stats_dirty: return
        save_stats(stats_file_path, _stats_cache)
        _stats_dirty = False

def save_tags(tags_file_path, tags_data): # タグデータの保存
    try:
        with open(tags_file_path, 'w', encoding='utf-8') as f: json.dump(tags_data, f, indent=4, ensure_ascii=False)
        return {"status": "success"}
    except Exception as e: return {"status": "error", "message": str(e)}

def rename_item_and_update_tags(mode, old_path, new_name, tags_path, root_path, cover_path, all_tags, cover_map): # アイテム名変更とタグ更新
    try:
        if not new_name or re.search(r'[\\/:*?"<>|]', new_name): return {"status": "error", "message": "Invalid name"}, 400
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        is_same_file_case_change = (os.path.normcase(old_path) == os.path.normcase(new_path))
        if not is_same_file_case_change and os.path.exists(new_path): return {"status": "error", "message": "File exists"}, 409
        is_dir = os.path.isdir(old_path)
        os.rename(old_path, new_path)
        old_rel_full = os.path.relpath(old_path, root_path).replace('\\', '/')
        new_rel_full = os.path.relpath(new_path, root_path).replace('\\', '/')
        old_rel = old_rel_full if is_dir else os.path.splitext(old_rel_full)[0]
        new_rel = new_rel_full if is_dir else os.path.splitext(new_rel_full)[0]
        
        old_prefix = f"{mode.upper()}:{old_rel}"
        new_prefix = f"{mode.upper()}:{new_rel}"

        tags = load_tags(tags_path)
        updated_tags = {}
        tags_changed = False
        for key, value in tags.items():
            if key == old_prefix or key.startswith(old_prefix + "/"):
                new_key = key.replace(old_prefix, new_prefix, 1)
                updated_tags[new_key] = value
                tags_changed = True
            else:
                updated_tags[key] = value

        if tags_changed:
            save_tags(tags_path, updated_tags)
            all_tags.clear()
            all_tags.update(updated_tags)

        new_item = _create_item_data(new_path, root_path, cover_path, tags, cover_map, mode)
        return {"status": "success", "new_item": new_item}, 200
    except Exception as e: return {"status": "error", "message": str(e)}, 500

def export_tree_structure(base_dirs, modes_config, data_dir): # ツリー構造のエクスポート
    output_filename = "library_structure.txt"
    output_path = os.path.join(data_dir, output_filename)
    lines = []
    lines.append(f"Library Structure Export - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 50)
    lines.append(".")
    sorted_modes = sorted(modes_config.keys())
    for i, mode in enumerate(sorted_modes):
        is_last_mode = (i == len(sorted_modes) - 1)
        mode_connector = "└── " if is_last_mode else "├── "
        lines.append(f"{mode_connector}{mode}")
        folder_name = modes_config[mode]['pages']
        all_items = []
        for base_dir in base_dirs:
            full_path = os.path.join(base_dir, folder_name)
            if os.path.exists(full_path) and os.path.isdir(full_path):
                try:
                    items = [f for f in os.listdir(full_path) if not f.startswith('.')]
                    drive_letter = os.path.splitdrive(base_dir)[0]
                    items = [f"[{drive_letter}] {f}" for f in items]
                    all_items.extend(items)
                except Exception as e: pass
        all_items.sort(key=natural_sort_key)
        child_prefix = "    " if is_last_mode else "│   "
        if not all_items:
            lines.append(f"{child_prefix}└── [Empty or Directory not found]")
            continue
        for j, item_name in enumerate(all_items):
            is_last_item = (j == len(all_items) - 1)
            item_connector = "└── " if is_last_item else "├── "
            lines.append(f"{child_prefix}{item_connector}{item_name}")
    try:
        with open(output_path, 'w', encoding='utf-8') as f: f.write("\n".join(lines))
        return {"status": "success", "file": output_path}
    except Exception as e: return {"status": "error", "message": str(e)}
    
def save_music_rating(music_ratings_path, data): # 音楽評価の保存
    with MUSIC_RATINGS_LOCK:
        current_tags = {}
        if os.path.exists(music_ratings_path):
            try:
                with open(music_ratings_path, 'r', encoding='utf-8') as f: current_tags = json.load(f)
            except Exception: current_tags = {}
        key, value = data.get('key'), data.get('value')
        if not key or not value: return {"status": "error", "message": "Missing key or value"}
        if key in current_tags:
            existing_data = current_tags[key]
            if isinstance(existing_data, list): existing_data.append(value)
            else: current_tags[key] = [existing_data, value]
        else: current_tags[key] = [value]
        try:
            with open(music_ratings_path, 'w', encoding='utf-8') as f: json.dump(current_tags, f, indent=4, ensure_ascii=False)
            return {"status": "success"}
        except Exception as e: return {"status": "error", "message": str(e)}

def build_and_save_cache(mode, root_paths, cover_paths, all_tags, cover_map, data_dir): # キャッシュの構築と保存
    import json
    cache_data = {"ROOT": [], "PATHS": {}, "FLAT_ITEMS": []}
    unique_items = {}
    for i, root_path in enumerate(root_paths):
        if not os.path.exists(root_path): continue
        cover_path = cover_paths[i]
        items = get_directory_items(root_path, root_path, cover_path, all_tags, cover_map, mode)
        if isinstance(items, list):
            cache_data["ROOT"].extend(items)
            for it in items: unique_items[it["full_path"]] = it
        for root_dir, dirs, files in os.walk(root_path):
            rel_path = os.path.relpath(root_dir, root_path)
            depth = 0 if rel_path == '.' else len(rel_path.split(os.sep))
            if mode == 'GAME' and depth >= 1: dirs[:] = []
            elif mode != 'GAME': dirs[:] = [d for d in dirs if not is_gallery(os.path.join(root_dir, d))]
            items = get_directory_items(root_dir, root_path, cover_path, all_tags, cover_map, mode)
            metadata = get_directory_metadata(root_dir, root_path, cover_path, all_tags, cover_map, mode)
            if isinstance(items, list):
                normalized_key = os.path.normpath(root_dir)
                cache_data["PATHS"][normalized_key] = {"items": items, "metadata": metadata}
                for it in items: unique_items[it["full_path"]] = it
    cache_data["FLAT_ITEMS"] = list(unique_items.values())
    cache_file = os.path.join(data_dir, f"{mode}_cache.json")
    try:
        with open(cache_file, 'w', encoding='utf-8') as f: json.dump(cache_data, f, ensure_ascii=False, indent=2)
        return {"status": "success"}
    except Exception as e: return {"status": "error", "message": str(e)}

def get_structured_stats_from_cache(stats_file_path, data_dir, limit=20): # キャッシュからの構造化統計取得
    stats = load_stats(stats_file_path)
    results = []
    caches = {}
    try:
        for filename in os.listdir(data_dir):
            if filename.endswith('_cache.json'):
                mode_name = filename.replace('_cache.json', '')
                with open(os.path.join(data_dir, filename), 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    path_map = {}
                    for item in cache_data.get("FLAT_ITEMS", []): path_map[item["media_path"]] = item["full_path"]
                    caches[mode_name] = path_map
    except Exception as e: print(f"Error loading caches for stats: {e}")
    for key, entry in stats.items():
        if ':' not in key: continue
        mode, item_name = key.split(':', 1)
        folder_views = entry.get('total_views', 0)
        sub_nodes = []
        if 'pages' in entry and entry['pages']:
            for p, v in entry['pages'].items(): sub_nodes.append({"name": f"Page {p}", "views": v, "total": v, "isLeaf": True, "type": "page", "page_index": int(p) - 1})
        elif 'files' in entry and entry['files']:
            for f, v in entry['files'].items(): sub_nodes.append({"name": f, "views": v, "total": v, "isLeaf": True, "type": "file"})
        if not sub_nodes and folder_views == 0: continue
        if '/' in item_name:
            parts = item_name.split('/')
            parent_name, child_name = parts[0], parts[-1]
            display_name = child_name if parent_name in child_name else item_name
        else: display_name = item_name
        path_map = caches.get(mode, {})
        full_path = path_map.get(item_name)
        results.append({"name": display_name, "full_path": full_path or item_name, "mode": mode, "views": folder_views, "total": folder_views, "nodes": sub_nodes, "isLeaf": False})
    return results

def export_tree_structure_from_cache(modes_config, data_dir): # キャッシュからのツリー構造エクスポート
    output_filename = "library_structure.txt"
    output_path = os.path.join(data_dir, output_filename)
    lines = []
    lines.append(f"Library Structure Export (Powered by Cache) - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 50)
    lines.append(".")
    sorted_modes = sorted(modes_config.keys())
    for i, mode in enumerate(sorted_modes):
        is_last_mode = (i == len(sorted_modes) - 1)
        mode_connector = "└── " if is_last_mode else "├── "
        lines.append(f"{mode_connector}{mode}")
        child_prefix = "    " if is_last_mode else "│   "
        cache_file = os.path.join(data_dir, f"{mode}_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f: cache_data = json.load(f)
                all_items = cache_data.get("ROOT", [])
                all_items.sort(key=lambda x: natural_sort_key(x['name']))
                if not all_items:
                    lines.append(f"{child_prefix}└── [Empty]")
                    continue
                for j, item in enumerate(all_items):
                    is_last_item = (j == len(all_items) - 1)
                    item_connector = "└── " if is_last_item else "├── "
                    drive_letter = os.path.splitdrive(item.get("full_path", ""))[0]
                    display_name = f"[{drive_letter}] {item['name']}" if drive_letter else item['name']
                    lines.append(f"{child_prefix}{item_connector}{display_name}")
            except Exception as e: lines.append(f"{child_prefix}└── [Error reading cache: {str(e)}]")
        else: lines.append(f"{child_prefix}└── [No cache found. Please click 'Update Cache' first]")
    try:
        with open(output_path, 'w', encoding='utf-8') as f: f.write("\n".join(lines))
        return {"status": "success", "file": output_path}
    except Exception as e: return {"status": "error", "message": str(e)}

def clean_orphaned_data(base_dirs, modes_config, tags_path, stats_path): # 無効なデータのクリーンアップ
    unreachable = []
    for base_dir in base_dirs:
        if not os.path.exists(base_dir):
            unreachable.append(base_dir)
    if unreachable:
        return {
            "status": "error",
            "message": f"以下のドライブが接続されていないため中止しました: {unreachable}"
        }
    valid_keys = set()
    for mode, config in modes_config.items():
        folder_name = config['pages']
        for base_dir in base_dirs:
            root_path = os.path.join(base_dir, folder_name)
            if not os.path.exists(root_path): continue
            for root, dirs, files in os.walk(root_path):
                for item in dirs + files:
                    if item.startswith('.'): continue
                    full_path = os.path.join(root, item)
                    is_dir = os.path.isdir(full_path)
                    if not is_dir:
                        ext = os.path.splitext(item)[1].lower()
                        if ext not in VALID_EXTS: continue
                    rel_path = os.path.relpath(full_path, root_path).replace('\\', '/')
                    item_key = rel_path if is_dir else os.path.splitext(rel_path)[0]
                    composite_key = f"{mode.upper()}:{item_key}"
                    valid_keys.add(composite_key)

    def filter_data(data_dict):
        cleaned = {}
        removed_count = 0
        for k, v in data_dict.items():
            if ':' in k and k.split(':', 1)[0] in modes_config:
                if k in valid_keys: cleaned[k] = v
                else: removed_count += 1
            else: cleaned[k] = v
        return cleaned, removed_count

    try:
        date_str = time.strftime('%Y%m%d')
        if os.path.exists(tags_path):
            t_dir, t_name = os.path.split(tags_path)
            t_base, t_ext = os.path.splitext(t_name)
            tags_backup_path = os.path.join(t_dir, f"{t_base}_{date_str}{t_ext}")
            shutil.copy2(tags_path, tags_backup_path)
        if os.path.exists(stats_path):
            s_dir, s_name = os.path.split(stats_path)
            s_base, s_ext = os.path.splitext(s_name)
            stats_backup_path = os.path.join(s_dir, f"{s_base}_{date_str}{s_ext}")
            shutil.copy2(stats_path, stats_backup_path)
            
        tags = load_tags(tags_path)
        cleaned_tags, tags_removed = filter_data(tags)
        save_tags(tags_path, cleaned_tags)
        
        with STATS_LOCK:
            stats = load_stats(stats_path)
            cleaned_stats, stats_removed = filter_data(stats)
            save_stats(stats_path, cleaned_stats)
            
        return {"status": "success", "tags_removed": tags_removed, "stats_removed": stats_removed}
    except Exception as e: return {"status": "error", "message": str(e)}