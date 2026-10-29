from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.tooling.checks import expected_module, interface_errors
from agent.tooling.runtime import Runtime

PROMPT='Implement a module of an 8-bit adder.\nModule name:\n    TopModule\nInput ports:\n    a[7:0]: Operand A.'


class ModuleNameTests(unittest.TestCase):
    def test_explicit_name_wins_over_natural_language_description(self):
        self.assertEqual(expected_module(PROMPT),'TopModule')
        self.assertIsNone(expected_module('Implement a module of an 8-bit adder.'))
        self.assertEqual(interface_errors(PROMPT,'','module TopModule; endmodule'),([],0))
        self.assertIn('Expected module TopModule',interface_errors(PROMPT,'','module Other; endmodule')[0])

    def test_interface_declaration_and_named_prompt(self):
        self.assertEqual(expected_module(PROMPT,'module Required(input a); endmodule'),'Required')
        self.assertEqual(expected_module('Implement a module named Counter.'),'Counter')
        self.assertEqual(expected_module('Implement module TopModule with input a.'),'TopModule')

    def test_eda_top_uses_same_explicit_name(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(PROMPT,'',Path(directory),time.monotonic()+10,lambda *_a,**_k:None)
            runtime.dispatch({'tool':'write_file','args':{'path':'src/solution.sv','content':'module TopModule; endmodule'}})
            with patch.object(runtime.eda,'operation',return_value={'status':'passed','runs':[]}) as operation:
                self.assertEqual(runtime.dispatch({'tool':'compile','args':{'top':'TopModule'}})['status'],'passed')
                operation.assert_called_once()
            with self.assertRaisesRegex(ValueError,'top must match'):
                runtime.dispatch({'tool':'compile','args':{'top':'Other'}})

    def test_helper_module_ports_do_not_override_public_top(self):
        prompt='Implement module named TopModule.\n - input a (8 bits)\n - output q (8 bits)'
        code='module TopModule(input [7:0] a, output [7:0] q); assign q=a; endmodule\nmodule Helper(input a, output q); assign q=a; endmodule'
        self.assertEqual(interface_errors(prompt,'',code),([],2))
