import asyncio
import websockets
import json
from threading import Lock
import logging
import uuid
import time
import traceback
from typing import Dict
import numpy as np
from config import *
from SnowflakeID import SnowflakeID
from StringUtils import remove_leading_punctuation

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('WebSocket4AsrOnly')


class WebSocket4AsrOnly:
    def __init__(self):
        self.asr_url = ASR_URL
        self.listen_host = SERVER_HOST
        self.listen_port = ASR_ONLY_SERVER_PORT
        self.active_connections: Dict[str, dict] = {}
        self.snowflake = SnowflakeID(1)

    async def _connect_to_asr(self, connection_id: str, channel_type):
        """连接到上游 WebSocket 服务端 (S2)"""
        try:
            logger.info(f"[{connection_id}] {channel_type} 正在连接到上游服务端 {self.asr_url}")
            asr_ws = await websockets.connect(self.asr_url, ping_interval=20, ping_timeout=40)
            logger.info(f"[{connection_id}] {channel_type} 已成功连接到上游服务端")

            message = json.dumps(
                {"chunk_size": [5, 10, 5],
                 "wav_name": f"{connection_id}_{channel_type}",
                 "is_speaking": True,
                 "chunk_interval": 10,
                 "itn": True,
                 "mode": "2pass"}
            )
            await asr_ws.send(message)

            return asr_ws
        except Exception as e:
            logger.error(f"[{connection_id}] 连接上游服务端失败: {str(e)}")
            return None

    async def _forward_fsc_to_asr(self, fsc_ws, asr_ws_left, asr_ws_right, connection_id: str, lock: Lock):
        """将客户端 (C1) 的消息转发到上游服务端 (S2)"""
        try:
            async for message in fsc_ws:
                # 将字节数据转换为int16数组
                audio_int16 = np.frombuffer(message, np.int16)
                left_channel = audio_int16[0::2]
                right_channel = audio_int16[1::2]
                await asr_ws_left.send(left_channel.tobytes())
                await asr_ws_right.send(right_channel.tobytes())
        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 客户端连接已关闭")
        except Exception as e:
            traceback.print_exc()
            logger.error(f"[{connection_id}] 转发客户端消息时出错: {str(e)}")
        finally:
            # 关闭 S2 连接
            await asr_ws_left.close()
            await asr_ws_right.close()

    async def _forward_asr_to_fsc(self, fsc_ws, asr_ws, channel_type, connection_id: str, lock: Lock):
        """将上游服务端 (S2) 的消息转发回客户端 (C1)"""
        try:
            current_id = None
            finished = 0
            msg_time = int(time.time()*1000)

            async for message in asr_ws:
                if not current_id:
                    with lock:
                        current_id = self.snowflake.generate_id()
                        msg_time = int(time.time() * 1000)

                asr_result = json.loads(message)
                if asr_result and asr_result.get("text"):
                    if asr_result.get("mode") == '2pass-online':
                        # 发送过offline消息，收到online消息 需要重新生成雪花id
                        if finished:
                            with lock:
                                current_id = self.snowflake.generate_id()
                                msg_time = int(time.time() * 1000)
                                finished = 0
                    elif asr_result.get("mode") == '2pass-offline':
                        #要考虑 两条offile消息中间，没有online消息
                        if finished:
                            with lock:
                                current_id = self.snowflake.generate_id()
                                msg_time = int(time.time() * 1000)
                        else:
                            finished = 1
                            
                    asr_send = {
                        "id": current_id, #消息雪花id
                        "time": msg_time, #消息开始时间
                        "channel": channel_type, #左右声道
                        "finished": finished, # 0表示continue 1表示本句话结束
                        "text": remove_leading_punctuation(asr_result.get("text"))
                    }
                    await fsc_ws.send(json.dumps(asr_send, ensure_ascii=False))

        except websockets.ConnectionClosed:
            logger.info(f"[{connection_id}] 上游服务端连接已关闭")
        except Exception as e:
            logger.error(f"[{connection_id}] 转发上游消息时出错: {str(e)}")
        finally:
            # 关闭 C1 连接
            await fsc_ws.close()

    async def proxy_handler(self, fsc_ws):
        """处理客户端连接并建立到 S2 的代理"""
        connection_id = str(uuid.uuid4())
        logger.info(f"[{connection_id}] 新的客户端连接来自 {fsc_ws.remote_address}")

        headers = dict(fsc_ws.request.headers)
        lock = Lock()

        # 记录连接
        self.active_connections[connection_id] = {
            "fsc": fsc_ws,
            "asr_left": None,
            "asr_right": None,
            "remote_address": fsc_ws.remote_address,
            "asr_result": [],
            "is_finished": False
        }

        try:
            # 连接到上游 WebSocket 服务端 (S2)
            asr_ws_left = await self._connect_to_asr(connection_id, "left")

            if not asr_ws_left:
                await fsc_ws.close(code=1011, reason="无法连接上游服务")
                return

            asr_ws_right = await self._connect_to_asr(connection_id, "right")
            if not asr_ws_right:
                await fsc_ws.close(code=1011, reason="无法连接上游服务")
                return
            # 更新连接记录
            self.active_connections[connection_id]["asr_left"] = asr_ws_left
            self.active_connections[connection_id]["asr_right"] = asr_ws_right

            # 创建转发任务
            fsc_to_asr = asyncio.create_task(self._forward_fsc_to_asr(fsc_ws, asr_ws_left, asr_ws_right, connection_id, lock))
            asr_left_to_fsc = asyncio.create_task(self._forward_asr_to_fsc(fsc_ws, asr_ws_left, "left", connection_id, lock))
            asr_right_to_fsc = asyncio.create_task(self._forward_asr_to_fsc(fsc_ws, asr_ws_right,"right", connection_id, lock))

            # 等待任一任务完成
            await asyncio.wait([fsc_to_asr, asr_left_to_fsc, asr_right_to_fsc], return_when=asyncio.FIRST_COMPLETED)

            # 取消仍在运行的任务
            for task in [fsc_to_asr, asr_left_to_fsc, asr_right_to_fsc]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            await asr_ws_left.close()
            await asr_ws_right.close()
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
    import signal
    import sys

    # 解析命令行参数
    parser = argparse.ArgumentParser(description='WebSocket 代理服务器')
    parser.add_argument('--listen-host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--listen-port', type=int, default=8099, help='监听端口 (默认: 8765)')
    parser.add_argument('--asr-url', default='ws://135.31.67.201:10096', help='上游 WebSocket 服务端 URL (S2)')
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], help='日志级别')

    args = parser.parse_args()

    # 设置日志级别
    logger.setLevel(args.log_level)

    # 创建代理服务器
    server = WebSocket4AsrOnly()

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