from app.parcel.consistency import apply_batch_checks


def rec(awb, status="SUCCESS"):
    return {"awb": awb, "status": status, "notes": "", "needs_review": 0}


def test_odd_length_and_rare_prefix_downgraded_but_not_changed():
    rows = [rec(f"19106721{i:06d}") for i in range(10)] + [rec("1910672176464271"), rec("16106721000001")]
    rows += [rec(f"31049517{i:06d}") for i in range(8)]
    n = apply_batch_checks(rows)
    odd = next(r for r in rows if r["awb"] == "1910672176464271")
    assert odd["status"] == "LOW_CONFIDENCE" and "digits" in odd["notes"] and odd["awb"] == "1910672176464271"
    assert all(r["status"] == "SUCCESS" for r in rows[:10]) and n >= 1


def test_small_batches_untouched():
    rows = [rec("19106721000001"), rec("1910672100000")]
    assert apply_batch_checks(rows) == 0
