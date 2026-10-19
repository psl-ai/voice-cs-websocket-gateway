import asyncio
import json
import logging
import time
import traceback
import uuid
from threading import Lock
from typing import Dict
from urllib.parse import quote
from base64 import b64encode
import webrtcvad
import websockets

from AiAgent import AiAgent
from SnowflakeID import SnowflakeID
from StringUtils import remove_leading_punctuation
from config import *
from queue import Queue
#import noisereduce as nr  # 降噪
import numpy as np
#import edge_tts

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s'
)
logger = logging.getLogger('WebSocketAiAgent')


class WebSocketAiAgent:
    def __init__(self):
        self.asr_url = ASR_URL
        self.listen_host = SERVER_HOST
        self.listen_port = AI_AGENT_SERVER_PORT
        self.active_connections: Dict[str, dict] = {}
        self.snowflake = SnowflakeID(1)

    async def _connect_to_asr(self, connection_id: str):
        """连接到上游 WebSocket 服务端 (S2)"""
        try:
            logger.info(f"[{connection_id}] 正在连接到上游服务端 {self.asr_url}")
            asr_ws = await websockets.connect(self.asr_url, ping_interval=20, ping_timeout=40)
            logger.info(f"[{connection_id}] 已成功连接到上游服务端")
            return asr_ws
        except Exception as e:
            logger.error(f"[{connection_id}] 连接上游服务端失败: {str(e)}")
            return None

    async def _forward_fsc_to_asr(self, fsc_ws, asr_ws, queue, connection_id: str, lock: Lock):
        """将客户端 (C1) 的消息转发到上游服务端 (S2)"""
        try:
            vad = webrtcvad.Vad(3)
            sample_rate = 16000
            logger.info("-----------------_forward_fsc_to_asr--------")
            i = 0
            async for message in fsc_ws:
                i += 1
                with lock:
                    info = self.active_connections.get(connection_id)
                    if info['is_finished']:
                        logger.info("_forward_fsc_to_asr --------任务已完成，退出循环.....")
                        break

                    now = time.time()
                    # 智能坐席对话超时，发送结束命令
                    if now - info["start_time"] > info["timeout"]:
                        info['is_finished'] = True
                        finish_cmd = {
                            "type": "setChannelVar",
                            "isFinished": 1,
                            "channelVarName": info.get("channel_var_name"),
                            "channelVarValue": "timeout",
                        }
                        await fsc_ws.send(json.dumps(finish_cmd, ensure_ascii=False))

                int16_array = np.frombuffer(message, dtype=np.int16)

                #reduce_noise = nr.reduce_noise(int16_array.astype(np.float32), sample_rate, stationary=False,
                #                               n_std_thresh_stationary=3)
                energy = np.sqrt(np.mean((int16_array.astype(np.float32) / 32768.0) ** 2))
                #message = reduce_noise.astype(np.int16).tobytes()

                is_speeking = vad.is_speech(message, sample_rate) and energy> 0.003
                if is_speeking:
                    print(f"is_speeking ===> {is_speeking}.......energy==>{energy}")
                else:
                    message = np.zeros_like(int16_array).astype(np.int16).tobytes()

                with lock:
                    if not is_speeking:
                        info["silence_frame_num"] += 1
                    else:
                        info["silence_frame_num"] = 0

                    is_agent_querying = info.get('agent_querying', False)
                    agent_querying_last_time = info.get('last_agent_querying_time', 0)

                    # if is_agent_querying and time.time() > agent_querying_last_time + 5:
                    #     # 智能体查询中，5秒一次的提醒
                    #     result = {
                    #         "type": "urlAudio",
                    #         "url": info["remote_tts_prefix"] + quote("查询中,请耐心等待"),
                    #         "isFinished": 0,
                    #         "tips": True
                    #     }
                    #     queue.put(result)
                    #     info['last_agent_querying_time'] = int(time.time())
                    # 这个地方有个小问题，静音片段确定需要基于asr结果去调用智能体，但是可能最后一句的asr结果还没到达，这个要做下处理
                    if info["silence_frame_num"] >= info["silence_frame_max_num"]:
                        # 添加一个处理，当前如果是在大模型查询中，静音片段缩减10frame ==>0.2s,让大模型下一轮次对话晚一点
                        if is_agent_querying:
                            info["silence_frame_num"] -= 5
                        else:
                            info["silence_frame_num"] = 0
                            info["ai_agent_chat"] = True

                # 转换为float32进行运算
                # audio = np.frombuffer(message, dtype=np.int16).astype(np.float32)
                # print("---------", audio)
                # 方法1：能量衰减 (减小远处声音)
                # audio *= 0.3  # 30%能量衰减
                # print("#########", audio)
                # message = audio.astype(np.int16).tobytes()
                # 转发消息到 asr_ws
                await asr_ws.send(message)
        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 客户端连接已关闭")
        except Exception as e:
            traceback.print_exc()
            logger.error(f"[{connection_id}] 转发客户端消息时出错: {str(e)}")
        finally:
            # 关闭 S2 连接
            await asr_ws.close()

    async def _forward_asr_result_to_agent(self, fsc_ws, queue, connection_id: str, lock: Lock):
        """将上游服务端 (S2) 的消息转发回客户端 (C1)"""
        try:
            with lock:
                info = self.active_connections.get(connection_id)

            agent = AiAgent()
            # 第一次消息是查询引导语
            success, reply = await agent.chat("")
            if success:
                with lock:
                    current_id = self.snowflake.generate_id()

                asr_send = {
                    "id": current_id,  # 消息雪花id
                    "time": int(time.time() * 1000),  # 消息开始时间
                    "type": "agent",
                    "channel": "right",  # 左右声道
                    "finished": 1,  # 0表示continue 1表示本句话结束
                    "text": remove_leading_punctuation(reply.get('txt')),
                    "msgType": reply.get("msgType")
                }
                await fsc_ws.send(json.dumps(asr_send, ensure_ascii=False))

                result = {
                    "type": "urlAudio",
                    "url": info["remote_tts_prefix"] + quote(reply.get('txt')),
                    "isFinished": 0
                }
                with lock:
                    queue.put(result)

            else:
                return

            while True:
                with lock:
                    info = self.active_connections.get(connection_id)
                    if info['is_finished']:
                        logger.info("_forward_asr_to_fsc --------任务已完成，退出循环.....")
                        break

                if not info['ai_agent_chat']:
                    # print("sleeping...")
                    # 静音片段总时长+当前休眠时间，是asr识别最大延迟，否则效果会出问题
                    await asyncio.sleep(0.5)
                    continue

                with lock:
                    if not info['asr_result']:
                        info['ai_agent_chat'] = False
                        continue

                    ai_message = " ".join(info['asr_result'])
                    info['ai_agent_chat'] = False
                    info['asr_result'] = []

                    info['agent_querying'] = True
                    info['last_agent_querying_time'] = int(time.time())

                # 这个地方不能占用锁
                if ai_message:
                    # success, reply = agent.chat(ai_message)
                    logger.info(f"=========ai_message:{ai_message}===========")
                    success, reply = await agent.chat(ai_message)
                    if success:
                        # 会话结束，这个地方需要休眠1s，等待tts播放结束
                        if reply.get("type").startswith("finish") or reply.get("type").startswith("transfer"):
                            await asyncio.sleep(4)
                            finish_cmd = {
                                "type": "setChannelVar",
                                "isFinished": 1,
                                "channelVarName": info.get("channel_var_name"),
                                "channelVarValue": "finish" if reply.get("type").startswith("finish") else "transfer",
                            }
                            await fsc_ws.send(json.dumps(finish_cmd, ensure_ascii=False))
                            break
                        elif reply.get("type").startswith("none"):
                            with lock:
                                info['agent_querying'] = False
                            continue
                        else:
                            if not reply.get('txt'):
                                with lock:
                                    info['agent_querying'] = False
                                continue

                            with lock:
                                current_id = self.snowflake.generate_id()

                            asr_send = {
                                "id": current_id,  # 消息雪花id
                                "time": int(time.time() * 1000),  # 消息开始时间
                                "channel": "right",  # 左右声道
                                "finished": 1,  # 0表示continue 1表示本句话结束
                                "text": remove_leading_punctuation(reply.get('txt')),
                                "msgType": reply.get("msgType")
                            }
                            await fsc_ws.send(json.dumps(asr_send, ensure_ascii=False))

                            result = {
                                 "type": "urlAudio",
                                 "url": info["remote_tts_prefix"] + quote(reply.get('txt').replace(".", "。")),
                                 "isFinished": 0
                            }
                            
                            with lock:
                                info['agent_querying'] = False
                                #queue.put(result)
                                await fsc_ws.send(json.dumps(result, ensure_ascii=False))
                            #with lock:
                            #    info['agent_querying'] = False

                            

                            #communicate = edge_tts.Communicate(reply.get('txt'), "zh-CN-YunjianNeural")
                            #chunks = []
                            #async for chunk in communicate.stream():
                            #    # print(f"chunk.....{int(int(time.time()%1000))}")
                            #    if chunk["type"] == "audio":
                            #        # file.write(chunk["data"])
                            #        chunks.append(chunk["data"])
                            #    if len(chunks) >= 30:
                            #        result = {
                            #            "type": "streamAudio",
                            #            "data": {
                            #                "audioData": b64encode(b''.join(chunks)).decode(),
                            #                "audioDataType": "mp3",
                            #            },
                            #            "isFinished": 0
                            #        }
                            #        print(result)
                            #        chunks = []
                            #        await fsc_ws.send(json.dumps(result, ensure_ascii=False))
                            #if chunks:
                            #    result = {
                            #        "type": "streamAudio",
                            #        "data": {
                            #            "audioData": b64encode(b''.join(chunks)).decode(),
                            #            "audioDataType": "mp3",
                            #        },
                            #        "isFinished": 0
                            #    }
                            #    print(result)
                            #    await fsc_ws.send(json.dumps(result, ensure_ascii=False))


                            # await fsc_ws.send(json.dumps(result, ensure_ascii=False))


        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 上游服务端连接已关闭")
        except Exception as e:
            logger.error(f"[{connection_id}] 转发上游消息时出错: {str(e)}")
        finally:
            # 关闭 C1 连接
            await fsc_ws.close()

    async def _forward_asr_to_asr_result(self, fsc_ws, asr_ws, queue, connection_id: str, lock: Lock):
        """将上游服务端 (S2) 的消息转发回客户端 (C1)"""
        try:
            async for message in asr_ws:
                with lock:
                    info = self.active_connections.get(connection_id)

                    if info['is_finished']:
                        logger.info("_forward_asr_to_fsc --------任务已完成，退出循环.....")
                        break

                asr_result = json.loads(message)

                if asr_result.get("mode") != '2pass-offline':
                    continue

                if asr_result and asr_result.get("text"):
                    with lock:
                        current_id = self.snowflake.generate_id()
                        info['asr_result'].append(asr_result.get("text"))

                    asr_send = {
                        "id": current_id,  # 消息雪花id
                        "time": int(time.time() * 1000),  # 消息开始时间
                        "type": "agent",
                        "channel": "left",  # 左右声道
                        "finished": 1,  # 0表示continue 1表示本句话结束
                        "text": remove_leading_punctuation(asr_result.get("text")),
                        "msgType": "asr"
                    }
                    await fsc_ws.send(json.dumps(asr_send, ensure_ascii=False))
        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 上游服务端连接已关闭")
        except Exception as e:
            logger.error(f"[{connection_id}] 转发上游消息时出错: {str(e)}")
        finally:
            pass

    async def _queue_tts_to_fsc(self, fsc_ws, queue, connection_id: str, lock: Lock):
        """将上游服务端 (S2) 的消息转发回客户端 (C1)"""
        while True:
            try:
                tts_msg = None
                with lock:
                    info = self.active_connections.get(connection_id)
                    if not info:
                        break

                    if info['is_finished']:
                        logger.info("_queue_tts_to_fsc --------任务已完成，退出循环.....")
                        break
                    is_agent_querying = info.get("agent_querying", False)
                    if not queue.empty():
                        try:
                            tts_msg = queue.get(block=False)
                        except Exception as e:
                            logger.error(f"[{connection_id}] _queue_tts_to_fsc: {traceback.format_exc()}")

                        # 队列中有其他语音信息，不播放提示语
                        if tts_msg.get("tips") and queue.qsize() > 1:
                            continue
                if tts_msg:
                    # 大模型已经查询出结果，则不播提示音
                    if tts_msg.get("tips") and not is_agent_querying:
                        continue

                    await fsc_ws.send(json.dumps(tts_msg, ensure_ascii=False))
                else:
                    await asyncio.sleep(0.2)

            except Exception as e:
                logger.info(f"[{connection_id}] _queue_tts_to_fsc : {traceback.format_exc()}")
                await asyncio.sleep(0.1)

    async def proxy_handler(self, fsc_ws):
        """处理客户端连接并建立到 S2 的代理"""
        connection_id = str(uuid.uuid4())
        logger.info(f"[{connection_id}] 新的客户端连接来自 {fsc_ws.remote_address}")

        headers = dict(fsc_ws.request.headers)
        lock = Lock()

        # 记录连接
        self.active_connections[connection_id] = {
            "fsc": fsc_ws,
            "asr": None,
            "remote_address": fsc_ws.remote_address,
            "asr_result": [],
            "silence_frame_num": 0,
            "ai_agent_chat": False,  # 为False表示静音片段不足，需要等待进一步asr结果， True表示可以进行对话了
            "is_finished": False,
            "start_time": time.time(),  # 当前轮次的开始时间，用于超时判断
            "silence_frame_max_num": int(headers.get("silence_frame_max_num", 50)),  # 最大静音片段
            "timeout": int(headers.get("timeout", 300)),  # 超时时间
            "remote_tts_prefix": headers.get("remote_tts_prefix", "remote_tts_prefix"),  # tts语音url
            "channel_var_name": headers.get("channel_var_name", "channel_var_name"),
        }

        try:
            # 连接到上游 WebSocket 服务端 (S2)
            asr_ws = await self._connect_to_asr(connection_id)
            if not asr_ws:
                await fsc_ws.close(code=1011, reason="无法连接上游服务")
                return
            message = json.dumps(
                {"chunk_size": [5, 10, 5], "wav_name": "h5", "is_speaking": True, "chunk_interval": 10, "itn": True,
                 "mode": "online"}
            )
            # voices.put(message)
            await asr_ws.send(message)
            with lock:
                # 更新连接记录
                self.active_connections[connection_id]["asr"] = asr_ws
            queue = Queue()
            # 创建转发任务
            fsc_to_asr = asyncio.create_task(self._forward_fsc_to_asr(fsc_ws, asr_ws, queue, connection_id, lock))
            asr_to_result = asyncio.create_task(
                self._forward_asr_to_asr_result(fsc_ws, asr_ws, queue, connection_id, lock))
            result_to_agent = asyncio.create_task(self._forward_asr_result_to_agent(fsc_ws, queue, connection_id, lock))
            result_to_agent2 = asyncio.create_task(self._queue_tts_to_fsc(fsc_ws, queue, connection_id, lock))

            # 等待任一任务完成
            await asyncio.wait([fsc_to_asr, asr_to_result, result_to_agent, result_to_agent2],
                               return_when=asyncio.FIRST_COMPLETED)

            # 取消仍在运行的任务
            for task in [fsc_to_asr, asr_to_result, result_to_agent]:
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
    import sys

    # 解析命令行参数
    parser = argparse.ArgumentParser(description='WebSocket 代理服务器')
    parser.add_argument('--listen-host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--listen-port', type=int, default=8765, help='监听端口 (默认: 8765)')
    parser.add_argument('--asr-url', default='ws://127.0.0.1:6066', help='上游 WebSocket 服务端 URL (S2)')
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], help='日志级别')

    args = parser.parse_args()

    # 设置日志级别
    logger.setLevel(args.log_level)

    # 创建代理服务器
    server = WebSocketAiAgent()

    # 设置信号处理
    loop = asyncio.get_event_loop()

    # async def shutdown(signal, frame):
    #     logger.info("收到关闭信号，正在停止服务器...")
    #     await server.close_all_connections()
    #     tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    #     for task in tasks:
    #         task.cancel()
    #     loop.stop()
    #
    #
    # for sig in (signal.SIGINT, signal.SIGTERM):
    #     loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(sig, None)))

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

