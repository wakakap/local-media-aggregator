# -*- coding: utf-8 -*-
import os, sys, json, time, queue, uuid, tempfile, threading, subprocess, traceback, unicodedata
from collections import deque
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
    tgt = os.path.normpath(full_path)
    with _LOCK:
        t = _TASKS.get(key)
        if t and t["state"] in ("queued", "uploading"): return {"status": "already", "message": "既にバックアップ中/待機中です"}
        if _OPS_PENDING > 0: return {"status": "error", "message": "クラウド操作 (リネーム同期/削除) が処理中です。完了後に再実行してください"} # moveto と sync の同一リモートへの同時実行を防ぐ
        for k, o in _TASKS.items(): # 祖先フォルダが待機中/実行中 → その同期に既に含まれるため投入不要
            if o["state"] in ("queued", "uploading") and tgt.startswith(os.path.normpath(o.get("full_path", "")) + os.sep):
                return {"status": "covered", "message": f"親フォルダの同期に含まれています: {k}"}
    if is_dir: cancel_under(full_path, message="親フォルダの同期に統合されました", descendants_only=True) # 配下の個別タスクは親に吸収 (同一リモートへの重複書き込み防止)
    with _LOCK:
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
    if is_dir:
        if not os.listdir(full_path): _set(key, "error", "ローカルフォルダが空のため同期を中止しました (クラウド側を空にしないための保護)"); return # 安全弁1: 空フォルダでの sync を拒否
        verb = cfg.get("dir_upload_mode", "sync") # sync = ミラー (多則削・少則加・異則上書き、対象子ツリー内のみ) / copy = 追加・上書きのみ
        if verb not in ("sync", "copy"): verb = "sync"
        args = [verb, rsync.local_arg(full_path), remote, "--create-empty-src-dirs"]
        if verb == "sync" and cfg.get("sync_max_delete") is not None: args += ["--max-delete", str(cfg["sync_max_delete"])] # 安全弁2: 削除数の上限 (超過で rclone がエラー終了 → 赤ランプ)
    else:
        args = ["copyto", rsync.local_arg(full_path), remote]
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
            if any(w in line for w in ("Attempt ", "retry", "ERROR", "NOTICE")): print(f"[backup] ⚠ [{key}] {line}") # 重試・エラーを可視化 (進捗行の条件では拾えない)
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
def cancel_under(full_path, wait_seconds=10, message="削除/リネーム操作により中止されました", descendants_only=False): # descendants_only=True: 配下のタスクのみ中止 (親フォルダ同期への統合用)
    tgt = os.path.normpath(full_path)
    victims, canceled = [], [] # canceled: 中止したタスクのキー (待機中も含む)
    with _LOCK:
        for key, t in _TASKS.items():
            if t["state"] not in ("queued", "uploading"): continue
            p = os.path.normpath(t.get("full_path", ""))
            if p == tgt or p.startswith(tgt + os.sep) or (not descendants_only and tgt.startswith(p + os.sep)): # 祖先・子孫どちらの関係でも中止 (descendants_only 時は子孫のみ)
                t["state"], t["message"], t["ts"] = "canceled", message, time.time()
                canceled.append(key)
                if t.get("proc"): victims.append((key, t["proc"]))
    for key, proc in victims: # ロック外でプロセス終了を待つ (削除処理がファイルハンドルと衝突しないように)
        try:
            proc.terminate()
            proc.wait(timeout=wait_seconds)
        except Exception:
            try: proc.kill(); proc.wait(timeout=5)
            except Exception: pass
        print(f"[backup] 削除/リネームに伴い中止: {key}")
    return canceled # 中止したタスクのキー一覧。空リスト = 何も中止していない

def drop_status(mode, media_path): # 削除された作品 (とその配下) の完了記録を破棄
    prefix = f"{mode.upper()}:{media_path}"
    with _LOCK:
        removed = [k for k in _DONE if k == prefix or k.startswith(prefix + "/")]
        for k in removed: del _DONE[k]
        if removed: _persist()

def invalidate_ancestors(mode, media_path): # 配下の削除により祖先フォルダのクラウド内容がローカルと不一致 → 祖先の完了記録を破棄 (灰に戻して再同期を促す)
    parts = media_path.split("/")
    anc = {f"{mode.upper()}:{'/'.join(parts[:i])}" for i in range(1, len(parts))}
    with _LOCK:
        removed = [k for k in anc if k in _DONE]
        for k in removed: del _DONE[k]
        if removed: _persist()
    return removed

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

