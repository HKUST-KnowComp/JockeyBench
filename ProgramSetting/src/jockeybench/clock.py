"""The two fixed times of a race day, in Japan Standard Time.

* ``cutoff(D)``  -- 21:00 on the evening before race day D. Every prediction for
  day D is made with evidence published by then.
* ``release(D)`` -- 20:30 on race day D. Finish results for day D become visible,
  and the daily reflection session may start.
"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def cutoff(day):
    return int((datetime.fromisoformat(day).replace(hour=21, tzinfo=JST) - timedelta(days=1)).timestamp())


def release(day):
    # Must precede the next day's 21:00 decision, including on consecutive race days.
    return int(datetime.fromisoformat(day).replace(hour=20, minute=30, tzinfo=JST).timestamp())
