import unittest

from tracker.classify import classify, classify_topic, is_disclosed, mentions_nubra
from tracker.config import load_config

CFG = load_config()


class ClassifyTests(unittest.TestCase):
    def test_mentions_nubra(self):
        self.assertTrue(mentions_nubra("I use NUBRA for this", CFG["nubra_terms"]))
        self.assertTrue(mentions_nubra("see https://nubra.io/docs", CFG["nubra_terms"]))
        self.assertFalse(mentions_nubra("zerodha is fine", CFG["nubra_terms"]))

    def test_disclosure_phrases(self):
        phrases = CFG["disclosure_phrases"]
        self.assertTrue(is_disclosed("I use Nubra (I work at Nubra)", phrases))
        self.assertTrue(is_disclosed("I’m from   Nubra btw", phrases))  # curly apostrophe + spaces
        self.assertTrue(is_disclosed("Full disclosure: I'm on the Nubra team", phrases))
        self.assertFalse(is_disclosed("Nubra has a strategy builder", phrases))

    def test_custom_phrase_list_is_respected(self):
        self.assertTrue(is_disclosed("nubra employee here", ["nubra employee"]))

    def test_topics(self):
        topics = CFG["topics"]
        self.assertEqual(classify_topic("theta decay eats the premium near expiry", topics), "Options & Greeks")
        self.assertEqual(classify_topic("Zerodha vs Upstox, which broker?", topics), "Broker comparison")
        self.assertEqual(classify_topic("backtest it and paper trade first", topics), "Algo trading")
        self.assertEqual(classify_topic("the websocket api drops and the sdk errors", topics), "API & SDK help")
        self.assertEqual(classify_topic("I need historical data and ohlc candles", topics), "Market data")
        self.assertEqual(classify_topic("congrats on the wedding", topics), "Other")

    def test_whole_word_matching(self):
        # 'api' must not match inside 'capital', 'iv' not inside 'give'
        self.assertEqual(classify_topic("give it capital", {"API": ["api"], "Opt": ["iv"]}), "Other")

    def test_thread_title_fallback(self):
        cfg = dict(CFG, topic_uses_thread_title=True)
        self.assertEqual(classify("good luck!", cfg, "Best broker for options?")["topic"], "Options & Greeks")
        cfg["topic_uses_thread_title"] = False
        self.assertEqual(classify("good luck!", cfg, "Best broker?")["topic"], "Other")


if __name__ == "__main__":
    unittest.main()
