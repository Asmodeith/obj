
import re

TG_TAG_PATTERN = re.compile(r'@[\w\d_]{4,32}')
TG_LINK_PATTERN = re.compile(r'(https?://)?t\.me/([\w\d_]{4,32})', re.IGNORECASE)

def replace_contact_tags(text: str, new_tag: str) -> str:

    uname = new_tag.lstrip('@')
    at_form = f"@{uname}"
    link_form = f"t.me/{uname}"


    text = TG_TAG_PATTERN.sub(at_form, text)
    text = TG_LINK_PATTERN.sub(link_form, text)
    return text

def replace_link_placeholder(text: str, link: str) -> str:

    pattern = re.compile(r"\*(ссылка|link)\*", re.IGNORECASE)
    return pattern.sub(link, text or "")
