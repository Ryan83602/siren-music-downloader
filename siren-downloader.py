import os
import re
import time
import concurrent.futures
import requests
from datetime import datetime

# 塞壬唱片 API 基础路径
BASE_API_URL = "https://monster-siren.hypergryph.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# 使用全局 Session 复用连接
session = requests.Session()
session.headers.update(HEADERS)

def sanitize_filename(name):
    """清理文件名中的非法字符，防止创建文件失败"""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def safe_get_json(url, retries=3, delay=1):
    """带自动重试机制的安全 GET 请求，解析 JSON"""
    for attempt in range(1, retries + 1):
        try:
            res = session.get(url, timeout=10)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            if attempt == retries:
                print(f"  [请求失败] 达到最大重试次数 ({url}): {e}")
                return None
            time.sleep(delay * attempt)

def extract_date_from_cover(cover_url):
    """从封面图片 URL 提取 YYYYMMDD 日期"""
    if not cover_url:
        return None
    
    match = re.search(r'/pic/(\d{4})(\d{2})(\d{2})/', cover_url)
    if match:
        year, month, day = match.groups()
        return f"{year}{month}{day}"
    return None

def parse_date_str(val):
    """备用：智能解析传统日期字段并转为 YYYYMMDD 格式"""
    if not val:
        return None

    if isinstance(val, (int, float)):
        if val <= 0:
            return None
        if val > 1e11:
            val = val / 1000
        try:
            return datetime.fromtimestamp(val).strftime('%Y%m%d')
        except Exception:
            return None

    val_str = str(val).strip()

    if val_str.isdigit():
        ts = int(val_str)
        if ts <= 0:
            return None
        if ts > 1e11:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts).strftime('%Y%m%d')
        except Exception:
            return None

    if "-" in val_str:
        clean_date = val_str.split(" ")[0].split("T")[0]
        return clean_date.replace("-", "")

    return None

def is_file_downloaded(filepath):
    """检查文件是否已存在且是非空有效文件"""
    return os.path.exists(filepath) and os.path.getsize(filepath) > 0

def check_song_audio_exists(folder_name, song_name):
    """
    在前置检查阶段，匹配文件夹中是否存在以 song_name 开头的音频文件（排除 .lrc）
    """
    if not os.path.exists(folder_name):
        return False
    
    matching_files = [
        f for f in os.listdir(folder_name)
        if f.startswith(f"{song_name}.") and not f.endswith(".lrc")
    ]
    
    for filename in matching_files:
        full_path = os.path.join(folder_name, filename)
        if is_file_downloaded(full_path):
            return True
    return False

def download_file_with_retry(url, filepath, retries=3, delay=1):
    """带自动重试机制的文件下载"""
    if is_file_downloaded(filepath):
        print(f"  [跳过] 已存在: {os.path.basename(filepath)}")
        return

    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, stream=True, timeout=15)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            print(f"  [成功] 下载完成: {os.path.basename(filepath)}")
            return
        except Exception as e:
            if attempt == retries:
                print(f"  [失败] 下载出错 ({os.path.basename(filepath)}): {e}")
                # 清理下载失败留下的空文件
                if os.path.exists(filepath) and os.path.getsize(filepath) == 0:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
            else:
                time.sleep(delay * attempt)

def process_download_task(task):
    """单条下载任务函数（供多线程调用）"""
    url, filepath = task
    download_file_with_retry(url, filepath)