# ==========================================
# クラウド操作キュー (v2)
# リネーム同期 (moveto) と差分削除 (purge/deletefile) を専用ワーカー 1 本で直列実行する。
# アップロード用キューとは別なので、大容量バックアップの後ろで待たされない。
# 直列なので A→B→C の連続リネームも順序通りに反映される。
# ==========================================
_OPS_QUEUE = queue.Queue()
_OPS_WORKER = None
_OPS_SEQ = 0                        # 終了済み操作の通し番号 (フロントの通知重複防止用)
_OPS_PENDING = 0                    # 待機中 + 実行中の件数
_OPS_RECENT = deque(maxlen=200)     # 終了済み操作 {id, kind, name, status(ok/skipped/error), message}
_LAST_DIFF = {"token": None, "ts": 0, "items": {}} # 直近の差分結果。削除要求はここに載っている項目のみ受け付ける
_DIFF_TTL = 1800                    # 差分結果の有効期限 (秒)

def _ensure_ops_worker():
    global _OPS_WORKER
    with _LOCK:
        if _OPS_WORKER is None or not _OPS_WORKER.is_alive():
            _OPS_WORKER = threading.Thread(target=_ops_loop, daemon=True, name="cloud-ops"); _OPS_WORKER.start()

def _enqueue_op(op):
    global _OPS_PENDING
    _ensure_ops_worker()
    with _LOCK: _OPS_PENDING += 1
    _OPS_QUEUE.put(op)

def _ops_loop(): # 例外を外へ漏らさず、1 操作につき必ず 1 件の終了報告を残す
    global _OPS_SEQ, _OPS_PENDING
    while True:
        op = _OPS_QUEUE.get()
        try:
            if op["kind"] == "rename": status, msg = _op_rename(op)
            elif op["kind"] == "purge": status, msg = _op_purge(op)
            else: status, msg = "error", f"未知の操作: {op['kind']}"
        except Exception as e:
            traceback.print_exc(); status, msg = "error", str(e)
        with _LOCK:
            _OPS_SEQ += 1; _OPS_PENDING -= 1
            _OPS_RECENT.append({"id": _OPS_SEQ, "kind": op["kind"], "name": op.get("name", ""), "status": status, "message": msg})
        print(f"[cloud-ops] {op['kind']} {status}: {op.get('name', '')} {msg}")
        _OPS_QUEUE.task_done()

def get_ops_seq():
    with _LOCK: return _OPS_SEQ

def get_ops_status(): # /api/status 用
    with _LOCK: return {"pending": _OPS_PENDING, "recent": list(_OPS_RECENT)}

# ---------- リネーム同期 ----------
def enqueue_rename(mode, old_full, new_full, old_rel, new_rel, base_dirs, cfg): # 即時 return。完了記録は楽観的に先行移行 (ランプの緑を即時引き継ぐ)
    migrate_status(mode, old_rel, new_rel)
    if not cfg.get("enabled", False): return
    _enqueue_op({"kind": "rename", "mode": mode, "name": os.path.basename(new_full), "old_full": old_full, "new_full": new_full,
                 "new_rel": new_rel, "base_dirs": list(base_dirs), "cfg": dict(cfg)})

def _op_rename(op):
    r = rsync.sync_rename(op["old_full"], op["new_full"], op["base_dirs"], op["cfg"])
    st = r.get("status")
    if st == "moved": return "ok", ""
    drop_status(op["mode"], op["new_rel"]) # 移動できなかった → 新名は灰に戻す (クラウドの旧名は差分チェックで検出される)
    if st in ("not_backed_up", "skipped", "disabled"): return "skipped", r.get("message", "")
    return "error", r.get("message", "") or st

# ---------- 差分チェック ----------
def _local_top_names(base_dirs, pages): # 全ドライブの pages 直下名の和集合 (NFC 正規化)。listdir の例外は呼び出し元へ伝播させる
    exact, folded = set(), set()
    for base in base_dirs:
        d = os.path.join(base, pages)
        if not os.path.isdir(d): continue # ドライブは接続済みだがこのモードのフォルダが無い = 空扱い
        for n in os.listdir(d):
            nfc = unicodedata.normalize("NFC", n)
            exact.add(nfc); folded.add(nfc.casefold())
    return exact, folded

