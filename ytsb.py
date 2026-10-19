import time
import json
from openai import OpenAI
from config import *
import logging
import traceback

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ytsb')


def ytsb(class_info, kf_question, user_answer):
    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

        examples = "\n".join(["   - "+ f"{i['category']}：{i['desc']}，{'比如'+ '/'.join(i.get('examples')) if i.get('examples') else ''}" for i in class_info])
        categories = [i['category'] for i in class_info]
        categories.append('其他')
        system_message = """# 角色设定
你是一位专业的智能客服用户意图分析专家。用户输入来自 ASR 语音识别，可能包含错字、断句、背景人声、环境噪声幻觉和无意义口癖。你的任务是尽力识别用户真实的意图类别，若用户回答内容有和assistant提问有关联的词汇即为当前类别。

## 可选意图
"""+examples+"""
   - 其他：如果不是以上类型之一，或文本噪声较大、证据不足、只有寒暄/语气词/背景声，则 intent_type 必须为其他

## 噪声处理原则
1. 输入如果主要是“嗯/啊/喂/好的/谢谢”等寒暄或语气词，判为其他，confidence 不高于 0.4。
2. 输入如果像背景音、旁人说话、广告字幕、播放器幻觉、无关闲聊或残缺片段，判为其他，confidence 不高于 0.4。
3. ASR 错字可以适度容错，但必须能从文本中找到明确业务诉求；不能只根据个别相似词猜测。
4. 当多个意图都可能时，选择证据最直接的一个；如果仍不确定，判为其他。

## 输出要求
请只输出 JSON 对象，不要包含解释性前后缀：
{
  "intent_type": "选择的意图类别或其他",
  "confidence": 0.0到1.0之间的数字
}

**intent_type** 的取值范围只能是 {categories} 中的一个，请保证严格一致

## 示例
用户输入："嗯嗯啊谢谢观看"
输出：
{
  "intent_type": "其他",
  "confidence": 0.2
}"""
        # logger.info(system_message)
        logger.info(f"kf_question=====>{kf_question}")
        logger.info(f"user_answer=====>{user_answer}")
        response = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": system_message}, 
                {"role": "assistant", "content": kf_question},
                {"role": "user", "content": user_answer},
            ],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            stream=False
        )
        logger.info("-------------意图识别结果-----------------")
        logger.info(response.choices[0].message.content)
        logger.info("-------------意图识别结果-----------------")
        return response.choices[0].message.content
    except:
        logger.error(traceback.format_exc())
        return "服务异常，请转人工坐席"

if __name__ == "__main__":
    s = time.time()
    classification_info = [
        {
            "category":"气象查询",
            "desc": "用户查询天气相关的信息",
            "examples": ["今天天气如何", "明天会下雨吗", "明天出门要带伞吗"]
        },
        {
            "category":"公共业务",
            "desc": "用户查询个人政务相关业务",
            "examples": ["公积金提取", "社保查询", "档案查询"]
        },
        {
            "category":"宽带查询",
            "desc": "用户查询个人宽带及网络相关信息",
            "examples": ["不能上网", "我上网好卡啊", "我的宽带过期了怎么缴费", "不能访问百度"]
        },

    ]
    # data = ytsb(classification_info, '''我的电脑访问不了www.bing.com？''')
    data = ytsb(classification_info, '请问你有什么问题？', '''西瓜多少钱一斤''')
    logger.info(data)
    info = json.loads(data)
    logger.info(f"intent_type ==> {info['intent_type']}")
    logger.info(time.time() - s)

