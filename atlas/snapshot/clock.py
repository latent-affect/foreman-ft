"""An injectable clock so retention (section 7's age-AND-count rule) can be tested
deterministically without sleeping real wall-clock seconds. real_clock() is the default;
tests supply a FakeClock instance instead."""

import datetime


def real_clock():
    return datetime.datetime.now(datetime.timezone.utc)


class FakeClock:
    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + datetime.timedelta(seconds=seconds)
