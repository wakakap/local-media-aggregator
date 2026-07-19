# -*- coding: utf-8 -*-
import os, sys, json, time, queue, tempfile, threading, subprocess
import rclone_sync as rsync

# ==========================================
# クラウドバックアップ管理モジュール (v1)
# 設計方針:
# 1. 真相源は data/backup_status.json (キー = "MODE:media_path", 値 = 完了情報)。
#    キャッシュ再構築とは完全独立のため、再スキャンしても状態は失われず、
#    新規スキャンされた作品はキー不在 = 「未バックアップ (灰)」として自然に扱われる。
#    クラウド側のスキャンは一切行わない。
# 2. アップロードは常駐ワーカースレッド (既定 1 本、config の max_concurrent_uploads で変更可)
#    が queue から取り出して実行する。Flask のリクエストスレッドを一切ブロックしないため、
#    バックアップ中も閲覧・再生は通常通り動作する (物理的なディスク/回線帯域のみ共有)。
# 3. 削除・リネームとの競合対策: cancel_under() が対象パスに関わる
#    queued タスクを破棄し、uploading 中の rclone プロセスを terminate して終了を待つ。
#    呼び出し側 (app.py) はローカル削除/リネームの「前」にこれを呼ぶこと。
#   タスク state 一覧: queued / uploading / done / error / canceled
# ==========================================

_LOCK = threading.Lock()          # _TASKS と _DONE の保護
_TASKS = {}                       # key -> {state, message, full_path, proc, ts}
_QUEUE = queue.Queue()            # 実行待ちタスク
_WORKERS = []                     # 起動済みワーカースレッド
_DONE = {}                        # key -> {"time": "..."} 完了済みバックアップ (永続化対象)
_STATUS_PATH = None               # backup_status.json のパス
_FINISHED_TTL = 900               # 終了済みタスクを /api/status に載せ続ける秒数

def init(data_dir): # 起動時に永続化済みステータスをロード
    global _STATUS_PATH, _DONE
    _STATUS_PATH = os.path.join(data_dir, "backup_status.json")
    try:
        if os.path.exists(_STATUS_PATH):
            with open(_STATUS_PATH, 'r', encoding='utf-8') as f: _DONE = json.load(f)
            print(f"[backup] ステータス読み込み: {len(_DONE)} 件")
    except Exception as e:
        print(f"[backup] ステータス読み込み失敗 (空で継続): {e}")
        _DONE = {}

def _persist(): # 完了ステータスのアトミック保存 (_LOCK 保持中に呼ぶこと)
    if not _STATUS_PATH: return
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_STATUS_PATH), suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(_DONE, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _STATUS_PATH)
    except Exception as e: print(f"[backup] ステータス保存失敗: {e}")

def _ensure_workers(cfg): # ワーカーを遅延起動 (既定は同時 1 作品ずつ)
    with _LOCK:
        need = max(1, int(cfg.get("max_concurrent_uploads", 1)))
        while len(_WORKERS) < need:
            t = threading.Thread(target=_worker_loop, daemon=True, name=f"backup-worker-{len(_WORKERS)}")
            t.start(); _WORKERS.append(t)

def _set(key, state, message=""): # タスク状態の更新
    with _LOCK:
        t = _TASKS.get(key)
        if t: t["state"], t["message"], t["ts"] = state, message, time.time()

def enqueue(key, full_path, is_dir, base_dirs, cfg): # バックアップ要求の受付 (即時 return、実行はワーカー)
    _ensure_workers(cfg)
    with _LOCK:
        t = _TASKS.get(key)
        if t and t["state"] in ("queued", "uploading"): return {"status": "already", "message": "既にバックアップ中/待機中です"}
        _TASKS[key] = {"state": "queued", "message": "", "full_path": full_path, "is_dir": is_dir, "proc": None, "ts": time.time()}
    _QUEUE.put((key, full_path, is_dir, base_dirs, dict(cfg)))
    return {"status": "queued"}

def _worker_loop(): # ワーカー本体: 例外を絶対に外へ漏らさない
    while True:
        key, full_path, is_dir, base_dirs, cfg = _QUEUE.get()
        try: _process(key, full_path, is_dir, base_dirs, cfg)
        except Exception as e:
            import traceback; traceback.print_exc()
            _set(key, "error", str(e))
        finally: _QUEUE.task_done()

