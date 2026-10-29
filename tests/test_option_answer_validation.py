"""Shared single-answer predicate and its heuristic/structural boundaries."""
import unittest

from tests.lint_pack_fixtures import lp, mc


class OptionAnswerValidationTests(unittest.TestCase):
    def test_integer_boundaries_and_bool_rejection(self):
        for answer, expected in [(0, True), (1, True), (-1, False), (2, False),
                                 (True, False), (False, False), (None, False),
                                 (0.0, False), ('0', False), ([], False), ({}, False)]:
            with self.subTest(answer=answer):
                self.assertIs(lp.has_valid_option_answer(['a', 'b'], answer), expected)

    def test_empty_options_short_circuit(self):
        for options in [None, [], (), '', {}, 0, False]:
            with self.subTest(options=options):
                self.assertIs(lp.has_valid_option_answer(options, 0), False)

    def test_shape_is_still_owned_by_schema(self):
        # The old heuristics accept sized non-lists and singleton lists.
        for options in [('a', 'b'), 'ab', {0: 'a', 1: 'b'}, ['a'], [None, 3]]:
            with self.subTest(options=options):
                self.assertTrue(lp.has_valid_option_answer(options, 0))
        # Preserve short-circuit behavior for malformed, unsized input.
        self.assertFalse(lp.has_valid_option_answer(1, True))
        with self.assertRaises(TypeError):
            lp.has_valid_option_answer(1, 0)
        for options in ['ab', ('a', 'b'), {0: 'a'}, ['a'], None, 1]:
            with self.subTest(options=options):
                self.assertIn('`options` must be a list of ≥2 entries',
                              [f['detail'] for f in lp.check_l7_schema(mc(options=options))])

    def test_all_heuristics_skip_invalid_keys_and_empty_options(self):
        checks = [lp.check_l2_stem_echo, lp.check_l3_length_tell,
                  lp.check_l8_parenthetical, lp.check_l10_distractor_coverage,
                  lp.check_l21_low_priority]
        for options, answer in [(['a', 'b'], a) for a in [True, False, -1, 2, None, '0', 0.0]] + [
                (o, 0) for o in [None, [], '', {}, False]]:
            q = mc(options=options, answer=answer, diagram='a', diagram_alt='diagram')
            for check in checks:
                with self.subTest(check=check.__name__, options=options, answer=answer):
                    self.assertEqual(check(q), [])
            self.assertEqual(lp.check_l16_answer_position([q] * lp.L16_MIN_GROUP), [])

    def test_schema_rejects_boolean_key_without_changing_diagnostic(self):
        self.assertEqual(lp.check_l7_schema(mc(answer=True)), [{
            'rule': 'L7', 'severity': 'critical',
            'detail': '`answer` (True) is not a valid index into options[len=4]',
        }])

    def test_affected_findings_preserve_exact_output(self):
        question = mc(prompt='Which firewall protects this system?', options=['firewall', 'router'], answer=0)
        self.assertEqual(lp.check_l2_stem_echo(question),
                         [{'rule': 'L2',
                           'severity': 'critical',
                           'detail': "distinctive prompt noun 'firewall' appears only in the correct option"}])
        question = mc(options=['a' * 120, 'brief'], answer=0)
        self.assertEqual(lp.check_l3_length_tell(question),
                         [{'rule': 'L3',
                           'severity': 'critical',
                           'detail': 'correct option is 120 chars; longest distractor is 5 (>1.4× and >25 '
                                     'char gap)'}])
        question = mc(options=['network security boundary (network security boundary)', 'other'], answer=0)
        self.assertEqual(lp.check_l8_parenthetical(question),
                         [{'rule': 'L8',
                           'severity': 'critical',
                           'detail': 'correct option parenthetical shares 3 content words with its label '
                                     "(['boundary', 'network', 'security'])"}])
        question = mc()
        self.assertEqual(lp.check_l10_distractor_coverage(question),
                         [{'rule': 'L10',
                           'severity': 'critical',
                           'detail': 'explanation addresses none of the 3 distractor(s) and uses no contrast '
                                     'language; it justifies the correct answer but not why the others are '
                                     'wrong'}])
        question = mc(options=['firewall', 'router'], answer=0, diagram='firewall', diagram_alt='diagram')
        self.assertEqual(lp.check_l21_low_priority(question),
                         [{'rule': 'L21',
                           'severity': 'critical',
                           'detail': "diagram markup contains distinctive token(s) ['firewall'] of the "
                                     'correct option but none of the distractors — the diagram leaks the '
                                     'answer'}])
        question = mc(answer=0)
        self.assertEqual(lp.check_l16_answer_position([question] * lp.L16_MIN_GROUP),
                         [{'qid': None,
                           'rule': 'L16',
                           'severity': 'warning',
                           'detail': '5/5 (100%) of 4-option questions key slot 0; non-uniform '
                                     'answer-position distribution (advisory — the renderer shuffles at '
                                     'display time)'}])
