# -*- coding: utf-8 -*-
import os, sys, subprocess, traceback

# ==========================================
# rclone クラウド同期モジュール (v2)
# 【v2 修正内容】
# 1. stdin を DEVNULL に固定：crypt リモート等で rclone が対話入力
#    (設定パスワード / トークン更新確認) を待って永久にハングするのを防止。
#    入力が必要な場合は即エラー終了し、その内容を stderr から報告する。
# 2. remote_exists を三値化：「存在する / 存在しない / 確認失敗」を区別。
#    従来は rclone 実行エラーも「未バックアップ」と誤報していた。
#    rclone の終了コード 3 (directory not found) / 4 (file not found) のみを
#    「存在しない」と判定し、それ以外の失敗は check_failed として操作を中止する。
# 3. 全公開関数を try/except で完全包囲し、例外を絶対に外へ漏らさない。
#    (ローカル操作は既に完了しているため、同期失敗で 500 を返してはならない)
# 4. 実行した rclone コマンドと stderr をサーバーコンソールへログ出力。
#   status 一覧:
#     disabled      : 同期機能が無効 (config で enabled=false)
#     skipped       : 同期対象外 (白名单外 / THUMBNAILS 等)
#     not_backed_up : クラウドに元パスが存在しない (未バックアップ) → 操作不要
#     check_failed  : 存在確認自体が失敗 (rclone エラー) → 安全のため操作せず
#     moved         : クラウド側の moveto 成功
#     deleted       : クラウド側の purge / deletefile 成功
#     error         : rclone 実行失敗 (次回のバックアップ/差分チェックで整合させる)
# ==========================================

def _run_rclone(args, cfg, timeout=None): # rclone コマンドの実行ラッパー
    exe = cfg.get("rclone_exe", "rclone")
    env = os.environ.copy()
    if cfg.get("config_password"): env["RCLONE_CONFIG_PASS"] = cfg["config_password"] # 暗号化された rclone.conf を非対話で復号
    kwargs = {
        "stdin": subprocess.DEVNULL, # 【重要】対話プロンプト待ちによるハングを根絶
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "encoding": "utf-8", "errors": "replace", "env": env,
        "timeout": timeout or cfg.get("timeout_seconds", 300)
    }
    if sys.platform == 'win32': kwargs["creationflags"] = 0x08000000 # CREATE_NO_WINDOW: サーバー実行中に黒いコンソール窓を出さない
    print(f"[rclone_sync] 実行: {exe} {' '.join(args)}")
    result = subprocess.run([exe] + args, **kwargs)
    if result.returncode != 0: print(f"[rclone_sync] 終了コード {result.returncode}: {(result.stderr or '').strip()[-500:]}")
    return result

def local_to_remote(full_path, base_dirs, cfg): # ローカル絶対パス → クラウドパスへの変換 (対象外なら (None, 理由))
    abs_path = os.path.normpath(full_path)
    matched_root = None
    for base in base_dirs: # どの base_dir に属するか判定
        base_norm = os.path.normpath(base)
        if abs_path.lower() == base_norm.lower() or abs_path.lower().startswith(base_norm.lower() + os.sep):
            matched_root = base_norm; break
    if matched_root is None: return None, "パスがどの base_dir にも属していません"
    rel = os.path.relpath(abs_path, matched_root).replace(os.sep, "/")
    for kw in cfg.get("exclude_keywords", ["THUMBNAILS"]): # サムネイル等はバックアップ対象外
        if f"/{kw}/" in f"/{rel}/": return None, f"除外キーワード '{kw}' を含むため対象外"
    matched_folder = None
    for folder in cfg.get("upload_list", []): # アップロード白名单に属するか判定
        folder_posix = folder.replace("\\", "/")
        if rel == folder_posix or rel.startswith(folder_posix + "/"): matched_folder = folder_posix; break
    if matched_folder is None: return None, "UPLOAD_LIST 白名单の対象外パスです"
    return cfg.get("remote_name", "").rstrip("/") + "/" + rel, None

def remote_exists(remote_full, cfg): # クラウド側の存在確認 → 'exists' / 'missing' / (None=確認失敗, エラー文)
    try:
        result = _run_rclone(["lsjson", remote_full, "--stat", "--no-mimetype", "--no-modtime"], cfg, timeout=60)
        if result.returncode == 0: return 'exists', None
        if result.returncode in (3, 4): return 'missing', None # 3=directory not found / 4=file not found
        return None, (result.stderr or "").strip()[-300:] or f"rclone 終了コード {result.returncode}"
    except subprocess.TimeoutExpired:
        return None, "存在確認がタイムアウトしました (ネットワーク/認証を確認してください)"
    except FileNotFoundError:
        return None, "rclone コマンドが見つかりません (Path を確認してください)"
    except Exception as e:
        return None, str(e)

def _do_sync(kind, local_paths, base_dirs, cfg, is_dir=False): # rename/delete 共通の同期本体
    remote_full, reason = local_to_remote(local_paths[0], base_dirs, cfg)
    if remote_full is None: return {"status": "skipped", "message": reason}
    state, err = remote_exists(remote_full, cfg)
    if state is None: return {"status": "check_failed", "message": f"クラウド側の存在確認に失敗したため操作を中止しました: {err}"}
    if state == 'missing': return {"status": "not_backed_up", "message": f"クラウドに元パスが存在しません: {remote_full}"}
    if kind == 'rename':
        remote_new, reason = local_to_remote(local_paths[1], base_dirs, cfg)
        if remote_new is None: return {"status": "skipped", "message": reason}
        result = _run_rclone(["moveto", remote_full, remote_new], cfg)
        if result.returncode != 0: return {"status": "error", "message": (result.stderr or "").strip()[-300:] or "rclone が非ゼロ終了コードを返しました"}
        return {"status": "moved", "remote_old": remote_full, "remote_new": remote_new}
    else:
        result = _run_rclone(["purge" if is_dir else "deletefile", remote_full], cfg)
        if result.returncode != 0: return {"status": "error", "message": (result.stderr or "").strip()[-300:] or "rclone が非ゼロ終了コードを返しました"}
        return {"status": "deleted", "remote": remote_full}

def sync_rename(old_full, new_full, base_dirs, cfg): # リネームのクラウド反映 (rclone moveto) ※例外を外に漏らさない
    if not cfg.get("enabled", False): return {"status": "disabled"}
    try:
        return _do_sync('rename', [old_full, new_full], base_dirs, cfg)
    except FileNotFoundError:
        return {"status": "error", "message": "rclone コマンドが見つかりません (Path を確認してください)"}
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

def sync_delete(full_path, is_dir, base_dirs, cfg): # 削除のクラウド反映 (フォルダ=purge / ファイル=deletefile) ※例外を外に漏らさない
    if not cfg.get("enabled", False): return {"status": "disabled"}
    try:
        return _do_sync('delete', [full_path], base_dirs, cfg, is_dir=is_dir)
    except FileNotFoundError:
        return {"status": "error", "message": "rclone コマンドが見つかりません (Path を確認してください)"}
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}