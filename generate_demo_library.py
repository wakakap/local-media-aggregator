import os
import json
import random
import time
from PIL import Image, ImageDraw

# --- 1. 初始化与配置加载 ---
CONFIG_PATH = "config.json"

try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
except Exception as e:
    print(f"读取 config.json 失败: {e}")
    exit(1)

DATA_DIR = config.get("system_data_dir", "./data")
BASE_DIRS = config.get("base_dirs", ["./data/DATA_1", "./data/DATA_2"])
MODES = {m["id"]: m for m in config.get("modes", [])}

# 全局数据字典，用于最后生成 json
tags_data = {}
stats_data = {}

# 标签池，用于随机分配，增加演示界面的丰富度
TAG_POOLS = {
    "MANGA": ["Sci-Fi", "Action", "Romance", "Full Color", "Completed", "Ongoing", "Masterpiece", "Webtoon"],
    "VIDEO": ["Vlog", "Tech Review", "Tutorial", "1080p", "4K", "Short Film"],
    "GAME": ["RPG Maker", "Indie", "Platformer", "Action", "Visual Novel"],
    "MUSIC": ["Synthwave", "OST", "Acoustic", "Lossless", "Vocal"],
    "NOVEL": ["Light Novel", "Sci-Fi", "Fantasy", "Epub", "Official"]
}

