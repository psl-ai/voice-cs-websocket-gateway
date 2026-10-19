import random
import asyncio
import websockets
import json
import math
import re
import sys
from array import array
import logging
import uuid
import time
import traceback
from ytsb import ytsb
from xxcq import xxcq
from typing import Dict
from config import *
from asr_text_filter import (
    append_asr_segment,
    build_transcript,
    is_probably_noise_text,
    normalize_asr_text,
    transcript_ready,
    detect_asr_abnormal
)

from AudioUtil import get_audio_info

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('WebSocketProxy')

def _get_int_header(headers: dict, name: str, default: int) -> int:
    raw_value = headers.get(name)
    if raw_value in (None, ""):
        return default
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        logger.warning(f"连接头 {name}={raw_value} 非法，使用默认值 {default}")
        return default


def _get_float_header(headers: dict, name: str, default: float) -> float:
    raw_value = headers.get(name)
    if raw_value in (None, ""):
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        logger.warning(f"连接头 {name}={raw_value} 非法，使用默认值 {default}")
        return default


def _get_bool_header(headers: dict, name: str, default: bool) -> bool:
    raw_value = headers.get(name)
    if raw_value in (None, ""):
        return default

    value = str(raw_value).strip().lower()
    if value in ("1", "true", "yes", "y", "on"):
        return True
    if value in ("0", "false", "no", "n", "off"):
        return False

    logger.warning(f"连接头 {name}={raw_value} 非法，使用默认值 {default}")
    return default


def _parse_json_object(payload):
    if isinstance(payload, dict):
        return payload
    if not payload:
        return None

    text = str(payload).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _confidence_ok(result: dict, threshold: float) -> bool:
    raw_value = result.get("confidence")
    if raw_value in (None, ""):
        return True
    try:
        return float(raw_value) >= threshold
    except (TypeError, ValueError):
        return False


def _confirm_value(info: dict, name: str, value: str, required_times: int) -> bool:
    last_key = f"last_{name}"
    count_key = f"{name}_hit_count"
    if info.get(last_key) == value:
        info[count_key] = info.get(count_key, 0) + 1
    else:
        info[last_key] = value
        info[count_key] = 1
    return info[count_key] >= max(1, required_times)


def _is_rec_success(result: dict) -> bool:
    try:
        return int(result.get("is_rec")) == 0
    except (TypeError, ValueError):
        return False


def _valid_rec_result(conf: dict, rec_result) -> bool:
    if rec_result in (None, ""):
        return False

    pattern = conf.get("rec_regx")
    if not pattern:
        return True

    rec_text = str(rec_result)
    try:
        return bool(re.fullmatch(pattern, rec_text) or re.search(pattern, rec_text))
    except re.error as exc:
        logger.warning(f"rec_regx={pattern} 非法，跳过本地正则校验: {exc}")
        return True