def main():
    print("正在获取专辑列表...")
    albums_res = safe_get_json(f"{BASE_API_URL}/albums")
    
    if not albums_res or albums_res.get("code") != 0:
        print("获取专辑列表失败！")
        return
    
    albums = albums_res.get("data", [])
    print(f"共发现 {len(albums)} 张专辑，开始处理...\n")
    
    MAX_WORKERS = 5
    
    for idx, album in enumerate(albums, 1):
        album_cid = album.get("cid")
        
        album_detail_res = safe_get_json(f"{BASE_API_URL}/album/{album_cid}/detail")
        if not album_detail_res or album_detail_res.get("code") != 0:
            album_detail_res = safe_get_json(f"{BASE_API_URL}/album/{album_cid}/data")
            
        album_data = album_detail_res.get("data", {}) if album_detail_res else {}
        album_name = sanitize_filename(album_data.get("name") or album.get("name") or "Unknown_Album")
        
        # 提取日期（格式：YYYYMMDD）
        cover_url = (
            album_data.get("coverUrl") or 
            album_data.get("coverDeUrl") or 
            album.get("coverUrl") or 
            album.get("coverDeUrl")
        )
        date_str = extract_date_from_cover(cover_url)
        
        if not date_str:
            raw_time = (
                album.get("publishTime") or 
                album.get("date") or 
                album.get("releaseTime") or
                album_data.get("publishTime") or 
                album_data.get("date") or 
                album_data.get("releaseTime")
            )
            date_str = parse_date_str(raw_time) or "未知日期"
        
        folder_name = f"[{date_str}] {album_name}"
        
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        
        print(f"▶ [{idx}/{len(albums)}] 正在处理专辑: {folder_name}")
        
        songs = album_data.get("songs", [])
        download_tasks = []
        
        for song in songs:
            song_cid = song.get("cid")
            song_name = sanitize_filename(song.get("name", "Unknown_Song"))
            
            # 1. 前置预判：如果音频与歌词均已下载完毕，直接跳过 API 请求
            audio_downloaded = check_song_audio_exists(folder_name, song_name)
            lrc_path = os.path.join(folder_name, f"{song_name}.lrc")
            lrc_downloaded = is_file_downloaded(lrc_path)
            
            if audio_downloaded and lrc_downloaded:
                print(f"  [跳过] 已完整下载: {song_name}")
                continue
            
            # 2. 未完整下载时，再调用 API 请求获取歌曲详情
            song_detail_res = safe_get_json(f"{BASE_API_URL}/song/{song_cid}")
            if not song_detail_res or song_detail_res.get("code") != 0:
                print(f"  [失败] 获取歌曲 {song_name} 详情失败，跳过...")
                continue
                
            song_data = song_detail_res.get("data", {})
            source_url = song_data.get("sourceUrl")
            lyric_url = song_data.get("lyricUrl")
            
            if not source_url:
                print(f"  [警告] 找不到歌曲 {song_name} 的音频地址。")
                continue
                
            clean_url = source_url.split("?")[0]
            ext = clean_url.split(".")[-1] if "." in clean_url else "wav"
            if len(ext) > 4:
                ext = "wav"
                
            audio_filepath = os.path.join(folder_name, f"{song_name}.{ext}")
            
            # 3. 按需加入并发任务队列
            if not is_file_downloaded(audio_filepath):
                download_tasks.append((source_url, audio_filepath))
            else:
                print(f"  [跳过] 音频已存在: {os.path.basename(audio_filepath)}")
                
            if lyric_url and not is_file_downloaded(lrc_path):
                download_tasks.append((lyric_url, lrc_path))
            elif lyric_url and is_file_downloaded(lrc_path):
                print(f"  [跳过] 歌词已存在: {os.path.basename(lrc_path)}")

        # 4. 多线程并发下载
        if download_tasks:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                executor.map(process_download_task, download_tasks)

    print("\n全部任务处理完成！")

if __name__ == "__main__":
    main()import os
import re
import time
import concurrent.futures
import requests
from datetime import datetime

# 塞壬唱片 API 基础路径
BASE_API_URL = "https://monster-siren.hypergryph.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# 使用全局 Session 复用连接
session = requests.Session()
session.headers.update(HEADERS)

def sanitize_filename(name):
    """清理文件名中的非法字符，防止创建文件失败"""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def safe_get_json(url, retries=3, delay=1):
    """带自动重试机制的安全 GET 请求，解析 JSON"""
    for attempt in range(1, retries + 1):
        try:
            res = session.get(url, timeout=10)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            if attempt == retries:
                print(f"  [请求失败] 达到最大重试次数 ({url}): {e}")
                return None
            time.sleep(delay * attempt)

def extract_date_from_cover(cover_url):
    """从封面图片 URL 提取 YYYYMMDD 日期"""
    if not cover_url:
        return None
    
    match = re.search(r'/pic/(\d{4})(\d{2})(\d{2})/', cover_url)
    if match:
        year, month, day = match.groups()
        return f"{year}{month}{day}"
    return None

def parse_date_str(val):
    """备用：智能解析传统日期字段并转为 YYYYMMDD 格式"""
    if not val:
        return None

    if isinstance(val, (int, float)):
        if val <= 0:
            return None
        if val > 1e11:
            val = val / 1000
        try:
            return datetime.fromtimestamp(val).strftime('%Y%m%d')
        except Exception:
            return None

    val_str = str(val).strip()

    if val_str.isdigit():
        ts = int(val_str)
        if ts <= 0:
            return None
        if ts > 1e11:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts).strftime('%Y%m%d')
        except Exception:
            return None

    if "-" in val_str:
        clean_date = val_str.split(" ")[0].split("T")[0]
        return clean_date.replace("-", "")

    return None

def is_file_downloaded(filepath):
    """检查文件是否已存在且是非空有效文件"""
    return os.path.exists(filepath) and os.path.getsize(filepath) > 0

def check_song_audio_exists(folder_name, song_name):
    """
    在前置检查阶段，匹配文件夹中是否存在以 song_name 开头的音频文件（排除 .lrc）
    """
    if not os.path.exists(folder_name):
        return False
    
    matching_files = [
        f for f in os.listdir(folder_name)
        if f.startswith(f"{song_name}.") and not f.endswith(".lrc")
    ]
    
    for filename in matching_files:
        full_path = os.path.join(folder_name, filename)
        if is_file_downloaded(full_path):
            return True
    return False

