import re


def slugify(text: str) -> str:
    # 转小写
    text = text.lower()
    # 将非字母数字字符替换为连字符
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # 去掉首尾连字符
    text = text.strip('-')
    return text
