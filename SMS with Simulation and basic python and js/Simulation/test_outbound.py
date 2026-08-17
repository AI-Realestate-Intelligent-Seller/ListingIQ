import unittest

from Simulation.outbound import fit_recipient_sms, recipient_style


class RecipientPolicyTests(unittest.TestCase):
    def test_styles_vary_by_phone_number(self):
        styles = {recipient_style(f"+1312555012{digit}")["name"] for digit in range(5)}
        self.assertEqual(len(styles), 5)

    def test_invented_email_is_removed(self):
        self.assertNotIn("@", fit_recipient_sms("My email is owner@example.com. Send it there."))

    def test_multiple_questions_are_reduced_to_one(self):
        reply = fit_recipient_sms("Who are you? How did you get this? What do you want?")
        self.assertEqual(reply.count("?"), 1)

    def test_long_reply_does_not_end_mid_word(self):
        reply = fit_recipient_sms("word " * 80, 80)
        self.assertLessEqual(len(reply), 80)
        self.assertTrue(reply.endswith("…"))


if __name__ == "__main__":
    unittest.main()