def download_file_with_retry(url, filepath, retries=3, delay=1):
    """带自动重试机制的文件下载"""
    if is_file_downloaded(filepath):
        print(f"  [跳过] 已存在: {os.path.basename(filepath)}")
        return

    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, stream=True, timeout=15)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            print(f"  [成功] 下载完成: {os.path.basename(filepath)}")
            return
        except Exception as e:
            if attempt == retries:
                print(f"  [失败] 下载出错 ({os.path.basename(filepath)}): {e}")
                # 清理下载失败留下的空文件
                if os.path.exists(filepath) and os.path.getsize(filepath) == 0:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
            else:
                time.sleep(delay * attempt)

def process_download_task(task):
    """单条下载任务函数（供多线程调用）"""
    url, filepath = task
    download_file_with_retry(url, filepath)

def main():
    print("正在获取专辑列表...")
    albums_res = safe_get_json(f"{BASE_API_URL}/albums")
    
    if not albums_res or albums_res.get("code") != 0:
        print("获取专辑列表失败！")
        return
    
    albums = albums_res.get("data", [])
    print(f"共发现 {len(albums)} 张专辑，开始处理...\n")
    
    MAX_WORKERS = 5
    
    for idx, album in enumerate(albums, 1):
        album_cid = album.get("cid")
        
        album_detail_res = safe_get_json(f"{BASE_API_URL}/album/{album_cid}/detail")
        if not album_detail_res or album_detail_res.get("code") != 0:
            album_detail_res = safe_get_json(f"{BASE_API_URL}/album/{album_cid}/data")
            
        album_data = album_detail_res.get("data", {}) if album_detail_res else {}
        album_name = sanitize_filename(album_data.get("name") or album.get("name") or "Unknown_Album")
        
        # 提取日期（格式：YYYYMMDD）
        cover_url = (
            album_data.get("coverUrl") or 
            album_data.get("coverDeUrl") or 
            album.get("coverUrl") or 
            album.get("coverDeUrl")
        )
        date_str = extract_date_from_cover(cover_url)
        
        if not date_str:
            raw_time = (
                album.get("publishTime") or 
                album.get("date") or 
                album.get("releaseTime") or
                album_data.get("publishTime") or 
                album_data.get("date") or 
                album_data.get("releaseTime")
            )
            date_str = parse_date_str(raw_time) or "未知日期"
        
        folder_name = f"[{date_str}] {album_name}"
        
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        
        print(f"▶ [{idx}/{len(albums)}] 正在处理专辑: {folder_name}")
        
        songs = album_data.get("songs", [])
        download_tasks = []
        
        for song in songs:
            song_cid = song.get("cid")
            song_name = sanitize_filename(song.get("name", "Unknown_Song"))
            
            # 1. 前置预判：如果音频与歌词均已下载完毕，直接跳过 API 请求
            audio_downloaded = check_song_audio_exists(folder_name, song_name)
            lrc_path = os.path.join(folder_name, f"{song_name}.lrc")
            lrc_downloaded = is_file_downloaded(lrc_path)
            
            if audio_downloaded and lrc_downloaded:
                print(f"  [跳过] 已完整下载: {song_name}")
                continue
            
            # 2. 未完整下载时，再调用 API 请求获取歌曲详情
            song_detail_res = safe_get_json(f"{BASE_API_URL}/song/{song_cid}")
            if not song_detail_res or song_detail_res.get("code") != 0:
                print(f"  [失败] 获取歌曲 {song_name} 详情失败，跳过...")
                continue
                
            song_data = song_detail_res.get("data", {})
            source_url = song_data.get("sourceUrl")
            lyric_url = song_data.get("lyricUrl")
            
            if not source_url:
                print(f"  [警告] 找不到歌曲 {song_name} 的音频地址。")
                continue
                
            clean_url = source_url.split("?")[0]
            ext = clean_url.split(".")[-1] if "." in clean_url else "wav"
            if len(ext) > 4:
                ext = "wav"
                
            audio_filepath = os.path.join(folder_name, f"{song_name}.{ext}")
            
            # 3. 按需加入并发任务队列
            if not is_file_downloaded(audio_filepath):
                download_tasks.append((source_url, audio_filepath))
            else:
                print(f"  [跳过] 音频已存在: {os.path.basename(audio_filepath)}")
                
            if lyric_url and not is_file_downloaded(lrc_path):
                download_tasks.append((lyric_url, lrc_path))
            elif lyric_url and is_file_downloaded(lrc_path):
                print(f"  [跳过] 歌词已存在: {os.path.basename(lrc_path)}")

        # 4. 多线程并发下载
        if download_tasks:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                executor.map(process_download_task, download_tasks)

    print("\n全部任务处理完成！")

if __name__ == "__main__":
    main()
