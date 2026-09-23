import unittest
from backend import provider

class TranslationValidation(unittest.TestCase):
    def test_links_and_known_proper_names_can_remain_unchanged(self):
        terms=[{'source':'MonkeyOCR','target':'MonkeyOCR'},{'source':'MonkeyDoc','target':'MonkeyDoc'}]
        for text in ('Yuliang-Liu/MonkeyOCR .','4 MonkeyOCR','0.0 MonkeyOCR-3B Gemini2.0-flash Qwen2-VL-72B','MonkeyDoc | >10 | (cid:33)'):
            with self.subTest(text=text):
                self.assertEqual(provider.validate_translation({'text':text},text,terms),text)

    def test_english_prose_still_requires_translation(self):
        for text in ('The model reads papers.','precision/recall trade-offs are important.'):
            with self.subTest(text=text):
                with self.assertRaises(provider.ProviderError):
                    provider.validate_translation({'text':text},text,[])

    def test_proper_names_do_not_bypass_number_validation(self):
        with self.assertRaises(provider.ProviderError):
            provider.validate_translation({'text':'4 MonkeyOCR'},'5 MonkeyOCR',[{'source':'MonkeyOCR','target':'MonkeyOCR'}])

if __name__=='__main__':
    unittest.main()
