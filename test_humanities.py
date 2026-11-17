import unittest
import humanities


class HumanitiesTests(unittest.TestCase):
    def test_local_public_domain_reference_is_bounded(self):
        self.assertEqual(len(humanities.chapters()), 4)
        self.assertTrue(all(len(text) > 3000 for _, text in humanities.chapters()))
        self.assertEqual(humanities.excerpt('解释一下这个 Python 报错'), '')
        passage = humanities.excerpt('《浮生六记》卷一里沈复怎样写芸？')
        self.assertIn('卷一', passage)
        self.assertIn('节选', passage)
        self.assertLess(len(passage), 600)


if __name__ == '__main__':
    unittest.main()
