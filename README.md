# 语音智能客服 WebSocket 网关

面向热线客服场景，打通 FreeSWITCH、ASR、大语言模型和 RAGFlow Agent，实现实时语音识别、意图判断、信息抽取和智能问答。

## 功能特性

- **实时语音网关**：使用 Python asyncio + websockets 开发实时语音客服网关，连接 FreeSWITCH、ASR、RAGFlow Agent 和 TTS 服务
- **多任务音频处理**：通过异步任务处理音频帧转发、ASR 结果接收、Agent 对话和 TTS 回传
- **VAD 静音检测**：增加 VAD 静音检测和音频能量判断，控制对话触发时机
- **完整通话流程**：完善欢迎语、TTS 播放、通话结束、超时和转人工等流程，支持将处理状态回传至通话系统

## 技术栈

| 层级 | 技术选型 |
|---|---|
| 通信框架 | Python asyncio、websockets |
| 语音处理 | 音频帧转发、VAD 静音检测、能量判断 |
| 对接服务 | FreeSWITCH、ASR、RAGFlow Agent、TTS |
| 业务流程 | 欢迎语播报、对话管理、转人工、超时处理 |

## 架构说明

```
FreeSWITCH ──音频流──▶ WebSocket 网关 ──文本──▶ ASR
                          │                      │
                          │◀──识别结果───────────┘
                          │
                          ├──▶ RAGFlow Agent（意图理解 + 问答）
                          │
                          ├──▶ TTS（语音合成）
                          │
                          └──▶ 通话状态回传
```

## 快速开始

```bash
pip install -r requirements.txt
python WebSocketAiAgent.py
```

## 项目结构

```
Voice Customer Service WebSocket Gateway/
├── WebSocketAiAgent.py      # 主网关入口
├── AiAgent.py               # Agent 对话逻辑
├── AudioUtil.py             # 音频处理工具
├── WebSocketProxy.py        # WebSocket 代理
├── WebSocket4AsrOnly.py     # 纯 ASR 模式
├── asr_text_filter.py       # ASR 文本过滤
├── config.py                # 配置文件
├── SnowflakeID.py           # 雪花 ID 生成
├── StringUtils.py           # 字符串工具
├── xxcq.py                  # 业务模块
└── ytsb.py                  # 业务模块
```

## 核心流程

1. FreeSWITCH 通过 WebSocket 推送音频流到网关
2. 网关将音频帧转发给 ASR 服务进行实时语音识别
3. ASR 返回识别文本，经过文本过滤后传给 RAGFlow Agent
4. Agent 生成回复，调用 TTS 合成语音
5. TTS 音频回传给 FreeSWITCH 播放给用户
6. 检测到静音或超时，触发结束或转人工流程
