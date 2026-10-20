import threading
import unittest

from app.services.recording import intent, ir
from app.services.recording.session import RecordingError, RecordingSession


def _session(steps):
    s = RecordingSession.__new__(RecordingSession)
    s.id = 'test'
    s.status = 'stopped'
    s.base_url = 'http://x'
    s.error = None
    s._pick = False
    s._lock = threading.RLock()
    for i, step in enumerate(steps, 1):
        step['no'] = i
    s._steps = steps
    s._picks = []
    return s


def _step(kind, text):
    step = ir.make_step(kind, no=0, value=None, source='record')
    step['text'] = text
    return step


class StepRulesTest(unittest.TestCase):
    def _flow(self):
        return [_step('click', 'open'), _step('new_page', None), _step('click', 'next'), _step('click', 'end')]

    def test_removing_the_opener_also_removes_its_new_page_marker(self):
        s = _session(self._flow())
        s.remove_step(1)
        self.assertEqual([x['type'] for x in s._steps], ['click', 'click'])
        self.assertEqual([x['no'] for x in s._steps], [1, 2])

    def test_new_page_marker_cannot_be_removed_on_its_own(self):
        s = _session(self._flow())
        with self.assertRaises(RecordingError):
            s.remove_step(2)

    def test_insert_after_opener_lands_after_the_new_page(self):
        s = _session(self._flow())
        checked = intent.validate({'type': 'sleep', 'seconds': 1}, [])
        s.add_intent_step(checked, text='wait', after_no=1)
        self.assertEqual([x['type'] for x in s._steps], ['click', 'new_page', 'sleep', 'click', 'click'])

    def test_tied_step_cannot_be_moved(self):
        s = _session(self._flow())
        with self.assertRaises(RecordingError):
            s.move_step(1, 'down')
        with self.assertRaises(RecordingError):
            s.move_step(3, 'up')

    def test_untied_steps_swap_and_renumber(self):
        s = _session(self._flow())
        s.move_step(4, 'up')
        self.assertEqual([x['text'] for x in s._steps if x['type'] == 'click'], ['open', 'end', 'next'])
        self.assertEqual([x['no'] for x in s._steps], [1, 2, 3, 4])

    def test_move_at_the_edge_is_a_no_op(self):
        s = _session(self._flow())
        s.move_step(1, 'up')
        self.assertEqual(s._steps[0]['type'], 'click')


if __name__ == '__main__':
    unittest.main()
