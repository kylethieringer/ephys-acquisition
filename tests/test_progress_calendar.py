import datetime as dt

import pandas as pd

from utils.progress_calendar import _month_labels, _weekday_grid, summarize_days


def _log(rows):
    return pd.DataFrame(rows, columns=["expt_id", "metadata_file", "keep", "targeted_cell_type", "genotype"])


def test_summarize_days_flags_kept_and_drops_test_ids():
    log = _log([
        ["fre001", "fre001/fre001_x_20260402_101010_metadata.json", "False", "dvmn", "ss1"],
        ["fre001", "fre001/fre001_x_20260402_111010_metadata.json", True, "dvmn", "ss1"],
        ["fre002", "fre002/fre002_x_20260403_101010_metadata.json", None, None, None],
        ["expt_xx", "expt_xx/expt_xx_x_20260404_101010_metadata.json", True, None, None],
    ])
    days = summarize_days(log)
    assert list(days.index) == [dt.date(2026, 4, 2), dt.date(2026, 4, 3)]
    assert days.loc[dt.date(2026, 4, 2), "n_recordings"] == 2
    assert days.loc[dt.date(2026, 4, 2), "n_kept"] == 1
    assert days.loc[dt.date(2026, 4, 3), "n_kept"] == 0
    assert days.loc[dt.date(2026, 4, 3), "cell_types"] == []


def test_weekday_grid_and_month_labels():
    weeks = _weekday_grid(dt.date(2026, 4, 2), dt.date(2026, 5, 6))
    assert weeks[0][0] == dt.date(2026, 3, 30)  # starts on the Monday
    assert all(len(w) == 5 for w in weeks)
    assert _month_labels(weeks) == [(0, "Apr"), (4, "May")]  # week of Apr 27 ends Fri May 1