# --- 2. 图像与文件生成工具 ---
def generate_pattern_image(filepath, text, width=600, height=800):
    """生成带有随机纹理和文字的低体积图片"""
    # 随机背景色
    bg_color = (random.randint(30, 100), random.randint(30, 100), random.randint(30, 100))
    img = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # 随机选择纹理：条纹(0) 或 圆圈(1)
    pattern_type = random.choice([0, 1])
    pattern_color = (random.randint(100, 200), random.randint(100, 200), random.randint(100, 200))
    
    if pattern_type == 0: # 画条纹
        for y in range(0, height, 40):
            draw.line([(0, y), (width, y)], fill=pattern_color, width=5)
    else: # 画圆圈
        for _ in range(15):
            r = random.randint(20, 150)
            cx, cy = random.randint(0, width), random.randint(0, height)
            draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=pattern_color, width=3)
            
    # 在中心画一个半透明黑色矩形用来衬托文字
    draw.rectangle([width//2 - 100, height//2 - 40, width//2 + 100, height//2 + 40], fill=(0, 0, 0))
    # 简单写字（为了不依赖外部字体文件，使用默认字体或直接多画几根线模拟文字，这里直接用默认）
    draw.text((width//2 - 30, height//2 - 10), str(text), fill=(255, 255, 255))
    
    # 保存为低质量 JPEG，体积极小
    img.save(filepath, format='JPEG', quality=30)

def create_empty_file(filepath):
    """生成空壳文件（0KB）用于视频、音乐等占位"""
    with open(filepath, 'w') as f:
        pass

# --- 3. 核心生成逻辑 ---
def register_metadata(mode_id, media_path, is_folder=True, pages_count=0):
    """注册 Tag 和统计数据"""
    composite_key = f"{mode_id}:{media_path}"
    
    # 随机分配 2-4 个 Tag
    pool = TAG_POOLS.get(mode_id, ["Demo"])
    assigned_tags = random.sample(pool, random.randint(2, min(4, len(pool))))
    tags_data[composite_key] = assigned_tags
    
    # 随机生成统计数据
    views = random.randint(5, 300)
    pages_stat = {}
    if is_folder and pages_count > 0:
        # 模拟阅读热力图，前几页访问多，后面逐渐减少
        for p in range(1, pages_count + 1):
            if random.random() > 0.3: # 70% 概率阅读了这页
                pages_stat[str(p)] = max(1, views - int(p * 1.5))
                
    stats_data[composite_key] = {
        "total_views": views,
        "last_accessed": int(time.time()) - random.randint(0, 86400 * 30),
        "pages": pages_stat,
        "files": {}
    }

def generate_mock_library():
    print("开始生成 Demo 库...")
    
    # 确保全局数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 针对 Manga 生成 10 个作品，分配到两个盘
    manga_titles = [f"Cyberpunk City Vol.{i}" for i in range(1, 4)] + \
                   [f"Slice of Life #{i}" for i in range(1, 4)] + \
                   ["Fantasy World (Completed)", "Deep Space 9", "Ocean Adventure", "Nested Story Anthology"]
                   
    for idx, title in enumerate(manga_titles):
        base_dir = BASE_DIRS[idx % len(BASE_DIRS)]
        manga_pages_dir = os.path.join(base_dir, MODES["MANGA"]["pages"])
        work_dir = os.path.join(manga_pages_dir, title)
        os.makedirs(work_dir, exist_ok=True)
        
        # 决定是平铺还是嵌套（最后 2 个做嵌套）
        if idx >= 8:
            for vol in ["Vol.1", "Vol.2"]:
                vol_dir = os.path.join(work_dir, vol)
                os.makedirs(vol_dir, exist_ok=True)
                for p in range(1, 11):
                    generate_pattern_image(os.path.join(vol_dir, f"{p:03d}.jpg"), f"P.{p}", 400, 600)
            register_metadata("MANGA", title, True, 20)
        else:
            for p in range(1, 31):
                generate_pattern_image(os.path.join(work_dir, f"{p:03d}.jpg"), f"Page {p}", 400, 600)
            register_metadata("MANGA", title, True, 30)

    # 针对 Video, Game, Music, Novel 生成空壳文件
    # VIDEO
    video_titles = ["Tech_Review_2024", "Tokyo_Vlog_4K", "Python_Tutorial_Part1"]
    for idx, title in enumerate(video_titles):
        base_dir = BASE_DIRS[idx % len(BASE_DIRS)]
        v_dir = os.path.join(base_dir, MODES["VIDEO"]["pages"], title)
        os.makedirs(v_dir, exist_ok=True)
        create_empty_file(os.path.join(v_dir, f"{title}.mp4"))
        create_empty_file(os.path.join(v_dir, f"{title}.vtt")) # 假装有字幕
        generate_pattern_image(os.path.join(v_dir, "cover.jpg"), "Video Cover", 600, 337) # 16:9 比例
        register_metadata("VIDEO", title)

    # GAME
    game_titles = ["Indie_Platformer_v1", "RPG_Maker_Demo", "FPS_Prototype"]
    for idx, title in enumerate(game_titles):
        base_dir = BASE_DIRS[idx % len(BASE_DIRS)]
        g_dir = os.path.join(base_dir, MODES["GAME"]["pages"], title)
        os.makedirs(g_dir, exist_ok=True)
        create_empty_file(os.path.join(g_dir, "game.exe"))
        create_empty_file(os.path.join(g_dir, "data.pak"))
        generate_pattern_image(os.path.join(g_dir, "icon.ico"), "ICON", 256, 256)
        register_metadata("GAME", title)
        
    # MUSIC
    music_titles = ["Synthwave_Mix_2023", "Acoustic_Guitar_Covers", "Game_OST_Collection"]
    for idx, title in enumerate(music_titles):
        base_dir = BASE_DIRS[idx % len(BASE_DIRS)]
        m_dir = os.path.join(base_dir, MODES["MUSIC"]["pages"], title)
        os.makedirs(m_dir, exist_ok=True)
        for t in range(1, 6):
            create_empty_file(os.path.join(m_dir, f"{t:02d}_track.mp3"))
        generate_pattern_image(os.path.join(m_dir, "cover.jpg"), "Album", 500, 500) # 1:1 比例
        register_metadata("MUSIC", title)

    # NOVEL
    novel_titles = ["Sci_Fi_Anthology_Vol1", "Light_Novel_Official"]
    for idx, title in enumerate(novel_titles):
        base_dir = BASE_DIRS[idx % len(BASE_DIRS)]
        n_dir = os.path.join(base_dir, MODES["NOVEL"]["pages"], title)
        os.makedirs(n_dir, exist_ok=True)
        create_empty_file(os.path.join(n_dir, f"{title}.epub"))
        generate_pattern_image(os.path.join(n_dir, "cover.jpg"), "Book Cover", 400, 600)
        register_metadata("NOVEL", title)

    # --- 4. 保存 JSON 数据 ---
    tags_path = os.path.join(DATA_DIR, "tags.json")
    stats_path = os.path.join(DATA_DIR, "stats.json")
    
    with open(tags_path, 'w', encoding='utf-8') as f:
        json.dump(tags_data, f, ensure_ascii=False, indent=4)
        
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats_data, f, ensure_ascii=False, indent=4)
        
    print(f"Demo 库生成完毕！")
    print(f"数据已分布在: {BASE_DIRS}")
    print(f"配置文件保存在: {tags_path} 和 {stats_path}")

if __name__ == "__main__":
    generate_mock_library()