import time
from openai import OpenAI
from config import *
import logging
import traceback

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('xxcq')


def xxcq(conf, kf_question, user_answer):
    try:
        now = time.strftime("%Y%m%d", time.localtime())
        rec_type = conf['rec_type']
        rec_desc = conf['rec_desc']
        rec_example = conf['rec_example']
        rec_regx = conf['rec_regx']

        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        sys_message = f"""### 信息抽取任务指令
你是一个专业的信息抽取系统。输入文本来自 ASR 语音识别，可能包含错字、断句、背景噪声幻觉、旁人说话和无意义口癖。请严格按照以下规范处理输入文本，只在证据充分且能通过规则校验时抽取结果。
"""+f"""
**已知信息:** 
   -当前日期:{now}
"""+"""

**抽取字段定义**
{{
  "字段名称": "<字段名称>",
  "字段描述": "<详细字段描述>",
  "正则表达式": "<精确匹配正则>",
  "验证规则": [
    "<规则1>",
    "<规则2>"
  ],
  "示例": ["<示例1>", "<示例2>"]
}}

**处理规则**
0.若是时间或日期问题，可以根据已知信息中的当前日期，推测, 如 用户输入昨天，则返回当前日期前一天的日期, 如果用户输入为上一周，则返回当前日期7天前的日期
1. 因为文本内容源自 ASR 语音识别，存在数字被中文数字或中文同音字表示，请先做 itn 处理，比如漆漆要转换成 77；也存在英文字母被中文近音字表示，比如欸要转换成 A，接要转换成 J。
2. 严格使用正则表达式进行匹配，抽取结果必须满足正则和字段描述。
3. 当文本中存在多个匹配时，取第一个完整匹配项。
4. 不要根据示例、字段描述或常识补全用户没有说出的字符。
5. 输入如果主要是噪声、寒暄、语气词、背景声、无关闲聊或残缺片段，必须抽取失败。
6. 对 ASR 识别文本做有限容错处理：
   - 数字 0-9 与汉字“零一二三四五六七八九”视为等价。
   - 字母大小写不敏感（W/w 视为相同）。
   - 忽略非关键分隔符（空格、逗号等）。

**输出规范**
请只输出 JSON 对象，不要包含任何解释或额外文本：
{{
  "rec_result": "<提取值>",
  "is_rec": 0或1,
  "confidence": 0.0到1.0之间的数字,
}}
其中 is_rec=0 表示成功，is_rec=1 表示失败；失败时 rec_result 必须为空字符串，confidence 不高于 0.4。"""+f"""

**当前任务配置**
字段名称: {rec_type}
字段描述: {rec_desc}
正则表达式: {rec_regx}
当前日期：{now}
示例: {str(rec_example)}
"""
        # print(sys_message)
        logger.error(f"kf_question ===> {kf_question}")
        logger.error(f"user_answer ===> {user_answer}")
        response = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": sys_message},
                {"role": "assistant", "content": kf_question},
                {"role": "user", "content": user_answer},
            ],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            stream=False
        )
        logger.error("大模型返回结果====>")
        logger.error(response.choices[0].message.content)

        # pattern = r'\{.*?"code":\d+,.*?"msg":".*?".*\}'
        return response.choices[0].message.content
    except:
        logger.error(traceback.format_exc())
        return "服务异常，请转人工坐席"

if __name__ == "__main__":
    s = time.time()
    conf = {
        "rec_type": "工号",
        "rec_desc": "以U开头5位数字的工号",
        "rec_example": ['U98812', 'U65432'],
        "rec_regx": 'U\d{5}',
    }
    data = xxcq(conf, '请问你的工号是多少？', '''我的工号是优四四三五三''')
    # info = json.loads(data)
    # print(f"intent_type ==> {info['items']}")
    print(time.time() - s)