def compute_cloud_diff(modes_config, base_dirs, cfg): # クラウドにのみ存在する作品単位の項目を列挙
    global _LAST_DIFF
    if not cfg.get("enabled", False): return {"status": "error", "message": "rclone 同期が config で無効になっています"}
    remote_root = cfg.get("remote_name", "").rstrip("/")
    if not remote_root: return {"status": "error", "message": "config の remote_name が未設定です"}
    missing = [b for b in base_dirs if not os.path.isdir(b)]
    if missing: return {"status": "error", "message": "未接続のドライブがあるため中止しました (その中の作品が全て削除候補になるのを防ぐため):\n" + "\n".join(missing)}
    with _LOCK: busy = _OPS_PENDING > 0
    if busy: return {"status": "error", "message": "クラウド操作 (リネーム同期/削除) が処理中です。完了後に再実行してください"}
    excludes = set(cfg.get("exclude_keywords", ["THUMBNAILS"]))
    modes_out, index, errors = [], {}, []
    for mode in sorted(modes_config.keys()):
        pages = modes_config[mode]["pages"]
        remote_dir = f"{remote_root}/{pages.replace(os.sep, '/').strip('/')}" # local_to_remote と同一規則 (remote_name + base_dir からの相対パス)
        entries, err = rsync.list_remote_dir(remote_dir, cfg)
        if entries is None: errors.append(f"{mode}: {err}"); continue # 取得失敗のモードは比較しない (空扱いにしない)
        local_exact, local_folded = _local_top_names(base_dirs, pages)
        extras = []
        for e in entries:
            name = e.get("Name", "")
            if not name or "/" in name or name in (".", "..") or name in excludes: continue # 不正名・除外対象は候補に載せない
            nfc = unicodedata.normalize("NFC", name)
            if nfc in local_exact: continue
            extras.append({"name": name, "is_dir": bool(e.get("IsDir")), "size": e.get("Size", -1),
                           "mtime": (e.get("ModTime") or "")[:16].replace("T", " "), "case_only": nfc.casefold() in local_folded})
            index[(mode, name)] = {"remote": f"{remote_dir}/{name}", "is_dir": bool(e.get("IsDir")), "pages": pages}
        extras.sort(key=lambda x: x["name"].lower())
        modes_out.append({"mode": mode, "remote_dir": remote_dir, "extras": extras})
    token = uuid.uuid4().hex
    with _LOCK: _LAST_DIFF = {"token": token, "ts": time.time(), "items": index}
    return {"status": "success", "token": token, "modes": modes_out, "errors": errors}

# ---------- 差分削除 ----------
def enqueue_cloud_clean(token, items, base_dirs, cfg): # 直近の差分結果に含まれる項目のみ削除キューへ (任意パスの削除を構造的に不可能にする)
    global _LAST_DIFF
    with _LOCK:
        if not token or token != _LAST_DIFF["token"] or time.time() - _LAST_DIFF["ts"] > _DIFF_TTL:
            return {"status": "error", "message": "差分結果が古いか無効です。もう一度差分チェックを実行してください"}
        index = _LAST_DIFF["items"]
        picked = [(it.get("mode"), it.get("name"), index[(it.get("mode"), it.get("name"))])
                  for it in items if (it.get("mode"), it.get("name")) in index]
        _LAST_DIFF = {"token": None, "ts": 0, "items": {}} # トークンは使い捨て (二重送信防止)
        baseline = _OPS_SEQ
    if not picked: return {"status": "error", "message": "削除対象がありません"}
    for mode, name, info in picked:
        _enqueue_op({"kind": "purge", "mode": mode, "name": name, "remote": info["remote"], "is_dir": info["is_dir"],
                     "pages": info["pages"], "base_dirs": list(base_dirs), "cfg": dict(cfg)})
    return {"status": "queued", "count": len(picked), "ops_baseline": baseline}

def _op_purge(op):
    missing = [b for b in op["base_dirs"] if not os.path.isdir(b)]
    if missing: return "error", "ドライブ未接続のため中止しました: " + ", ".join(missing)
    local_exact, _ = _local_top_names(op["base_dirs"], op["pages"]) # 実行直前に再確認 (差分表示後に同名作品を戻した場合を保護)
    if unicodedata.normalize("NFC", op["name"]) in local_exact: return "skipped", "ローカルに同名項目が存在するため削除しませんでした"
    result = rsync._run_rclone(["purge" if op["is_dir"] else "deletefile", op["remote"]], op["cfg"],
                               timeout=op["cfg"].get("purge_timeout_seconds", 3600)) # 大容量フォルダの purge 用に長めのタイムアウト
    if result.returncode == 0:
        drop_status(op["mode"], op["name"]); return "ok", ""
    if result.returncode in (3, 4):
        drop_status(op["mode"], op["name"]); return "skipped", "クラウド側に既に存在しません"
    return "error", (result.stderr or "").strip()[-300:] or f"rclone 終了コード {result.returncode}"