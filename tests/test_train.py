"""Every hyperparameter accepted on the command line must reach the model.

Written after the study logged loss=absolute_error for 8 runs that actually trained with
squared error: the flag was parsed and logged, but never passed to the model.
"""
from argparse import Namespace

import numpy as np
import pandas as pd

from src import train

ROWS = pd.DataFrame({"lag1": np.arange(60.0), "x": np.arange(60.0) % 7,
                     "y": np.arange(60.0) + 1})


def test_every_cli_hyperparameter_reaches_the_model():
    args = Namespace(target_mode="level", loss="absolute_error", learning_rate=0.07,
                     max_iter=5, max_leaf_nodes=9, min_samples_leaf=3, l2=0.5)
    model = train.fit(ROWS, ["lag1", "x"], args, seed=1)
    assert model.loss == "absolute_error"
    assert model.learning_rate == 0.07
    assert model.max_iter == 5
    assert model.max_leaf_nodes == 9
    assert model.min_samples_leaf == 3
    assert model.l2_regularization == 0.5