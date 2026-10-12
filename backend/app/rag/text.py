"""Tiny shared text helpers: tokenisation, light stemming, sentence splitting."""
import re

STOPWORDS = frozenset(
    """a an the and or but if of to in on at by for with from as is are was were be been being do does did
    i me my we our you your it its this that these those can could would should will shall may might
    what which who whom how when where why there here than then so not no yes about into over after before
    please tell want need have has had get got just any some also""".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
        w = w[:-1]  # shipp -> ship
    return w


def terms(text: str) -> list[str]:
    """Lower-cased, stop-word-free, lightly stemmed terms."""
    return [_stem(w) for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text) if s and s.strip()]
