import unittest

from agent.loop.context import Conversation


class ThinkingHistoryTests(unittest.TestCase):
    def test_reasoning_survives_next_turn_with_tool_pairing(self):
        conversation = Conversation('test task', '', False)
        call = {'id':'call_1', 'type':'function',
                'function':{'name':'list_files', 'arguments':'{}'}}
        conversation.remember([({}, call)], [{'status':'ok'}],
                              assistant_message={'reasoning_content':'Check the interface first.'})
        assistant, tool = conversation.history[0]
        self.assertEqual(assistant['reasoning_content'], 'Check the interface first.')
        self.assertEqual(assistant['tool_calls'][0]['id'], tool['tool_call_id'])

    def test_reasoning_alias_is_normalized_without_duplication(self):
        conversation = Conversation('test task', '', False)
        conversation.remember([], [{'status':'ok'}],
                              assistant_message={'reasoning':'Plan', 'reasoning_content':'Plan'})
        self.assertEqual(conversation.history[0][0]['reasoning_content'], 'Plan')
        self.assertNotIn('reasoning', conversation.history[0][0])

    def test_history_limit_removes_reasoning_with_its_turn(self):
        conversation = Conversation('test task', '', False)
        for index in range(4):
            conversation.remember([], [{'status':'ok'}],
                                  assistant_message={'reasoning':str(index)})
        self.assertEqual([turn[0]['reasoning_content'] for turn in conversation.history],
                         ['1', '2', '3'])
