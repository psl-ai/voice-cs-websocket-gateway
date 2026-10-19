import os

LLM_API_KEY = os.getenv("LLM_API_KEY", 'sk-c344c620db3f4e5fa365067994a5a2e6')
LLM_BASE_URL =  os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", 10))

#asr配置
ASR_URL = os.getenv("ASR_URL", 'ws://10.1.255.178:8765')

SERVER_HOST = os.getenv("SERVER_HOST",'0.0.0.0')
SERVER_PORT = int(os.getenv("SERVER_PORT", 8077))

ASR_ONLY_SERVER_PORT = int(os.getenv("ASR_ONLY_SERVER_PORT", 8099))
AI_AGENT_SERVER_PORT = int(os.getenv("AI_AGENT_SERVER_PORT", 8095))

AI_AGENT_BASE_URL = os.getenv("AI_AGENT_BASE_URL", "http://10.1.255.178:8088")
AI_AGENT_APIKEY = os.getenv("AI_AGENT_APIKEY", "ragflow-8imAsxMODyEcJu31nQpnJ34BayDRFSgJAptIy5SU0u0")
AI_AGENT_APP_ID = os.getenv("AI_AGENT_APP_ID", "d2a33c20393311f1a15ef59eeec78c8c")
AI_AGENT_NEW_CONVERSATION_API = f"/api/v1/agents/{AI_AGENT_APP_ID}/sessions"
AI_AGENT_CHAT_API = f"/api/v1/agents/{AI_AGENT_APP_ID}/completions"

# ASR 噪声抑制与意图确认配置，可被 WebSocket 连接头覆盖
ASR_MIN_TEXT_CHARS = int(os.getenv("ASR_MIN_TEXT_CHARS", 1))
ASR_MIN_TRANSCRIPT_CHARS = int(os.getenv("ASR_MIN_TRANSCRIPT_CHARS", 1))
ASR_MAX_TRANSCRIPT_CHARS = int(os.getenv("ASR_MAX_TRANSCRIPT_CHARS", 160))
ASR_MAX_SEGMENTS = int(os.getenv("ASR_MAX_SEGMENTS", 12))
INTENT_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", 0.60))
INTENT_CONFIRM_TIMES = int(os.getenv("INTENT_CONFIRM_TIMES", 1))

# PCM16 音频能量门控，来自旧 FunASR 客户端的麦克风降噪思路
AUDIO_GATE_ENABLED = os.getenv("AUDIO_GATE_ENABLED", "1").lower() not in ("0", "false", "no", "off")
AUDIO_GATE_THRESHOLD_DB = float(os.getenv("AUDIO_GATE_THRESHOLD_DB", -90.0))
AUDIO_GATE_THRESHOLD_DB_MIN = float(os.getenv("AUDIO_GATE_THRESHOLD_DB_MIN", -30.0)) #最低不能低于30分贝
AUDIO_GATE_START_MS = int(os.getenv("AUDIO_GATE_START_MS", 80))
AUDIO_GATE_END_MS = int(os.getenv("AUDIO_GATE_END_MS", 700))
AUDIO_NOISE_CALIBRATE_MS = int(os.getenv("AUDIO_NOISE_CALIBRATE_MS", 3000))
AUDIO_NOISE_MARGIN_DB = float(os.getenv("AUDIO_NOISE_MARGIN_DB", 20.0))
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", 16000))
AUDIO_CHANNELS = int(os.getenv("AUDIO_CHANNELS", 1))
AUDIO_SAMPLE_WIDTH = int(os.getenv("AUDIO_SAMPLE_WIDTH", 2))
# ASR_ONLY_SERVER_PORT = int(os.getenv("ASR_ONLY_SERVER_PORT", 8099))
# AI_AGENT_SERVER_PORT = int(os.getenv("AI_AGENT_SERVER_PORT", 8095))
#
# AI_AGENT_BASE_URL = os.getenv("AI_AGENT_BASE_URL", "http://10.1.255.178:8088")
# AI_AGENT_APIKEY = os.getenv("AI_AGENT_APIKEY", "ragflow-8imAsxMODyEcJu31nQpnJ34BayDRFSgJAptIy5SU0u0")
# AI_AGENT_APP_ID = os.getenv("AI_AGENT_APP_ID", "d2a33c20393311f1a15ef59eeec78c8c")
# AI_AGENT_NEW_CONVERSATION_API = f"/api/v1/agents/{AI_AGENT_APP_ID}/sessions"
# AI_AGENT_CHAT_API = f"/api/v1/agents/{AI_AGENT_APP_ID}/completions"

