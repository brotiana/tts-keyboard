import unittest
from dictee.commands import is_finish_word, finish_command


class CommandTests(unittest.TestCase):
    def test_accents_case_and_one_edit_are_accepted(self):
        for word in ('terminé', 'TERMINE', 'terminer', 'terminée', 'termine\u0301',
                     'termihe', 'terine'):
            with self.subTest(word=word):
                self.assertTrue(is_finish_word(word))

    def test_more_than_fifteen_percent_is_rejected(self):
        for word in ('interminer', 'terminologie', 'bonjour', 'termi', 'te', ''):
            with self.subTest(word=word):
                self.assertFalse(is_finish_word(word))

    def test_only_final_word_is_command(self):
        self.assertIsNone(finish_command('Il faut terminer mon travail.'))
        value='Bonjour Tin, terminer !'
        match=finish_command(value)
        self.assertIsNotNone(match)
        self.assertEqual(value[:match.start()], 'Bonjour Tin,')
