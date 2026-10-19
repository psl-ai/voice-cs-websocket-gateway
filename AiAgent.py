import asyncio
import json
import logging
import re
import traceback
import httpx
from SnowflakeID import SnowflakeID
from config import *


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('AiAgentUtils')

headers = {
    'Content-Type': 'application/json'
}

class AiAgent:
    def __init__(self):
        self.app_id = AI_AGENT_APP_ID
        self.user_id = str(SnowflakeID(1).generate_id())
        self.conversation_id = None
        self.client = httpx.AsyncClient(timeout=30.0, verify=False)

    async def _new_conversation_id(self):
        try:
            headers = {
                "Authorization": f"Bearer {AI_AGENT_APIKEY}",
                'Accept': '*/*'
            }

            r = await self.client.post(AI_AGENT_BASE_URL+AI_AGENT_NEW_CONVERSATION_API+f"?user_id={self.user_id}", headers=headers, timeout=30.0)
            ret = r.json()

            if ret and ret.get("code") == 0 and ret.get("data"):
                self.conversation_id = ret.get("data").get("id")
                logging.error(ret)
                return True, json.loads(ret.get("data").get("message")[0]["content"])
            return False, None
        except:
            logging.error(traceback.format_exc())
            return False, None

    async def chat(self, message=""):
        try:
            #新对话，使用雪花id生成user_id, 调接口生成conversation_id
            if not self.conversation_id:
                ret, reply = await self._new_conversation_id()
                if not ret:
                    return False, None
                if not message:
                    return True, reply


            data = {
                "question": message,
                "stream": False,
                "session_id": self.conversation_id
            }
            headers = {
                "Authorization": f"Bearer {AI_AGENT_APIKEY}",
                'Accept': '*/*'
            }
            timeout = httpx.Timeout(30.0, connect=10.0)


            r = await self.client.post(AI_AGENT_BASE_URL + AI_AGENT_CHAT_API,
                                       json=data,
                                       headers=headers,
                                       timeout=timeout)
            ret = r.json()
            print("ret-----------")
            print(ret)
            if ret and ret.get("code") == 0:
                logging.error(ret)
                return True, json.loads(ret.get("data", {}).get("data", {}).get("content", "{}").replace("\n", ""))

            return False, None
        except:
            logging.error(traceback.format_exc())
            return False, None


async def my_async_function():
    agent = AiAgent()
    while True:
        msg = input("请输入问题，按enter发起：")
        success, reply = await agent.chat(msg)

        if not success or not reply:
            print(success, reply)
            break
        print(success, reply)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(my_async_function())
    loop.run_forever()


        # answer = json.loads(reply.get('answer'))
        # if answer:
        #     print(answer.get('txt'))
