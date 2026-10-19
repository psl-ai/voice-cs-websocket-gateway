import requests
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from io import BytesIO
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('AudioUtil')

def get_audio_info(url):
    """
    下载音频到内存，使用 mutagen 解析时长
    返回 (文件大小(字节), 时长(秒))
    失败时返回 (None, None)
    """
    try:
        # 1. 下载完整文件到内存（文件小，直接一次性读取也可，但流式更稳健）
        logger.info("正在下载音频...")
        response = requests.get(url, stream=True, timeout=1)
        response.raise_for_status()

        # 收集所有数据
        audio_data = b''
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                audio_data += chunk

        file_size = len(audio_data)
        logger.info(f"下载完成，大小: {file_size} 字节")

        if file_size == 0:
            logger.info("下载数据为空")
            return None, None

        # 2. 检查数据开头，辅助判断是否为 MP3
        # MP3 通常以 ID3 标签或 FF FB / FF F3 等同步字开头
        header_preview = audio_data[:20]
        logger.info(f"数据开头 (十六进制): {header_preview.hex(' ')}")
        logger.info(f"数据开头 (ASCII): {header_preview}")

        
        audio_type = 'WAV'
        if audio_data[:3] == b'ID3':
            audio_type = 'MP3'
            logger.info("检测到 ID3v2 标签，是 MP3")
        elif audio_data[0] == 0xFF and (audio_data[1] & 0xE0) == 0xE0:
            logger.info("检测到 MP3 帧同步字，是 MP3")
            audio_type = 'MP3'
        else:
            logger.info("警告：开头不是标准 MP3 标识,默认当WAV处理...")

        # 3. 用 mutagen 解析时长
        try:
            if audio_type == 'MP3':
                audio = MP3(BytesIO(audio_data))
            else:
                audio = WAVE(BytesIO(audio_data))
            duration = audio.info.length
            logger.info(f"解析成功，时长: {duration:.2f} 秒")
            return file_size, duration
        except Exception as e:
            logger.info(f"mutagen 解析失败: {e}")
            # 可能是数据损坏或不完整
            return file_size, None

    except requests.exceptions.RequestException as e:
        logger.info(f"网络请求失败: {e}")
        return None, None
    except Exception as e:
        logger.info(f"未知错误: {e}")
        return None, None

if __name__ == "__main__":
    url = 'http://freeswitch:SwE432OpHwHuo0@10.1.255.177:8001/rhtx/api/getByFileId/2077672642520940545' 
    size, duration = get_audio_info(url)

    if size is not None:
        logger.info(f"文件大小: {size} 字节")
    if duration is not None:
        logger.info(f"音频时长: {duration:.2f} 秒")
    else:
        logger.info("未能获取音频时长")