def _strip_surrogates(value):
    if isinstance(value, str):
        return re.sub(r"[\ud800-\udfff]", "", value)
    if isinstance(value, list):
        return [_strip_surrogates(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_surrogates(item) for item in value)
    if isinstance(value, dict):
        return {
            _strip_surrogates(key): _strip_surrogates(item)
            for key, item in value.items()
        }
    return value


def _safe_json_dumps(payload) -> str:
    return json.dumps(_strip_surrogates(payload), ensure_ascii=False)


async def _send_json(ws, payload):
    await ws.send(_safe_json_dumps(payload))


def pcm16_rms_dbfs(data) -> float:
    audio_bytes = bytes(data or b"")
    if len(audio_bytes) < 2:
        return -120.0
    if len(audio_bytes) % 2:
        audio_bytes = audio_bytes[:-1]

    samples = array("h")
    samples.frombytes(audio_bytes)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return -120.0

    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    if rms <= 0:
        return -120.0
    return 20 * math.log10(rms / 32768.0)


def _audio_chunk_ms(data, info: dict) -> float:
    sample_rate = max(1, int(info.get("audio_sample_rate", AUDIO_SAMPLE_RATE)))
    channels = max(1, int(info.get("audio_channels", AUDIO_CHANNELS)))
    sample_width = max(1, int(info.get("audio_sample_width", AUDIO_SAMPLE_WIDTH)))
    frame_bytes = channels * sample_width
    frames = len(data) / frame_bytes
    return max(1.0, frames * 1000.0 / sample_rate)


def _silence_like(data):
    return b"\x00" * len(data)


def apply_audio_gate(message, info: dict, connection_id: str):
    #不降噪
    return message

    #if not info.get("audio_gate_enabled", True):
     #   return message
    if not isinstance(message, (bytes, bytearray, memoryview)):
        return message

    audio_bytes = bytes(message)
    if not audio_bytes:
        return audio_bytes

    chunk_ms = _audio_chunk_ms(audio_bytes, info)
    level_db = pcm16_rms_dbfs(audio_bytes)

    if not info.get("audio_gate_calibrated", True):
        info.setdefault("audio_noise_levels", []).append(level_db)
        info["audio_noise_calibrated_ms"] = info.get("audio_noise_calibrated_ms", 0.0) + chunk_ms
        if info["audio_noise_calibrated_ms"] >= info.get("audio_noise_calibrate_ms", 0):
            levels = info.get("audio_noise_levels") or [level_db]
            noise_floor = sum(levels) / len(levels)
            threshold = max(info["audio_gate_threshold_db"], noise_floor + info["audio_noise_margin_db"])
            threshold = min(info["audio_gate_threshold_db_min"], threshold)
            info["audio_gate_threshold_db"] = threshold
            info["audio_gate_calibrated"] = True
            logger.info(
                f"[{connection_id}] 音频门控校准完成: noise_floor={noise_floor:.1f} dBFS, "
                f"threshold={threshold:.1f} dBFS"
            )
        return _silence_like(audio_bytes)

    is_loud = level_db >= info["audio_gate_threshold_db"]
    if is_loud:
        info["audio_gate_loud_ms"] = info.get("audio_gate_loud_ms", 0.0) + chunk_ms
        info["audio_gate_quiet_ms"] = 0.0
    else:
        info["audio_gate_loud_ms"] = 0.0
        info["audio_gate_quiet_ms"] = info.get("audio_gate_quiet_ms", 0.0) + chunk_ms

    if not info.get("audio_gate_open", False) and info["audio_gate_loud_ms"] >= info["audio_gate_start_ms"]:
        info["audio_gate_open"] = True
        logger.debug(f"[{connection_id}] 音频门控打开: level={level_db:.1f} dBFS")
    elif info.get("audio_gate_open", False) and info["audio_gate_quiet_ms"] >= info["audio_gate_end_ms"]:
        info["audio_gate_open"] = False
        logger.debug(f"[{connection_id}] 音频门控关闭: level={level_db:.1f} dBFS")

    return audio_bytes if info.get("audio_gate_open", False) else _silence_like(audio_bytes)

async def configure_server(ws, info) -> None:
    question = json.loads(info["question"])['question']
    config = json.loads(info["config"] or "[]")

    hot_words = []
    #if "rec_example" not in config:
    #  for i in config:
    #    logger.info("~~~~~~~~~~~~~~~~~~~~~~~~")
    #    logger.info(i)
    #    logger.info("~~~~~~~~~~~~~~~~~~~~~~~~")
    #    if i.get('examples', []):
    #       hot_words.extend(i.get('examples', []))

    await ws.send(
        json.dumps(
            {
                "type": "config",
                "sample_rate": 16000,
                "encoding": "pcm_s16le",
                "content": f"\n贵州方言或者普通话\n用户被提问的问题：{question}",
                "hot_words": hot_words
            },
            ensure_ascii=False,
        )
    )

def get_asr_url():
    url_list = [url.strip() for url in ASR_URL.split(",") if url.strip() ]
    logger.info(f"url_list:{url_list}")
    idx = random.randint(0, len(url_list) -1)
    return url_list[idx]
class WebSocketProxy:
    def __init__(self):
        self.asr_url = get_asr_url()
        self.listen_host = SERVER_HOST
        self.listen_port = SERVER_PORT
        self.active_connections: Dict[str, dict] = {}
        self.llm_slots = asyncio.Semaphore(max(1, LLM_MAX_CONCURRENCY))

    async def _run_llm(self, connection_id: str, func, *args):
        async with self.llm_slots:
            started = time.perf_counter()
            logger.info(f"[{connection_id}] 开始大模型调用: {func.__name__}")
            try:
                return await asyncio.to_thread(func, *args)
            finally:
                elapsed = time.perf_counter() - started
                logger.info(f"[{connection_id}] 大模型调用结束: {func.__name__}, elapsed={elapsed:.3f}s")

    async def _connect_to_asr(self, connection_id: str):
        """连接到上游 WebSocket 服务端 (S2)"""
        try:
            asr_url = get_asr_url()
            logger.info(f"[{connection_id}] 正在连接到上游服务端 {asr_url}")
            asr_ws = await websockets.connect(asr_url, ping_interval=100, ping_timeout=200)
            info = self.active_connections.get(connection_id)
            await configure_server(asr_ws, info)

            logger.info(f"[{connection_id}] 已成功连接到上游服务端")
            return asr_ws
        except Exception as e:
            logger.error(f"[{connection_id}] 连接上游服务端失败: {traceback.format_exc()}")
            return None
    
    # 休眠期间，将fs传过来的音频丢掉
    async def _forward_fsc_to_null(self, fsc_ws, connection_id: str, lock: asyncio.Lock):
        async for message in fsc_ws:
            pass


    async def _forward_fsc_to_asr(self, fsc_ws, asr_ws, connection_id: str, lock: asyncio.Lock):
        """将客户端 (C1) 的消息转发到上游服务端 (S2)"""
        try:
            async for message in fsc_ws:
                send_to_client = None
                should_break = False
                should_forward = False

                async with lock:
                    info = self.active_connections.get(connection_id)
                    if not info or info.get('is_finished'):
                        print("_forward_fsc_to_asr --------任务已完成，退出循环.....")
                        should_break = True
                    else:
                        now = time.time()
                        #超时判断
                        if now > info['last_start_time'] + info['timeout']:

                            info['asr_result'] = []
                            # 超时但未超轮,播放异常提示音
                            if info['current_retry'] <= info['max_retry']:
                                # 第二轮及之后，都是invalid_audio+7s
                                size,duration = get_audio_info(info["invalid_audio"])
                                if duration:
                                    info['timeout'] = duration + 7

                                info['current_retry'] = info['current_retry'] + 1
                                info['last_start_time'] = now
                                send_to_client = {
                                  "type": "urlAudio",
                                  "url": info["invalid_audio"] or '',
                                  "isFinished": 0
                                }
                                logger.error(send_to_client)
                            else:
                                info['is_finished'] = True
                                send_to_client = {
                                    "type": "cmd",
                                    "url": '',
                                    "isFinished": 1
                                }
                                logger.error(send_to_client)
                                should_break = True
                        else:
                            message = apply_audio_gate(message, info, connection_id)
                            should_forward = True

                if send_to_client:
                    await _send_json(fsc_ws, send_to_client)

                if should_break:
                    break

                if not should_forward:
                    continue

                # 转发消息到 asr_ws
                await asr_ws.send(message)
        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 客户端连接已关闭")
        except Exception as e:
            logger.error("---------------------")
            logger.error(traceback.format_exc())
            logger.error(f"[{connection_id}] 转发客户端消息时出错: {str(e)}")
        finally:
            # 关闭 S2 连接
            await asr_ws.close()

    async def _forward_asr_to_fsc(self, fsc_ws, asr_ws, connection_id: str, lock: asyncio.Lock):
        """将上游服务端 (S2) 的消息转发回客户端 (C1)"""
        try:
            async for message in asr_ws:
                async with lock:
                    info = self.active_connections.get(connection_id)
                    if not info or info.get('is_finished'):
                        print("_forward_asr_to_fsc --------任务已完成，退出循环.....")
                        break

                try:
                    logger.warning(f"收到asr结果 {message}")
                    asr_result = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning(f"[{connection_id}] 收到非 JSON ASR 消息: {message}")
                    continue

                # if asr_result.get("mode") != '2pass-offline':
                #     continue
                if asr_result.get("type") != 'result':
                    continue
                
                raw_text = asr_result.get("text") or ""
                '''if raw_text.startswith('/'):
                    continue
                if detect_asr_abnormal(raw_text)['is_abnormal']:
                    logger.warning(f"asr 识别结果不符合要求，丢弃：{raw_text}")
                    continue'''
                clean_text = normalize_asr_text(raw_text)
                is_noise, reason = is_probably_noise_text(clean_text, info["min_asr_text_chars"])
                if is_noise:
                    logger.info(f"[{connection_id}] 忽略低质量 ASR 片段({reason}): {raw_text}")
                    asr_result["text"] = ''
                    continue

                async with lock:
                    changed = append_asr_segment(info['asr_result'], clean_text, info["max_asr_segments"])
                    transcript = build_transcript(info['asr_result'], info["max_transcript_chars"])

                if not changed:
                    logger.debug(f"[{connection_id}] 忽略重复 ASR 片段: {clean_text}")
                    continue
                if not transcript_ready(transcript, info["min_transcript_chars"]):
                    logger.debug(f"[{connection_id}] ASR 文本尚不足以识别意图: {transcript}")
                    continue

                print(f"asr_result ---------------- {transcript}")
                if info["task_type"] == "ytsb":
                    try:
                        config = json.loads(info["config"] or "[]")
                        kf_question = json.loads(info["question"])['question']
                    except json.JSONDecodeError:
                        logger.error(f"[{connection_id}] ytsb config 不是合法 JSON")
                        continue

                    result = await self._run_llm(connection_id, ytsb, config, kf_question, transcript)
                    result_json = _parse_json_object(result)
                    if not result_json:
                        logger.warning(f"[{connection_id}] 意图识别返回非 JSON: {result}")
                        continue

                    intent_type = result_json.get("intent_type")
                    categories = [i.get('category') for i in config if isinstance(i, dict)]
                    if intent_type in categories and _confidence_ok(result_json, info["intent_confidence_threshold"]):
                        if not _confirm_value(info, "intent", intent_type, info["intent_confirm_times"]):
                            logger.info(f"[{connection_id}] 意图 {intent_type} 等待更多确认")
                            continue
                        result_ytsb = {
                            "type": "setChannelVar",
                            "isFinished": 1,
                            "channelVarName": info.get("channel_var_name"),
                            "channelVarValue": intent_type,
                        }
                        async with lock:
                            info['is_finished'] = True
                        await _send_json(fsc_ws, result_ytsb)
                        break
                    else:
                        pass
                elif info["task_type"] == "xxcq":
                    try:
                        conf = json.loads(info["config"] or "{}")
                        kf_question = json.loads(info["question"])['question']
                    except json.JSONDecodeError:
                        logger.error(f"[{connection_id}] xxcq config 不是合法 JSON")
                        continue

                    result = await self._run_llm(connection_id, xxcq, conf, kf_question, transcript)
                    result_json = _parse_json_object(result)
                    if not result_json:
                        logger.warning(f"[{connection_id}] 信息抽取返回非 JSON: {result}")
                        continue

                    rec_result = result_json.get("rec_result")
                    if (
                        _is_rec_success(result_json)
                        and _confidence_ok(result_json, info["intent_confidence_threshold"])
                        and _valid_rec_result(conf, rec_result)
                    ):
                        rec_text = str(rec_result)
                        if not _confirm_value(info, "rec", rec_text, info["intent_confirm_times"]):
                            logger.info(f"[{connection_id}] 抽取结果 {rec_text} 等待更多确认")
                            continue
                        result_xxcq = {
                            "type": "setChannelVar",
                            "isFinished": 1,
                            "channelVarName": info.get("channel_var_name"),
                            "channelVarValue": rec_text,
                        }
                        async with lock:
                            info['is_finished'] = True
                        await _send_json(fsc_ws, result_xxcq)
                        break
                # else:
                #     # 转发消息回 C1
                #     await fsc_ws.send(message)
                #     logger.debug(f"[{connection_id}] 已转发消息回客户端")

        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 上游服务端连接已关闭")
        except Exception as e:
            logger.error("########################")
            logger.error(traceback.format_exc())
            logger.error(f"[{connection_id}] 转发上游消息时出错: {str(e)}")
        finally:
            # 关闭 C1 连接
            await fsc_ws.close()

    async def proxy_handler(self, fsc_ws):
        """处理客户端连接并建立到 S2 的代理"""
        connection_id = str(uuid.uuid4())
        logger.info(f"[{connection_id}] 新的客户端连接来自 {fsc_ws.remote_address}")

        headers = dict(fsc_ws.request.headers)

        logger.error("####################################")
        logger.error(headers)
        logger.error("####################################")
        lock = asyncio.Lock()

        # 记录连接
        self.active_connections[connection_id] = {
            "fsc": fsc_ws,
            "asr": None,
            "remote_address": fsc_ws.remote_address,
            "asr_result": [],
            "is_finished": False,
            "task_type":  headers.get("task_type", "ytsb"), #任务类型：ytsb、xxcq
            "config": headers.get("config"), #运行配置信息
            "question": headers.get("question", ""),
            "max_retry": _get_int_header(headers, "max_retry", 1), #最大重试次数
            "current_retry": 1,
            "timeout": _get_int_header(headers, "retry_timeout", 10), #每轮超时时间
            "last_start_time": time.time(), #当前轮次的开始时间，用于超时判断
            "invalid_audio":  headers.get("invalid_audio"), #识别出错提示音
            "channel_var_name": headers.get("channel_var_name"),
            "welcome_audio": headers.get("welcome_audio"),
            "min_asr_text_chars": _get_int_header(headers, "min_asr_text_chars", ASR_MIN_TEXT_CHARS),
            "min_transcript_chars": _get_int_header(headers, "min_transcript_chars", ASR_MIN_TRANSCRIPT_CHARS),
            "max_transcript_chars": _get_int_header(headers, "max_transcript_chars", ASR_MAX_TRANSCRIPT_CHARS),
            "max_asr_segments": _get_int_header(headers, "max_asr_segments", ASR_MAX_SEGMENTS),
            "intent_confidence_threshold": _get_float_header(headers, "intent_confidence_threshold", INTENT_CONFIDENCE_THRESHOLD),
            "intent_confirm_times": _get_int_header(headers, "intent_confirm_times", INTENT_CONFIRM_TIMES),
            "last_intent": None,
            "intent_hit_count": 0,
            "last_rec": None,
            "rec_hit_count": 0,
            "audio_gate_enabled": _get_bool_header(headers, "audio_gate_enabled", AUDIO_GATE_ENABLED),
            "audio_gate_threshold_db": _get_float_header(headers, "audio_gate_threshold_db", AUDIO_GATE_THRESHOLD_DB),
            "audio_gate_start_ms": _get_int_header(headers, "audio_gate_start_ms", AUDIO_GATE_START_MS),
            "audio_gate_end_ms": _get_int_header(headers, "audio_gate_end_ms", AUDIO_GATE_END_MS),
            "audio_noise_calibrate_ms": _get_int_header(headers, "audio_noise_calibrate_ms", AUDIO_NOISE_CALIBRATE_MS),
            "audio_noise_margin_db": _get_float_header(headers, "audio_noise_margin_db", AUDIO_NOISE_MARGIN_DB),
            "audio_gate_threshold_db_min": _get_float_header(headers, "audio_gate_threshold_db_min", AUDIO_GATE_THRESHOLD_DB_MIN),
            "audio_sample_rate": _get_int_header(headers, "audio_sample_rate", AUDIO_SAMPLE_RATE),
            "audio_channels": _get_int_header(headers, "audio_channels", AUDIO_CHANNELS),
            "audio_sample_width": _get_int_header(headers, "audio_sample_width", AUDIO_SAMPLE_WIDTH),
            "audio_gate_open": True,
            "audio_gate_loud_ms": 0.0,
            "audio_gate_quiet_ms": float(_get_int_header(headers, "audio_gate_end_ms", AUDIO_GATE_END_MS)),
            "audio_gate_calibrated": _get_int_header(headers, "audio_noise_calibrate_ms", AUDIO_NOISE_CALIBRATE_MS) <= 0,
            "audio_noise_calibrated_ms": 0.0,
            "audio_noise_levels": []
        }

        try:
            # 连接到上游 WebSocket 服务端 (S2)
            asr_ws = await self._connect_to_asr(connection_id)
            if not asr_ws:
                await fsc_ws.close(code=1011, reason="无法连接上游服务")
                return
            message = json.dumps(
                {"chunk_size":[5,10,5],"wav_name":"h5","is_speaking":True,"chunk_interval":10,"itn":True,"mode":"online"}
            )
            # voices.put(message)
            await asr_ws.send(message)
            # 更新连接记录
            self.active_connections[connection_id]["asr"] = asr_ws

            result = {
                "type": "urlAudio",
                "url": headers.get("welcome_audio"),
                "isFinished": 0
            }
            logger.info(result)
            await _send_json(fsc_ws, result)

            fsc_to_null = None
            size, duration = get_audio_info(headers.get("welcome_audio"))
            if duration and duration > 2.3:
                logger.info("开始休眠.....")
                fsc_to_null = asyncio.create_task(self._forward_fsc_to_null(fsc_ws, connection_id, lock))
                await asyncio.sleep(duration-2)

            if fsc_to_null:
                fsc_to_null.cancel()

            logger.info("开始创建asr任务.....")
            # 创建转发任务
            fsc_to_asr = asyncio.create_task(self._forward_fsc_to_asr(fsc_ws, asr_ws, connection_id, lock))
            asr_to_fsc = asyncio.create_task(self._forward_asr_to_fsc(fsc_ws, asr_ws, connection_id, lock))

            # 等待任一任务完成
            await asyncio.wait([fsc_to_asr, asr_to_fsc], return_when=asyncio.FIRST_COMPLETED)

            # 取消仍在运行的任务
            for task in [fsc_to_asr, asr_to_fsc]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            await asr_ws.close()
            logger.info(f"[{connection_id}] 代理连接已关闭")

        except Exception as e:
            logger.error(f"[{connection_id}] 代理处理过程中出错: {str(e)}")
        finally:
            # 清理连接
            if connection_id in self.active_connections:
                del self.active_connections[connection_id]

    def process_message(self, message: str) -> str:
        """处理从客户端收到的消息（可重写此方法实现自定义逻辑）"""
        # 示例：解析 JSON 消息
        try:
            data = json.loads(message)
            logger.debug(f"处理 JSON 消息: {data}")
            # 这里可以修改消息内容
            return _safe_json_dumps(data)
        except json.JSONDecodeError:
            return message

    def process_response(self, response: str) -> str:
        """处理从上游服务端收到的响应（可重写此方法实现自定义逻辑）"""
        # 示例：添加时间戳
        try:
            data = json.loads(response)
            data["proxy_timestamp"] = int(time.time() * 1000)
            return _safe_json_dumps(data)
        except json.JSONDecodeError:
            return response

    async def start(self):
        """启动 WebSocket 代理服务器"""
        server = await websockets.serve(
            self.proxy_handler,
            self.listen_host,
            self.listen_port,
            ping_interval=30,
            ping_timeout=60,
            close_timeout=10
        )

        logger.info(f"代理服务器已启动，监听 ws://{self.listen_host}:{self.listen_port}")
        logger.info(f"上游服务端: {self.asr_url}")
        logger.info("等待客户端连接...")

        await server.wait_closed()

    async def close_all_connections(self):
        """关闭所有活动连接"""
        logger.info("正在关闭所有活动连接...")
        for conn_id, conn_info in list(self.active_connections.items()):
            try:
                if conn_info["fsc"] and not conn_info["fsc"].closed:
                    await conn_info["fsc"].close(code=1000, reason="服务器关闭")
                if conn_info["asr"] and not conn_info["asr"].closed:
                    await conn_info["asr"].close()
            except Exception as e:
                print(f"关闭连接 {conn_id} 时出错: {str(e)}")
        self.active_connections.clear()


# 运行服务器
if __name__ == "__main__":
    import argparse
    import signal
    import sys

    # 解析命令行参数
    parser = argparse.ArgumentParser(description='WebSocket 代理服务器')
    parser.add_argument('--listen-host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--listen-port', type=int, default=8766, help='监听端口 (默认: 8765)')
    parser.add_argument('--asr-url', default='ws://127.0.0.1:8765', help='上游 WebSocket 服务端 URL (S2)')
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], help='日志级别')

    args = parser.parse_args()

    # 设置日志级别
    logger.setLevel(args.log_level)

    # 创建代理服务器
    server = WebSocketProxy()

    # 设置信号处理
    loop = asyncio.get_event_loop()

    try:
        # 启动服务器
        loop.run_until_complete(server.start())
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("用户中断，正在停止服务器...")
    finally:
        loop.run_until_complete(server.close_all_connections())
        loop.close()
        logger.info("服务器已关闭")
        sys.exit(0)


