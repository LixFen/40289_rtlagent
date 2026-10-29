import unittest

from agent.tooling.testbench import build_testbench, normalize_stimulus


class FragmentTests(unittest.TestCase):
    def test_initial_wrapper_becomes_block_without_changing_checks(self):
        stimulus='  initial begin : model_sequence\n int i;\n for(i=0;i<20;i++) begin\n check(q === 15, "boundary");\n end\nend'
        expected=stimulus.replace('  initial','',1)
        self.assertEqual(normalize_stimulus(stimulus),expected)
        tb=build_testbench('logic clk; wire [3:0] q;',stimulus,[{'id':'boundary'}])
        self.assertIn(expected,tb)
        self.assertNotIn('initial begin : model_sequence',tb)
        self.assertEqual(tb.count('check(q === 15, "boundary")'),1)

    def test_plain_stimulus_and_other_constructs_are_not_rewritten(self):
        for text in ('begin check(0,"x"); end','check(0,"x");','initial #5 check(0,"x");',
                     'initially = 1;', '// initial begin\ncheck(0,"x");'):
            self.assertEqual(normalize_stimulus(text),text)

    def test_fragments_cannot_skip_harness_completion_or_forge_coverage(self):
        for code in ('$finish;', '$stop;', '$display("RTL_SELFTEST_PASS token=%s checks=1",token);',
                     '$display("RTL_CHECK:reset");'):
            for declarations, stimulus in (('logic a;',code),('logic a; initial begin '+code+' end','check(1,"reset");')):
                with self.subTest(code=code, declarations=declarations), self.assertRaisesRegex(ValueError,'managed by the harness'):
                    build_testbench(declarations,stimulus,[{'id':'reset'}])

    def test_diagnostic_displays_and_protocol_comments_are_allowed(self):
        tb=build_testbench('logic a; // no $finish in fragments',
            '/* RTL_SELFTEST_PASS */ $display("expected=1 actual=%b",a); check(a === 1,"reset");',
            [{'id':'reset'}])
        self.assertIn('check(a === 1,"reset")',tb)
