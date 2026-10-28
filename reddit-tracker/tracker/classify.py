"""Keyword-based classification. All rules come from config.json."""
import re

OTHER = "Other"


def _normalise(text):
    text = (text or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text)


def _word_pattern(keyword):
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])")


def mentions_nubra(text, terms):
    t = _normalise(text)
    return any(term.lower() in t for term in terms)


def is_disclosed(text, phrases):
    t = _normalise(text)
    return any(_normalise(p) in t for p in phrases)


def topic_scores(text, topics):
    t = _normalise(text)
    return {name: sum(len(_word_pattern(k).findall(t)) for k in keywords)
            for name, keywords in topics.items()}


def classify_topic(text, topics, fallback_text=None):
    """Topic with the most keyword hits; ties go to the topic listed first."""
    for candidate in (text, fallback_text):
        if not candidate:
            continue
        scores = topic_scores(candidate, topics)
        best = max(scores.values(), default=0)
        if best > 0:
            return next(name for name, s in scores.items() if s == best)
    return OTHER


def _hits(text, keywords):
    t = _normalise(text)
    return sum(len(_word_pattern(k).findall(t)) for k in keywords)


def brands_mentioned(text, brands):
    """Set of brand names (keys of `brands`) mentioned at least once in text."""
    t = _normalise(text)
    return {name for name, words in brands.items()
            if any(_word_pattern(w).search(t) for w in words)}


def all_brands(cfg):
    """Nubra plus competitors, as {brand: [words]} (nubra.io is matched via 'nubra')."""
    return {"Nubra": cfg.get("nubra_terms", ["nubra"]), **cfg.get("competitors", {})}


def sentiment(text, cfg):
    """'positive', 'negative' or 'neutral' from keyword hits."""
    lists = cfg.get("sentiment", {})
    score = _hits(text, lists.get("positive", [])) - _hits(text, lists.get("negative", []))
    return "positive" if score > 0 else "negative" if score < 0 else "neutral"


def classify(text, cfg, thread_title=None):
    fallback = thread_title if cfg.get("topic_uses_thread_title") else None
    return {
        "mentions_nubra": mentions_nubra(text, cfg.get("nubra_terms", ["nubra"])),
        "disclosed": is_disclosed(text, cfg.get("disclosure_phrases", [])),
        "topic": classify_topic(text, cfg.get("topics", {}), fallback),
    }
