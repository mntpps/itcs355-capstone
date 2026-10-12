import numpy as np
import pandas as pd
import pytest

from src import forecast


class Constant:
    feature_names_in_ = np.array(["lag1", "x"])

    def __init__(self, value):
        self.value = value

    def predict(self, X):
        return np.full(len(X), self.value)


ROWS = pd.DataFrame({"lag1": [10.0, 20.0], "x": [0, 0]})


def test_delta_adds_yesterday():
    assert list(forecast.predict(Constant(2.0), ROWS, "delta")) == [12.0, 22.0]


def test_level_is_used_as_is():
    assert list(forecast.predict(Constant(2.0), ROWS, "level")) == [2.0, 2.0]


def test_never_negative():
    assert list(forecast.predict(Constant(-50.0), ROWS, "delta")) == [0.0, 0.0]


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        forecast.predict(Constant(0.0), ROWS, "levels")