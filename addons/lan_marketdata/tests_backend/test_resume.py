from .test_rolling import days
from addons.lan_marketdata.backend.runtime import task


def test_completed_imported_etf_warmup_is_not_redownloaded_on_first_view():
    sessions = days(70)
    end = sessions[-1] + " 15:00:00"
    master = {
        "588170.SH": dict(eligible=False, warm_count=256, latest5=end, live_attempt=end)
    }
    assert task(master, sessions, end, end, {"588170.SH"}) is None
    master["588170.SH"]["rebuild_pending"] = True
    assert task(master, sessions, end, end, {"588170.SH"})[2] == "WARM"
