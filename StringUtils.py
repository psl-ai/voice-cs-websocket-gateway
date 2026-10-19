import string


def remove_leading_punctuation(text):
    # 定义要去除的字符集合
    punctuation_set = set(
        string.punctuation +  # 英文标点符号
        '，。、；：‘’“”【】（）《》！？…—' +  # 中文标点符号
        ' \u3000'  # 半角空格 + 全角空格
    )

    # 遍历字符串直到找到第一个非标点/非空格字符
    for i, char in enumerate(text):
        if char not in punctuation_set:
            return text[i:]

    # 如果全部字符都是标点/空格，返回空字符串
    return ''