from datetime import datetime

import pytest

from train_recommender import END, JULY, audit_training_rows, experiment, fit, profile_as_of


@pytest.mark.parametrize("order_date,return_date",[
    ("2026-10-01",None),
    ("2026-09-29","2026-10-02"),
])
def test_october_orders_and_outcomes_cannot_enter_training(order_date, return_date):
    row = {"order_date":datetime.fromisoformat(order_date),
           "return_requested_at":datetime.fromisoformat(return_date) if return_date else None}
    for train in (fit,experiment):
        with pytest.raises(ValueError,match="outside the allowed pre-October window"):
            # An October sentinel must fail before feature extraction or reading model labels.
            train([row],{},{},{})


def test_temporal_split_rejects_later_training_labels():
    row = {"order_date":datetime(2026,6,29),"return_requested_at":datetime(2026,7,1)}
    assert audit_training_rows([row],END)["count"] == 1
    with pytest.raises(ValueError):
        audit_training_rows([row],JULY)


def test_future_signup_not_used_as_fit_anchor():
    customers = {"future":{"signup_date":END,"usual_top":"L","usual_jeans":"34","dimensions":{"bust":95}}}
    assert profile_as_of(customers,"future",END) == {"usual_top":"","usual_jeans":"","dimensions":{}}
