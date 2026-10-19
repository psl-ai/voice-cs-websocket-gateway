import re
from typing import List, Tuple


_MEANINGFUL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_PUNCT_OR_SPACE_RE = re.compile(r"[\s，。！？、,.!?;；:：'\"“”‘’（）()【】\[\]{}<>《》\-_=+~`|\\/]+")
_FILLER_RE = re.compile(r"^[恩嗯啊呃额呀哎诶哦噢嗷喂哈呵嘿呐啦嘛吧哼]+$")

_COMMON_ASR_NOISE_PHRASES = (
    "谢谢观看",
    "感谢观看",
    "感谢收看",
    "字幕",
    "订阅",
    "点赞",
    "转发",
    "下期再见",
    "明镜与点点栏目",
)

# 匹配 /silence /noise 这类ASR控制标记
CMD_PATTERN = re.compile(r"^/[a-zA-Z0-9_]+$")
# 中文Unicode区间
CHN_START = 0x4E00
CHN_END = 0x9FFF
# 英文占比阈值，超过则判定识别异常
EN_RATIO_THRESHOLD = 0.3

def normalize_asr_text(text: str) -> str:
    """Normalize one ASR text segment while preserving business content."""
    if not text:
        return ""

    text = str(text).strip()
    text = re.sub(r"\s+", "", text)
    # 新增将英文字母和/.字符去除
    text = re.sub(r"[a-zA-Z\/\.]", "", text)
    return text


def meaningful_length(text: str) -> int:
    return len(_MEANINGFUL_RE.findall(text or ""))


def is_probably_noise_text(text: str, min_chars: int = 2) -> Tuple[bool, str]:
    """Return whether an ASR segment looks like silence/noise hallucination."""
    text = normalize_asr_text(text)
    if not text:
        return True, "empty"

    meaningful_chars = _MEANINGFUL_RE.findall(text)
    meaningful_text = "".join(meaningful_chars)
    if len(meaningful_chars) < min_chars:
        return True, "too_short"

    if any(phrase in text for phrase in _COMMON_ASR_NOISE_PHRASES):
        return True, "common_noise_phrase"

    stripped = _PUNCT_OR_SPACE_RE.sub("", text)
    if stripped and _FILLER_RE.fullmatch(stripped):
        return True, "filler"

    if len(meaningful_text) >= 2 and len(set(meaningful_text)) == 1:
        return True, "repeated_single_char"

    non_meaningful = len(text) - len(meaningful_chars)
    if len(text) >= 4 and non_meaningful / len(text) > 0.5:
        return True, "too_many_symbols"

    return False, ""


def append_asr_segment(segments: List[str], text: str, max_segments: int = 12) -> bool:
    """Append a cleaned ASR segment, de-duplicating common 2pass repeats."""
    text = normalize_asr_text(text)
    if not text:
        return False

    if segments:
        last = segments[-1]
        if text == last or text in last:
            return False
        if last in text:
            segments[-1] = text
            return True

    if text in segments[-3:]:
        return False

    segments.append(text)
    if max_segments > 0 and len(segments) > max_segments:
        del segments[:-max_segments]
    return True


def build_transcript(segments: List[str], max_chars: int = 160) -> str:
    #transcript = " ".join(segment for segment in segments if segment)
    transcript = segments[-1]
    if max_chars > 0 and len(transcript) > max_chars:
        return transcript[-max_chars:]
    return transcript


def transcript_ready(transcript: str, min_chars: int = 3) -> bool:
    return meaningful_length(transcript) >= min_chars


def count_chs_eng(text: str) -> tuple[int, int]:
    """统计中文字符、英文字母数量"""
    chs = 0
    eng = 0
    for char in text:
        if CHN_START <= ord(char) <= CHN_END:
            chs += 1
        elif char.isalpha():
            eng += 1
    return chs, eng


def detect_asr_abnormal(asr_text: str) -> dict:
    text = asr_text.strip()
    res = {
        "original_text": asr_text,
        "is_abnormal": False,
        "abnormal_type": None,
        "desc": ""
    }

    # 1. 判断是否为 /xxx 控制指令
    if CMD_PATTERN.match(text):
        res["is_abnormal"] = True
        res["abnormal_type"] = "cmd_instruction"
        res["desc"] = f"ASR内部控制标记：{text}"
        return res

    chs_cnt, eng_cnt = count_chs_eng(text)
    total_valid = chs_cnt + eng_cnt

    # 2. 纯标点/空格，无任何中英文有效文字 → 异常
    if total_valid == 0:
        res["is_abnormal"] = True
        res["abnormal_type"] = "no_valid_content"
        res["desc"] = "文本仅含标点/空白，无有效语音识别内容"
        return res

    # 3. 英文占比过高，识别跑偏
    eng_ratio = eng_cnt / total_valid
    if eng_ratio >= EN_RATIO_THRESHOLD:
        res["is_abnormal"] = True
        res["abnormal_type"] = "high_english"
        res["desc"] = f"英文占比{eng_ratio:.2%}，超出阈值{EN_RATIO_THRESHOLD:.0%}，识别异常"
    else:
        res["desc"] = f"英文占比{eng_ratio:.2%}，包含有效中文内容，识别正常"

    return res

if __name__ == '__main__':
    print(detect_asr_abnormal('有人拉我进群。'))
