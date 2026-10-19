"""Pure helpers for bill drop pre-fill line description (U-676h)."""
from __future__ import annotations

import re

FORMAT_SHORT_DESCRIPTION_MAX_LEN = 60

_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:!?\-–—…]+(?:\.{2,})?$")
_LEADING_CAP_SKIP = frozenset("\"'([{<")
_KNOWN_ABBREV_WORDS = frozenset({"etc.", "inc.", "co.", "ltd."})
_TRAILING_FILLER_WORDS = frozenset({"purchase", "purchases"})


def _strip_trailing_filler_words(words: list[str]) -> list[str]:
    if len(words) <= 1:
        return words
    trimmed = list(words)
    while len(trimmed) > 1:
        tail = trimmed[-1].lower().strip(".,;:!?")
        if tail not in _TRAILING_FILLER_WORDS:
            break
        trimmed.pop()
    return trimmed


def truncate_at_word_boundary(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    chunk = text[:max_len]
    last_space = chunk.rfind(" ")
    if last_space > 0:
        return chunk[:last_space].rstrip()
    return chunk.rstrip()


def _title_first_character(word: str, index: int) -> str:
    ch = word[index]
    upper = ch.upper()
    if len(upper) == 1:
        return word[:index] + upper + word[index + 1 :]
    return word


def _capitalize_word(word: str) -> str:
    if not word:
        return word
    i = 0
    while i < len(word) and word[i] in _LEADING_CAP_SKIP:
        i += 1
    if i >= len(word):
        return word
    return _title_first_character(word, i)


def _strip_trailing_punct(text: str) -> str:
    text = text.rstrip()
    if not text:
        return text
    last_word = text.rsplit(" ", 1)[-1]
    keep_final_dot = last_word.lower() in _KNOWN_ABBREV_WORDS
    while text:
        if text.endswith("…"):
            text = text[:-1].rstrip()
            continue
        if text.endswith("..."):
            text = text[:-3].rstrip()
            continue
        match = re.search(r"[\s.,;:!?\-–—]+$", text)
        if not match:
            break
        suffix = match.group()
        if suffix == "." and keep_final_dot:
            break
        text = text[: match.start()].rstrip()
    return text


def format_short_description(text: str | None) -> str | None:
    """Collapse whitespace, first 5 words, Title Case, max 60 chars, no trailing punct."""
    if text is None:
        return None
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return None
    collapsed = _strip_trailing_punct(collapsed)
    if not collapsed:
        return None
    words = _strip_trailing_filler_words(collapsed.split(" "))
    words = words[:5]
    titled = [_capitalize_word(word) for word in words if word]
    if not titled:
        return None
    result = " ".join(titled)
    result = _strip_trailing_punct(result)
    if not result:
        return None
    if len(result) > FORMAT_SHORT_DESCRIPTION_MAX_LEN:
        result = truncate_at_word_boundary(result, FORMAT_SHORT_DESCRIPTION_MAX_LEN)
        if len(result) > FORMAT_SHORT_DESCRIPTION_MAX_LEN:
            result = result[:FORMAT_SHORT_DESCRIPTION_MAX_LEN].rstrip()
        result = _strip_trailing_punct(result)
        post_cut_words = _strip_trailing_filler_words(result.split(" "))
        result = " ".join(post_cut_words)
        result = _strip_trailing_punct(result)
    return result or None