def _process(key, full_path, is_dir, base_dirs, cfg): # 1 タスクの実行
    with _LOCK:
        t = _TASKS.get(key)
        if not t or t["state"] != "queued": return # キュー待機中にキャンセル済み
    if not os.path.exists(full_path):
        _set(key, "error", "ローカルパスが存在しません (削除済み?)"); return
    remote, reason = rsync.local_to_remote(full_path, base_dirs, cfg)
    if remote is None:
        _set(key, "error", reason or "リモートパスを解決できません"); return
    args = ["copy" if is_dir else "copyto", full_path, remote]
    if is_dir: args.append("--create-empty-src-dirs")
    args += ["-v", "--stats", "5s", "--stats-one-line"] # 進捗の可視化: ファイル毎の完了ログ + 5秒毎の一行統計 (転送量/％/速度/ETA)
    if cfg.get("bwlimit"): args += ["--bwlimit", str(cfg["bwlimit"])] # 閲覧への帯域影響を抑えたい場合に config で指定 (例 "8M")
    if cfg.get("transfers"): args += ["--transfers", str(cfg["transfers"])]
    args += list(cfg.get("upload_extra_args", []))
    exe = cfg.get("rclone_exe", "rclone")
    env = os.environ.copy()
    if cfg.get("config_password"): env["RCLONE_CONFIG_PASS"] = cfg["config_password"]
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE,
              "encoding": "utf-8", "errors": "replace", "env": env}
    if sys.platform == 'win32': kwargs["creationflags"] = 0x08000000 # CREATE_NO_WINDOW
    print(f"[backup] 開始: {exe} {' '.join(args)}")
    try: proc = subprocess.Popen([exe] + args, **kwargs)
    except FileNotFoundError:
        _set(key, "error", "rclone コマンドが見つかりません (Path を確認してください)"); return
    with _LOCK: # Popen 直後の再確認: 直前に cancel された場合は即座に殺す
        t = _TASKS.get(key)
        if not t or t["state"] == "canceled":
            try: proc.terminate()
            except Exception: pass
        else:
            t["state"], t["proc"], t["ts"] = "uploading", proc, time.time()
    from collections import deque
    tail = deque(maxlen=10) # エラー時の原因報告用に stderr 末尾を保持
    try:
        for line in proc.stderr: # rclone の稼働中ログをリアルタイムに読む (terminate されるとストリームが閉じてループも終わる)
            line = line.strip()
            if not line: continue
            tail.append(line)
            # if ": Copied (" in line or ": Updated (" in line: # ファイル単位の完了報告 例: "INFO  : 作品A/001.jpg: Copied (new)"
            #     print(f"[backup] ✓ {line.split(':', 1)[-1].strip()}")
            if ("%" in line and "/" in line) or "ETA" in line: # --stats-one-line の統計行 例: "1.2 GiB / 4.5 GiB, 27%, 8.4 MiB/s, ETA 6m2s"
                print(f"[backup] 進行 [{key}] {line}")
                with _LOCK:
                    t = _TASKS.get(key)
                    if t and t["state"] == "uploading": t["message"] = line # フロントの黄ランプ tooltip に進捗を表示
    except Exception: pass # ストリーム読取中の例外は終了処理に委ねる
    proc.wait() # 完了 or terminate まで待機 (タイムアウト無し: 大容量作品対応)
    with _LOCK:
        t = _TASKS.get(key)
        canceled = (not t) or t["state"] == "canceled"
        if t: t["proc"] = None
    if canceled:
        print(f"[backup] 中止: {key}"); return
    if proc.returncode == 0:
        with _LOCK:
            _DONE[key] = {"time": time.strftime('%Y-%m-%d %H:%M:%S'), "remote": remote}
            _persist()
        _set(key, "done", "")
        print(f"[backup] 完了: {key} → {remote}")
    else:
        _set(key, "error", " / ".join(list(tail)[-3:])[-300:] or f"rclone 終了コード {proc.returncode}")

def cancel_under(full_path, wait_seconds=10): # 対象パスに関係する実行中/待機中タスクを中止 (削除・リネームの直前に呼ぶ)
    tgt = os.path.normpath(full_path)
    victims = []
    with _LOCK:
        for key, t in _TASKS.items():
            if t["state"] not in ("queued", "uploading"): continue
            p = os.path.normpath(t.get("full_path", ""))
            if p == tgt or p.startswith(tgt + os.sep) or tgt.startswith(p + os.sep): # 祖先・子孫どちらの関係でも中止
                t["state"], t["message"], t["ts"] = "canceled", "削除/リネーム操作により中止されました", time.time()
                if t.get("proc"): victims.append((key, t["proc"]))
    for key, proc in victims: # ロック外でプロセス終了を待つ (削除処理がファイルハンドルと衝突しないように)
        try:
            proc.terminate()
            proc.wait(timeout=wait_seconds)
        except Exception:
            try: proc.kill(); proc.wait(timeout=5)
            except Exception: pass
        print(f"[backup] 削除/リネームに伴い中止: {key}")
    return len(victims) > 0 or any(True for _ in victims)

def drop_status(mode, media_path): # 削除された作品 (とその配下) の完了記録を破棄
    prefix = f"{mode.upper()}:{media_path}"
    with _LOCK:
        removed = [k for k in _DONE if k == prefix or k.startswith(prefix + "/")]
        for k in removed: del _DONE[k]
        if removed: _persist()

def migrate_status(mode, old_rel, new_rel): # リネーム成功 (クラウド moveto 済) 時に完了記録のキーを移行
    old_prefix, new_prefix = f"{mode.upper()}:{old_rel}", f"{mode.upper()}:{new_rel}"
    with _LOCK:
        moved = False
        for k in list(_DONE.keys()):
            if k == old_prefix or k.startswith(old_prefix + "/"):
                _DONE[k.replace(old_prefix, new_prefix, 1)] = _DONE.pop(k); moved = True
        if moved: _persist()

def get_done_keys(): # 完了済みキー集合 (items への注入用)
    with _LOCK: return set(_DONE.keys())

def get_live_tasks(): # 現在のタスク状況 (/api/status 用)。終了済みは一定時間で自動掃除
    now = time.time()
    with _LOCK:
        stale = [k for k, t in _TASKS.items() if t["state"] in ("done", "error", "canceled") and now - t["ts"] > _FINISHED_TTL]
        for k in stale: del _TASKS[k]
        return {k: {"state": t["state"], "message": t["message"]} for k, t in _TASKS.items()}

def has_active(): # 実行中/待機中タスクの有無
    with _LOCK: return any(t["state"] in ("queued", "uploading") for t in _TASKS.values())