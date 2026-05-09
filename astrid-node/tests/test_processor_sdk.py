import unittest

from astrid_node import Processor
from astrid_node.processor import ProcessorError


class MyProcessor(Processor):
    inputs = ["aave.raw.account_events.v1"]

    def handle(self, msg, ctx):
        ctx.insert("astrid_user.my_scores", {"user": msg["user"], "score": 1})


class BadProcessor(Processor):
    def handle(self, msg, ctx):
        ctx.insert("astrid_aave.account_profiles", {"user": msg["user"]})


class ProcessorSdkTests(unittest.TestCase):
    def test_fixture_replay_is_deterministic(self):
        messages = [{"user": "0xabc"}, {"user": "0xdef"}]
        first = MyProcessor.run_fixture(messages)
        second = MyProcessor.run_fixture(messages)
        self.assertEqual(first.writes, second.writes)
        self.assertEqual(len(first.writes), 2)

    def test_envelope_rows_are_processed_as_messages(self):
        envelope = {"rows": [{"user": "0xabc"}, {"user": "0xdef"}]}
        result = MyProcessor.run_fixture([envelope])
        self.assertEqual(len(result.writes), 2)

    def test_processor_outputs_are_restricted_to_user_namespace(self):
        with self.assertRaises(ProcessorError):
            BadProcessor.run_fixture([{"user": "0xabc"}])


if __name__ == "__main__":
    unittest.main()
